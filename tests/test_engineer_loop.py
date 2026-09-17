"""The engineer loop: refuses rather than accepts, and keeps nothing that failed.

The model and the external tools are scripted; git is real, because what the
loop leaves behind in the game repository is the part that must be right.
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from app.engineer.gate import Gate
from app.engineer.gemini import GeminiRefused, GeminiUnavailable
from app.engineer.loop import EngineerLoop
from app.engineer.runs import (
    EngineerRuns,
    NoDesign,
    load_design,
    run_name,
    usage_recorder,
)
from app.engineer.schemas import EngineeringTask
from app.engineer.workspace import SERVICES_PATH, Worktree
from app.models import AuditRecord, Candidate, ResearchRun, SystemState

KNOWN = frozenset({"Players", "ReplicatedStorage", "DataStoreService"})
DESIGN = {"proposal": {"concept_title": "Obby Tycoon", "core_loop": "Collect coins, buy upgrades, <b>climb</b>.",
                       "essential_features": ["Coins persist between sessions"]}}
TASK = EngineeringTask(audit_id="audit-1", system="DataService", goal="Persist each player's coins safely.",
                       acceptance_criteria=["Coins survive rejoining", "Failed loads never overwrite data"])

GOOD = "--!strict\nlocal Services = require(script.Parent.Parent.shared.Services)\nreturn {}\n"
HALLUCINATED = "--!strict\nlocal Services = require(script.Parent.Parent.shared.Services)\nServices.Players:MadeUp()\nreturn {}\n"


def answer(content=GOOD, path="src/server/DataService.luau", services=("Players", "DataStoreService")):
    return json.dumps({"files": [{"path": path, "content": content}], "services": list(services),
                       "summary": "Session-locked coin persistence"})


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "game"
    (repo / "src" / "shared").mkdir(parents=True)
    git("init", "-q", "-b", "master", cwd=repo)
    (repo / "default.project.json").write_text('{"name": "game", "tree": {}}\n')
    (repo / "src" / "shared" / "Util.luau").write_bytes(b"--!strict\nreturn {}\n")
    git("add", "-A", cwd=repo)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base", cwd=repo)
    return repo


class ScriptedModel:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def __call__(self, system, prompt, deadline):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, "gemini-test (key1)"


def luau_lsp_runner(args, cwd, timeout):
    """luau-lsp's verdict: a hallucinated member is a type error, as on the real toolchain."""
    if args[0] == "luau-lsp":
        for path in sorted((cwd / "src").rglob("*.luau")):
            member = re.search(r"MadeUp\w*", path.read_text())
            if member:
                return 1, (f"{path.relative_to(cwd).as_posix()}(3,17): TypeError: "
                           f"Key '{member.group()}' not found in external type 'Players'")
    return 0, ""


def make_loop(repo, tmp_path, model, *, runner=luau_lsp_runner, max_attempts=4, events=None):
    definitions = tmp_path / "defs.d.luau"
    definitions.write_text("")
    gate = Gate(KNOWN, definitions, runner=runner, mode="builtin")
    return EngineerLoop(
        model=model, gate=gate, known_services=KNOWN,
        create_worktree=lambda name: Worktree.create(repo, "master", tmp_path / "worktrees", name),
        handoff_dir=tmp_path / "handoffs", max_attempts=max_attempts, run_seconds=3600,
        on_event=(lambda stage, detail="", **extra: events.append((stage, detail, extra))) if events is not None else None,
    )


def branches(repo):
    return git("branch", "--list", "--format=%(refname:short)", cwd=repo).split()


async def test_a_refused_attempt_teaches_the_next_one_and_only_passing_code_is_committed(repo, tmp_path):
    model = ScriptedModel(answer(HALLUCINATED), answer(GOOD))
    events: list = []
    result = await make_loop(repo, tmp_path, model, events=events).run("data-service-00000001", TASK, DESIGN)

    assert (result.status, result.attempts, result.branch) == ("complete", 2, "engineer/data-service-00000001")
    # The second prompt carries the first attempt's exact type error.
    assert "Key 'MadeUp' not found" in model.prompts[1] and "previous_attempt" in model.prompts[1]
    committed = git("show", f"{result.commit}:src/server/DataService.luau", cwd=repo)
    assert "MadeUp" not in committed
    services = git("show", f"{result.commit}:{SERVICES_PATH}", cwd=repo)
    assert 'DataStoreService = game:GetService("DataStoreService") :: DataStoreService' in services
    assert git("log", "--format=%s", "master", cwd=repo) == "base"
    assert not (tmp_path / "worktrees" / "data-service-00000001").exists()
    stages = [stage for stage, _, _ in events]
    assert stages[:3] == ["worktree", "preflight_passed", "attempt"] and stages[-1] == "committed"


