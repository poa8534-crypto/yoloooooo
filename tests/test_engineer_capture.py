"""Keeping every attempt, including the ones nobody wanted.

The recorder exists to build a training set out of this model's own mistakes,
so the tests that matter are: a refused attempt is kept (it is the valuable
one), recording cannot fail a run, and nothing secret reaches the disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.engineer.capture import Attempt, AttemptRecorder, load_attempts


def an_attempt(**overrides) -> Attempt:
    fields = {"run_id": "data-service-abc123", "attempt": 1, "model": "qwen2.5-coder:14b",
              "task": {"system": "DataService"}, "prompt": "build it", "answer": '{"files": []}',
              "passed": False}
    return Attempt(**{**fields, **overrides})


def test_a_refused_attempt_is_kept_with_its_exact_failure(tmp_path):
    recorder = AttemptRecorder(tmp_path)
    checks = [{"check": "selene", "passed": False, "output": "error[parse_error]: unexpected token `as`"}]
    path = recorder.record(an_attempt(files={"src/server/A.luau": "--!strict\n"}, checks=checks))

    stored = json.loads(Path(path).read_text(encoding="utf-8"))
    assert stored["files"]["src/server/A.luau"] == "--!strict\n"
    assert stored["checks"][0]["output"].startswith("error[parse_error]")
    assert stored["passed"] is False


def test_failures_are_what_a_repair_example_is_built_from():
    attempt = an_attempt(checks=[{"check": "guard", "passed": True, "output": ""},
                                 {"check": "selene", "passed": False, "output": "2 warnings"}])
    assert [check["check"] for check in attempt.failures()] == ["selene"]


def test_an_answer_refused_before_the_gate_is_kept_too(tmp_path):
    """Bad JSON and unsafe paths are a different mistake from failing a check,
    and the model makes them; an example of each is worth having."""
    path = AttemptRecorder(tmp_path).record(
        an_attempt(refusal="Your answer was not the required JSON object: files: Field required"))
    stored = json.loads(Path(path).read_text(encoding="utf-8"))
    assert "files: Field required" in stored["refusal"]
    assert stored["checks"] == []


def test_each_attempt_of_a_run_gets_its_own_file(tmp_path):
    recorder = AttemptRecorder(tmp_path)
    for number in (1, 2, 10):
        recorder.record(an_attempt(attempt=number))

    files = sorted(p.name for p in (tmp_path / "data-service-abc123").iterdir())
    # Zero-padded, so attempt 2 sorts before attempt 10 both here and in
    # `load_attempts`, which replays them in the order they were made.
    assert files == ["attempt-01.json", "attempt-02.json", "attempt-10.json"]


def test_recording_never_fails_the_run(tmp_path):
    """Six hours of generation must not be lost to a directory that cannot be
    written. The root here is a regular file, so `mkdir` under it raises."""
    root = tmp_path / "not-a-directory"
    root.write_text("", encoding="utf-8")
    reported: list[str] = []

    assert AttemptRecorder(root, on_error=reported.append).record(an_attempt()) is None
    assert reported and "attempt 1 was not recorded" in reported[0]


def test_a_key_that_reached_the_prompt_does_not_reach_the_disk(tmp_path, monkeypatch):
    from app import config

    settings = config.get_settings().model_copy(update={"gemini_api_key_1": "AIzaSyTOTALLYSECRET"})
    monkeypatch.setattr(config, "get_settings", lambda: settings)

    path = AttemptRecorder(tmp_path).record(
        an_attempt(prompt="context AIzaSyTOTALLYSECRET here", answer="echoed AIzaSyTOTALLYSECRET"))

    text = Path(path).read_text(encoding="utf-8")
    assert "AIzaSyTOTALLYSECRET" not in text
    assert text.count("[REDACTED]") >= 2


def test_a_run_id_never_escapes_the_capture_directory(tmp_path):
    """Run ids are generated, but the recorder takes them as data: a path
    separator in one must not write outside the root."""
    AttemptRecorder(tmp_path).record(an_attempt(run_id="../../escaped"))
    assert not (tmp_path.parent / "escaped").exists()
    assert list(tmp_path.rglob("attempt-01.json"))


def test_load_attempts_replays_them_in_order(tmp_path):
    recorder = AttemptRecorder(tmp_path)
    recorder.record(an_attempt(attempt=2, model="second"))
    recorder.record(an_attempt(attempt=1, model="first"))

    assert [a.model for a in load_attempts(tmp_path)] == ["first", "second"]


@pytest.mark.parametrize("content", ["{not json", '{"schema": 99}', "[]"])
def test_one_unreadable_file_does_not_cost_the_rest(tmp_path, content):
    AttemptRecorder(tmp_path).record(an_attempt())
    (tmp_path / "data-service-abc123" / "attempt-09.json").write_text(content, encoding="utf-8")

    assert [a.attempt for a in load_attempts(tmp_path)] == [1]


def test_an_empty_capture_directory_is_not_an_error(tmp_path):
    assert load_attempts(tmp_path / "never-written") == []
