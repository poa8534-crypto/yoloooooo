"""A system is not accepted until something runs it.

Twelve systems reached island-haven's master having passed six checks, five of
which only read the code and one of which ran whatever specs existed -- and
none existed for them, because `tests/` was outside the writable roots and
every attempt to write one was refused as an unsafe path. So the behaviour
check passed by measuring nothing.

These tests hold both halves shut: the Engineer may write its own spec and
nothing else under tests/, and the gate refuses a system that arrives without
one, or with one that never loads the module it names.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.engineer.gate import Gate
from app.engineer.workspace import UnsafePath, spec_path, validate_path


def gate() -> Gate:
    return Gate(known_services=frozenset({"Players"}), definitions=Path("nonexistent.d.luau"),
                runner=lambda args, cwd, timeout: (0, "ok"), mode="verify-script")


def project(tmp_path: Path, *, source: str = "src/server/WidgetService.luau",
            spec: str | None = None) -> Path:
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scripts/verify.ps1").write_text("exit 0", encoding="utf-8")
    written = tmp_path / source
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text("--!strict\nreturn {}\n", encoding="utf-8")
    if spec is not None:
        (tmp_path / "tests").mkdir(exist_ok=True)
        (tmp_path / "tests/WidgetService.spec.luau").write_text(spec, encoding="utf-8")
    return tmp_path


def find(report, fragment: str):
    return next(check for check in report.checks if fragment in check.name)


def test_the_engineer_may_write_its_own_spec():
    assert validate_path("tests/WidgetService.spec.luau") == "tests/WidgetService.spec.luau"
    assert spec_path("WidgetService") == "tests/WidgetService.spec.luau"


@pytest.mark.parametrize("path", ["tests/harness.luau", "tests/runner.luau",
                                  "tests/nested/A.spec.luau", "tests/notes.txt",
                                  "tests/WidgetService.luau"])
def test_nothing_else_under_tests_may_be_written(path):
    """The harness and the runner decide what passing means. A model that can
    rewrite them can pass by lowering the bar."""
    with pytest.raises(UnsafePath):
        validate_path(path)


def test_a_system_without_a_spec_is_refused(tmp_path):
    report = gate().run(project(tmp_path), "WidgetService")
    assert not report.passed
    check = find(report, "behaviour spec")
    assert not check.passed and "tests/WidgetService.spec.luau is missing" in check.output


def test_a_spec_that_never_loads_the_module_is_refused(tmp_path):
    """A file called a spec that does not run the system is the same silence
    with a file around it."""
    root = project(tmp_path, spec='return function(harness)\n\treturn { ["nothing"] = function() end }\nend\n')
    check = find(gate().run(root, "WidgetService"), "behaviour spec")
    assert not check.passed and "never loads src/server/WidgetService.luau" in check.output


def test_a_spec_that_loads_the_module_passes(tmp_path):
    root = project(tmp_path, spec='return function(harness)\n'
                                  '\tlocal s = harness.load("src/server/WidgetService.luau")\n'
                                  '\treturn { ["it works"] = function() assert(s) end }\nend\n')
    report = gate().run(root, "WidgetService")
    assert find(report, "behaviour spec").passed and report.passed


def test_the_client_is_asked_for_its_own_path_not_the_servers(tmp_path):
    """Where the source actually is, measured from the tree: a view under
    src/client must not be told to name a path under src/server."""
    root = project(tmp_path, source="src/client/WidgetService.luau")
    check = find(gate().run(root, "WidgetService"), "behaviour spec")
    assert "src/client/WidgetService.luau" in check.output
    assert "src/server/WidgetService.luau" not in check.output


def test_the_preflight_run_asks_for_no_spec(tmp_path):
    """Preflight judges the untouched project, which cannot hold a spec for a
    system nobody has written yet."""
    report = gate().run(project(tmp_path))
    assert report.passed
    assert not any("behaviour spec" in check.name for check in report.checks)


def spec_task(**overrides):
    from app.engineer.schemas import EngineeringTask

    base = dict(audit_id="audit-1", system="WidgetService", goal="Prove the widget behaves.",
                acceptance_criteria=["A second claim gives nothing"], deliverable="spec")
    base.update(overrides)
    return EngineeringTask(**base)


def test_a_spec_only_task_says_so_in_the_prompt():
    from app.engineer.prompts import build_prompt

    prompt = build_prompt(spec_task(), {}, {"src/server/WidgetService.luau": "return {}"}, None, 1)
    assert "THIS TASK IS THE SPEC ONLY" in prompt
    assert "tests/WidgetService.spec.luau" in prompt


def test_an_ordinary_task_gets_no_spec_only_section():
    from app.engineer.prompts import build_prompt

    prompt = build_prompt(spec_task(deliverable="system"), {}, {}, None, 1)
    assert "THIS TASK IS THE SPEC ONLY" not in prompt


def test_a_backfill_that_rewrites_the_system_is_refused():
    """The system is already accepted, synced and running. A spec-only task
    that returns it too would land a rewrite nobody asked for."""
    import json

    from app.engineer.loop import EngineerLoop, Refusal

    answer = json.dumps({"files": [
        {"path": "tests/WidgetService.spec.luau", "content": "return function(h) return {} end\n"},
        {"path": "src/server/WidgetService.luau", "content": "--!strict\nreturn {}\n"},
    ], "services": [], "summary": "spec and a fix"})
    loop = EngineerLoop.__new__(EngineerLoop)
    loop.known_services = frozenset()
    with pytest.raises(Refusal) as raised:
        loop._accept(answer, set(), spec_task())
    assert "spec only" in str(raised.value) and "src/server/WidgetService.luau" in str(raised.value)


def test_a_backfill_returning_only_the_spec_is_accepted():
    import json

    from app.engineer.loop import EngineerLoop

    answer = json.dumps({"files": [
        {"path": "tests/WidgetService.spec.luau", "content": "return function(h) return {} end\n"},
    ], "services": [], "summary": "the spec"})
    loop = EngineerLoop.__new__(EngineerLoop)
    loop.known_services = frozenset()
    output, _services = loop._accept(answer, set(), spec_task())
    assert [file.path for file in output.files] == ["tests/WidgetService.spec.luau"]


def test_a_stored_summary_keeps_the_end_where_the_failure_is():
    """verify.ps1 prints its PASSes first and the reason last, so a summary
    clipped from the front stores everything except what it is for."""
    from app.engineer.gate import CheckResult, GateReport

    output = ("PASS filler line\n" * 400) + "FAIL  the case that matters: expected 4, got 7"
    stored = GateReport((CheckResult("verify.ps1", False, output),)).summary()[0]["output"]
    assert "expected 4, got 7" in stored
    assert len(stored) < len(output)


def test_the_project_listing_fits_a_budget_and_keeps_what_matters():
    """One prompt reached 761,280 characters at forty-five systems, which no
    model in the chain could read. What is quoted is now chosen by relevance:
    the system's own file, then the files that name it."""
    from app.engineer.prompts import select_sources

    existing = {
        "src/server/Huge.luau": "x" * 200_000,
        "src/server/WidgetService.luau": "--!strict\nreturn {}\n",
        "src/server/Neighbour.luau": "-- talks to WidgetService\n",
    }
    quoted, named = select_sources(spec_task(), existing, 1_000)
    assert "src/server/WidgetService.luau" in quoted
    assert "src/server/Neighbour.luau" in quoted
    assert named == ["src/server/Huge.luau"]


def test_what_does_not_fit_is_still_named():
    """A model that can see a file exists can say it needs it; one that cannot
    invents what it contains."""
    from app.engineer.prompts import build_prompt

    existing = {"src/server/Huge.luau": "x" * 200_000,
                "src/server/WidgetService.luau": "return {}\n"}
    prompt = build_prompt(spec_task(), {}, existing, None, 1, listing_budget=1_000)
    assert "src/server/Huge.luau" in prompt and "did not fit" in prompt
    assert len(prompt) < 20_000


def test_a_stored_summary_keeps_a_failure_buried_in_the_middle():
    """The behaviour runner prints specs alphabetically, so a failure sits
    wherever the alphabet puts it. Head and tail were both PASSes on the
    report that made this necessary."""
    from app.engineer.gate import CheckResult, GateReport

    output = ("PASS early\n" * 300) + "FAIL  the buried case: expected 4, got 7\n" + ("PASS late\n" * 300)
    stored = GateReport((CheckResult("verify.ps1", False, output),)).summary()[0]["output"]
    assert "the buried case: expected 4, got 7" in stored
    assert len(stored) < len(output)