async def test_files_from_a_refused_attempt_do_not_survive_into_the_next(repo, tmp_path):
    first = json.dumps({"files": [{"path": "src/server/DataService.luau", "content": GOOD},
                                  {"path": "src/server/Leftover.luau", "content": HALLUCINATED}],
                        "services": ["Players"]})
    model = ScriptedModel(first, answer(GOOD))
    result = await make_loop(repo, tmp_path, model).run("data-service-0000000b", TASK, DESIGN)
    assert (result.status, result.attempts) == ("complete", 2)
    tree = git("ls-tree", "-r", "--name-only", result.commit, cwd=repo)
    assert "src/server/Leftover.luau" not in tree


async def test_design_text_is_fenced_as_data(repo, tmp_path):
    model = ScriptedModel(answer())
    await make_loop(repo, tmp_path, model).run("data-service-00000002", TASK, DESIGN)
    prompt = model.prompts[0]
    assert "Collect coins, buy upgrades, b climb /b ." in prompt
    assert "Coins survive rejoining" in prompt and '"name": "game"' in prompt
    assert "--- src/shared/Util.luau ---" in prompt


async def test_exhausted_attempts_keep_the_refused_attempt_as_evidence_and_hand_off(repo, tmp_path):
    model = ScriptedModel(*[answer(HALLUCINATED.replace("MadeUp", f"MadeUp{letter}")) for letter in "ABC"])
    result = await make_loop(repo, tmp_path, model, max_attempts=3).run("data-service-00000003", TASK, DESIGN)

    assert (result.status, result.attempts, result.reason) == ("blocked", 3, "All 3 attempts were refused")
    # docs/GATING.md: a branch that cannot go green is left in place, marked.
    assert result.branch == "engineer/data-service-00000003"
    assert sorted(branches(repo)) == ["engineer/data-service-00000003", "master"]
    message = git("log", "-1", "--format=%B", result.commit, cwd=repo)
    assert message.startswith("refused(DataService): attempt 3 failed luau-lsp") and "Do not merge" in message
    assert "MadeUpC" in git("show", f"{result.commit}:src/server/DataService.luau", cwd=repo)
    assert git("log", "--format=%s", "master", cwd=repo) == "base"
    assert not (tmp_path / "worktrees" / "data-service-00000003").exists()

    handoff = (tmp_path / "handoffs" / "data-service-00000003.md").read_text()
    assert "Key 'MadeUpC' not found" in handoff and "Persist each player's coins safely." in handoff
    assert "HARD RULES" in handoff and "engineer/data-service-00000003" in handoff and "verify.ps1" in handoff


async def test_the_same_failure_three_times_stops_early(repo, tmp_path):
    model = ScriptedModel(*[answer(HALLUCINATED)] * 6)
    result = await make_loop(repo, tmp_path, model, max_attempts=6).run("data-service-00000004", TASK, DESIGN)
    assert (result.status, result.attempts) == ("blocked", 3)
    assert "three attempts running" in result.reason and len(model.replies) == 3


@pytest.mark.parametrize("bad, expected", [
    ("not json at all", "not the required JSON"),
    (answer(path="../../escape.luau"), "'..' path segments"),
    (answer(path="src/client/Controller.luau"), "src/server, src/shared"),
    (answer(path=SERVICES_PATH), "Services module is generated"),
    (answer(services=("Players", "MadeUpService")), "not Roblox services: MadeUpService"),
])
async def test_answers_refused_before_the_gate_never_touch_the_worktree(repo, tmp_path, bad, expected):
    runner_calls = []

    def runner(args, cwd, timeout):
        runner_calls.append(args[0])
        return luau_lsp_runner(args, cwd, timeout)

    model = ScriptedModel(bad, answer())
    result = await make_loop(repo, tmp_path, model, runner=runner).run("data-service-00000005", TASK, DESIGN)
    assert result.status == "complete" and result.attempts == 2
    assert expected in model.prompts[1]
    # One preflight gate run and one real attempt: the refused answer never reached the tools.
    assert runner_calls.count("luau-lsp") == 2
    assert not (repo.parent / "escape.luau").exists()


async def test_markdown_fenced_json_is_accepted(repo, tmp_path):
    model = ScriptedModel("```json\n" + answer() + "\n```")
    result = await make_loop(repo, tmp_path, model).run("data-service-00000006", TASK, DESIGN)
    assert result.status == "complete"


