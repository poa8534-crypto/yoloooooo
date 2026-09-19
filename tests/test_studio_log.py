"""Reading a playtest from Studio's own log.

The lines below are copied from a real Studio 0.739 log: an ascent-era build
whose modules failed to load. What matters is that a script's warnings and
errors are found, and that Studio's own chatter is not mistaken for them.
"""

from __future__ import annotations

import os

from app.bridge.studio_log import newest, parse, read_since

REAL = (
    "2026-09-19T05:04:54.597Z,1152.597,4d54,6,Info [FLog::CreatorOutput] Info: Stack Begin\n"
    "2026-09-19T05:04:54.597Z,1152.597,4d54,6,Info [FLog::CreatorOutput] Info: "
    "Script 'ServerScriptService.Server.LineAssistService', Line 4\n"
    "2026-09-19T05:04:54.598Z,1152.598,4d54,6,Warning [FLog::CreatorWarning] Warning: "
    "[VentureBootstrap] LineAssistService failed to load: Requested module experienced "
    "an error while loading\n"
    "2026-09-19T05:04:54.598Z,1152.598,4d54,6,Error [FLog::CreatorError] Error: "
    "Requested module experienced an error while loading\n"
    "2026-09-19T05:04:54.600Z,1152.600,4d54,6,Info [FLog::CreatorOutput] "
    "[VentureBootstrap] 8 module(s) loaded, 0 started\n"
    "2026-09-19T12:55:54.803Z,8.803,4db0,6,Warning [FLog::Output] Hello world, CLI-208683!\n"
)


def test_a_scripts_prints_warnings_and_errors_are_found_by_level():
    entries = parse(REAL)

    levels = [entry.level for entry in entries]
    assert levels == ["info", "info", "warning", "error", "info"]
    assert entries[2].message.startswith("[VentureBootstrap] LineAssistService failed")
    assert entries[3].message == "Requested module experienced an error while loading"
    assert entries[4].at == "2026-09-19T05:04:54.600Z"


def test_studios_own_output_is_not_read_as_the_places():
    """`FLog::Output` is Studio talking about itself, at Warning level, every
    few minutes. Counted as the place's warnings, it would fail every test."""
    assert not any("CLI-208683" in entry.message for entry in parse(REAL))


def test_a_line_still_being_written_waits_for_the_next_read(tmp_path):
    log = tmp_path / "0.739_Studio_X_last.log"
    complete = REAL.splitlines(keepends=True)[4]
    log.write_bytes((complete + "2026-09-19T05:04:55Z,1,1,6,Error [FLog::CreatorEr").encode())

    first, offset = read_since(log, 0)
    assert [entry.level for entry in first] == ["info"]
    assert offset == len(complete.encode())

    with log.open("ab") as handle:
        handle.write(b"ror] Error: boom\n")
    second, _end = read_since(log, offset)
    assert [(entry.level, entry.message) for entry in second] == [("error", "boom")]


def test_reading_starts_where_it_is_told(tmp_path):
    """Only what happened after the sync belongs to it: the log also holds
    every earlier build of the session."""
    log = tmp_path / "0.739_Studio_X_last.log"
    log.write_bytes(REAL.encode())
    before = len(REAL.encode())
    with log.open("ab") as handle:
        handle.write(b"2026-09-19T06:00:00Z,1,1,6,Info [FLog::CreatorOutput] after\n")

    entries, _end = read_since(log, before)

    assert [entry.message for entry in entries] == ["after"]


def test_the_newest_session_log_is_the_one_written_last(tmp_path):
    older = tmp_path / "0.739_20260918T000000Z_Studio_A_last.log"
    newer = tmp_path / "0.739_20260919T000000Z_Studio_B_last.log"
    player = tmp_path / "0.739_20260919T010000Z_Player_C_last.log"
    for index, path in enumerate((older, newer, player)):
        path.write_text("x\n")
        os.utime(path, (1_000 + index, 1_000 + index))

    assert newest(tmp_path) == newer
    assert newest(tmp_path / "missing") is None
