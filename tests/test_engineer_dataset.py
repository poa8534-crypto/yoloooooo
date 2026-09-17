"""Repair examples built from captured attempts.

The dangerous mistake here is quiet: pairing a failed attempt with a fix that
was never verified. That teaches the model its wrong second guess was right,
and nothing downstream would notice -- the file is valid JSONL either way. So
most of these tests are about what must NOT be emitted.
"""

from __future__ import annotations

import json

from app.engineer.capture import Attempt
from app.engineer.dataset import repairs, tool_message, write_jsonl

BAD = '{"files": [{"path": "src/server/A.luau", "content": "broken"}], "services": [], "summary": "x"}'
GOOD = "--!strict\nreturn {}\n"


def attempt(number: int, *, passed: bool = False, files=None, checks=None, refusal=None,
            run_id: str = "run-1") -> Attempt:
    return Attempt(run_id=run_id, attempt=number, model="qwen2.5-coder:14b", task={"system": "DataService"},
                   prompt="build a DataService", answer=BAD, passed=passed,
                   files=files if files is not None else {"src/server/A.luau": "broken"},
                   services=["Players"], refusal=refusal, checks=checks or [])


SELENE_FAILED = [{"check": "guard", "passed": True, "output": ""},
                 {"check": "selene", "passed": False, "output": "error[parse_error]: unexpected token `as`"}]


def test_a_failed_attempt_and_the_accepted_file_make_one_repair():
    [repair] = list(repairs([attempt(1, checks=SELENE_FAILED)], {"src/server/A.luau": GOOD}))

    roles = [message["role"] for message in repair.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert repair.source == "human"
    assert "unexpected token `as`" in repair.messages[3]["content"]
    assert json.loads(repair.messages[4]["content"])["files"][0]["content"] == GOOD


def test_the_tool_turn_carries_the_exact_compiler_output():
    """Paraphrasing it would train the model against text no tool emits."""
    message = tool_message(attempt(1, checks=SELENE_FAILED))
    assert message == "## selene FAILED\nerror[parse_error]: unexpected token `as`"


def test_a_pre_gate_refusal_is_the_tool_turn_when_there_are_no_checks():
    message = tool_message(attempt(1, refusal="'/etc/passwd': files may only be written under src/server"))
    assert "files may only be written under src/server" in message


def test_two_failures_in_a_row_are_not_a_repair():
    """The second attempt also failed, so it is not a fix -- and with no
    accepted file there is nothing to pair either one with."""
    assert list(repairs([attempt(1), attempt(2)])) == []


def test_a_later_passing_attempt_is_the_fix_for_every_earlier_one():
    examples = list(repairs([attempt(1), attempt(2),
                             attempt(3, passed=True, files={"src/server/A.luau": GOOD})]))

    assert [(e.attempt, e.source) for e in examples] == [(1, "attempt"), (2, "attempt")]
    assert all(json.loads(e.messages[4]["content"])["files"][0]["content"] == GOOD for e in examples)


def test_the_passing_attempt_is_not_itself_an_example():
    assert list(repairs([attempt(1, passed=True, files={"src/server/A.luau": GOOD})])) == []


def test_an_earlier_pass_is_not_a_fix_for_a_later_failure():
    """Attempt 1 passing cannot explain attempt 2 failing; nothing in that
    ordering is a repair."""
    assert list(repairs([attempt(1, passed=True, files={"src/server/A.luau": GOOD}),
                         attempt(2)])) == []


def test_a_fix_identical_to_the_broken_code_is_not_a_repair():
    """The accepted file matching what the model wrote means the failure was
    somewhere else; an example with identical turns teaches nothing."""
    assert list(repairs([attempt(1, files={"src/server/A.luau": GOOD})],
                        {"src/server/A.luau": GOOD})) == []


def test_the_fix_holds_only_the_paths_the_attempt_wrote():
    """The accepted tree is the whole project. Answering with files the task
    never asked for would teach the model to invent them."""
    [repair] = list(repairs([attempt(1, checks=SELENE_FAILED)],
                            {"src/server/A.luau": GOOD, "src/server/Unrelated.luau": "--!strict\n"}))

    answer = json.loads(repair.messages[4]["content"])
    assert [file["path"] for file in answer["files"]] == ["src/server/A.luau"]


def test_an_attempt_that_wrote_nothing_has_no_repair():
    """Refused before the gate: there is no code to pair with a fix."""
    assert list(repairs([attempt(1, files={}, refusal="not JSON")], {"src/server/A.luau": GOOD})) == []


def test_runs_are_kept_apart():
    """A pass in one run must never be used as the fix for another's failure."""
    examples = list(repairs([attempt(1, run_id="run-1"),
                             attempt(2, run_id="run-2", passed=True, files={"src/server/A.luau": GOOD})]))
    assert examples == []


def test_write_jsonl_is_one_example_per_line(tmp_path):
    out = tmp_path / "nested" / "repairs.jsonl"
    count = write_jsonl(repairs([attempt(1, checks=SELENE_FAILED)], {"src/server/A.luau": GOOD}), out)

    lines = out.read_text(encoding="utf-8").splitlines()
    assert count == len(lines) == 1
    assert json.loads(lines[0])["metadata"]["fix_source"] == "human"