async def test_a_run_that_never_wrote_code_leaves_no_branch(repo, tmp_path):
    model = ScriptedModel(*["not json"] * 3)
    result = await make_loop(repo, tmp_path, model, max_attempts=3).run("data-service-0000000c", TASK, DESIGN)
    assert (result.status, result.branch, result.commit) == ("blocked", None, None)
    assert branches(repo) == ["master"]


async def test_a_refusal_after_a_checked_attempt_keeps_the_checked_one(repo, tmp_path):
    model = ScriptedModel(answer(HALLUCINATED), "not json")
    result = await make_loop(repo, tmp_path, model, max_attempts=2).run("data-service-0000000d", TASK, DESIGN)
    assert result.status == "blocked" and result.commit is not None
    assert "attempt 1 failed" in git("log", "-1", "--format=%s", result.commit, cwd=repo)


async def test_a_base_that_already_fails_is_refused_before_any_model_call(repo, tmp_path):
    (repo / "src" / "shared" / "Util.luau").write_bytes(b"print(game.Name)\n")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "broken", cwd=repo)
    model = ScriptedModel()
    result = await make_loop(repo, tmp_path, model).run("data-service-00000007", TASK, DESIGN)
    assert (result.status, result.attempts, result.planned) == ("blocked", 0, False)
    assert "fix the base branch first" in result.reason and branches(repo) == ["master"]


async def test_gemini_unavailable_stops_as_planned_and_refused_as_unplanned(repo, tmp_path):
    result = await make_loop(repo, tmp_path, ScriptedModel(GeminiUnavailable("both keys limited"))).run(
        "data-service-00000008", TASK, DESIGN)
    assert (result.status, result.planned) == ("blocked", True)
    result = await make_loop(repo, tmp_path, ScriptedModel(GeminiRefused("model not found"))).run(
        "data-service-00000009", TASK, DESIGN)
    assert (result.status, result.planned) == ("blocked", False)
    assert branches(repo) == ["master"]


# ---- runs: design source, records, usage ----------------------------------

def seed_audit(factory, payload):
    with factory() as db:
        run = ResearchRun(niche="obby")
        db.add(run)
        db.flush()
        candidate = Candidate(run_id=run.id, external_id="77")
        db.add(candidate)
        db.flush()
        audit = AuditRecord(candidate_id=candidate.id, payload=payload)
        db.add(audit)
        db.commit()
        return audit.id


def test_the_engineer_only_builds_audited_designs(session_factory):
    with pytest.raises(NoDesign, match="only builds audited designs"):
        load_design(session_factory, "no-such-audit")
    blocked = seed_audit(session_factory, {"proposal": None, "risks": ["gates failed"]})
    with pytest.raises(NoDesign, match="produced no design"):
        load_design(session_factory, blocked)
    designed = seed_audit(session_factory, DESIGN)
    assert load_design(session_factory, designed)["proposal"]["concept_title"] == "Obby Tycoon"


def test_run_records_events_without_secrets(session_factory, settings):
    settings.gemini_api_key_1 = "SYNTHETIC_GEMINI_LEAK_999"
    runs = EngineerRuns(session_factory)
    runs.create("data-service-0000000a", TASK)
    runs.event("data-service-0000000a", "gate", "failed with SYNTHETIC_GEMINI_LEAK_999",
               checks=[{"check": "selene", "passed": False, "exit_code": 1, "output": "SYNTHETIC_GEMINI_LEAK_999"}])
    stored = json.dumps(runs.get("data-service-0000000a"))
    assert "SYNTHETIC_GEMINI_LEAK_999" not in stored and "[REDACTED]" in stored


def test_usage_is_counted_per_key(session_factory):
    record = usage_recorder(session_factory)
    record("key1", "gemini-3.1-pro-preview", "ok", {"total_tokens": 100})
    record("key1", "gemini-3.1-pro-preview", "rate_limited", {})
    record("key2", "gemini-3.1-pro-preview", "ok", {"total_tokens": 50})
    with session_factory() as db:
        [row] = db.query(SystemState).filter(SystemState.key.like("gemini-usage:%")).all()
    assert row.value_json["key1"]["calls"] == 2 and row.value_json["key1"]["rate_limited"] == 1
    assert row.value_json["key1"]["total_tokens"] == 100 and row.value_json["key2"]["total_tokens"] == 50


def test_run_names_are_worktree_safe():
    name = run_name("DataService")
    assert name.startswith("data-service-") and len(name) == len("data-service-") + 8
