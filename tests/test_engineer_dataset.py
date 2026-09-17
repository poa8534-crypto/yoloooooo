"""Repair examples built from captured attempts.

Two mistakes here are quiet, and both survive every downstream check because
the file is valid JSONL either way:

  * pairing a failed attempt with a fix that was never verified, which teaches
    the model its wrong second guess was right;
  * putting the broken code in an assistant turn, which teaches the model to
    write it, because the standard recipe trains on every assistant turn.

So most of these tests are about what must NOT be emitted.
"""

from __future__ import annotations

import json

from app.engineer.capture import Attempt
from app.engineer.dataset import repairs, retry_prompt, tool_message, write_jsonl

BAD = '{"files": [{"path": "src/server/A.luau", "content": "broken"}], "services": [], "summary": "x"}'
GOOD = "--!strict\nreturn {}\n"


def attempt(number: int, *, passed: bool = False, files=None, checks=None, refusal=None,
            run_id: str = "run-1", prompt: str = "build a BaseHealth") -> Attempt:
    return Attempt(run_id=run_id, attempt=number, model="qwen2.5-coder:14b", task={"system": "BaseHealth"},
                   prompt=prompt, answer=BAD, passed=passed,
                   files=files if files is not None else {"src/server/A.luau": "broken"},
                   services=["Players"], refusal=refusal, checks=checks or [])


SELENE_FAILED = [{"check": "guard", "passed": True, "output": ""},
                 {"check": "selene", "passed": False,
                  "output": "error[undefined_variable]: `isfinite` is not defined"}]


# ---- the shape of an example ----------------------------------------------

def test_an_example_has_exactly_one_assistant_turn_and_it_is_the_fix():
    """The broken code must never be an assistant turn: training on assistant
    turns is the default recipe, and it would learn to write the broken one."""
    [repair] = list(repairs([attempt(1, checks=SELENE_FAILED)], {"src/server/A.luau": GOOD}))

    assert [m["role"] for m in repair.messages] == ["system", "user", "assistant"]
    assert json.loads(repair.messages[2]["content"])["files"][0]["content"] == GOOD
    assert "broken" not in repair.messages[2]["content"]


def test_the_broken_answer_and_its_errors_are_in_the_user_turn():
    """They are context for the fix, exactly as the loop sends them on a retry."""
    [repair] = list(repairs([attempt(1, checks=SELENE_FAILED)], {"src/server/A.luau": GOOD}))

    user = repair.messages[1]["content"]
    assert "isfinite" in user
    assert "previous_attempt" in user


def test_a_later_attempts_prompt_is_used_verbatim():
    """It is what the model was actually sent, feedback included. Nothing
    reconstructed can promise that."""
    run = [attempt(1, checks=SELENE_FAILED),
           attempt(2, prompt="THE REAL RETRY PROMPT"),
           attempt(3, passed=True, files={"src/server/A.luau": GOOD})]

    first = next(r for r in repairs(run) if r.attempt == 1)
    assert first.messages[1]["content"] == "THE REAL RETRY PROMPT"
    assert first.prompt == "captured"


def test_only_the_last_attempt_of_a_run_needs_a_synthesised_prompt():
    [repair] = list(repairs([attempt(1, checks=SELENE_FAILED)], {"src/server/A.luau": GOOD}))
    assert repair.prompt == "synthesised"


def test_the_synthesised_prompt_keeps_the_original_task():
    prompt, origin = retry_prompt(attempt(2, prompt="build a BaseHealth", checks=SELENE_FAILED), None)
    assert prompt.startswith("build a BaseHealth")
    assert 'number="2"' in prompt
    assert origin == "synthesised"


def test_the_tool_output_is_carried_verbatim():
    """Paraphrasing it would train the model against text no tool emits."""
    assert tool_message(attempt(1, checks=SELENE_FAILED)) == (
        "## selene FAILED\nerror[undefined_variable]: `isfinite` is not defined")


def test_a_pre_gate_refusal_is_the_feedback_when_there_are_no_checks():
    assert "may only be written under src/server" in tool_message(
        attempt(1, refusal="'/etc/passwd': files may only be written under src/server"))


# ---- what must not become an example --------------------------------------

def test_two_failures_in_a_row_are_not_a_repair():
    assert list(repairs([attempt(1), attempt(2)])) == []


def test_a_later_passing_attempt_is_the_fix_for_every_earlier_one():
    examples = list(repairs([attempt(1), attempt(2),
                             attempt(3, passed=True, files={"src/server/A.luau": GOOD})]))

    assert [(e.attempt, e.source) for e in examples] == [(1, "attempt"), (2, "attempt")]
    assert all(json.loads(e.messages[2]["content"])["files"][0]["content"] == GOOD for e in examples)


def test_the_passing_attempt_is_not_itself_an_example():
    assert list(repairs([attempt(1, passed=True, files={"src/server/A.luau": GOOD})])) == []


def test_an_earlier_pass_is_not_a_fix_for_a_later_failure():
    assert list(repairs([attempt(1, passed=True, files={"src/server/A.luau": GOOD}), attempt(2)])) == []


def test_a_fix_identical_to_the_broken_code_is_not_a_repair():
    assert list(repairs([attempt(1, files={"src/server/A.luau": GOOD})],
                        {"src/server/A.luau": GOOD})) == []


def test_the_fix_holds_only_the_paths_the_attempt_wrote():
    """The accepted tree is the whole project. Answering with files the task
    never asked for would teach the model to invent them."""
    [repair] = list(repairs([attempt(1, checks=SELENE_FAILED)],
                            {"src/server/A.luau": GOOD, "src/server/Unrelated.luau": "--!strict\n"}))

    assert [f["path"] for f in json.loads(repair.messages[2]["content"])["files"]] == ["src/server/A.luau"]


def test_an_attempt_that_wrote_nothing_has_no_repair():
    assert list(repairs([attempt(1, files={}, refusal="not JSON")], {"src/server/A.luau": GOOD})) == []


def test_runs_are_kept_apart():
    """A pass in one run must never be the fix for another run's failure."""
    assert list(repairs([attempt(1, run_id="run-1"),
                         attempt(2, run_id="run-2", passed=True,
                                 files={"src/server/A.luau": GOOD})])) == []


def test_write_jsonl_is_one_example_per_line(tmp_path):
    out = tmp_path / "nested" / "repairs.jsonl"
    count = write_jsonl(repairs([attempt(1, checks=SELENE_FAILED)], {"src/server/A.luau": GOOD}), out)

    lines = out.read_text(encoding="utf-8").splitlines()
    assert count == len(lines) == 1
    assert json.loads(lines[0])["metadata"]["fix_source"] == "human"
