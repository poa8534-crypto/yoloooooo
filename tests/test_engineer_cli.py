"""The terminal the engineer reports through.

A check's output is not text this project controls: selene draws diagnostics
with box-drawing characters and luau-lsp quotes source. Windows hands a
redirected stdout cp1252, so printing one killed a real run at attempt 1 of 6 --
the failure was in reporting the failure, and the loop unwound with it.
"""

from __future__ import annotations

import io

from app.engineer.cli import use_utf8

SELENE_DIAGNOSTIC = "  ┌── src/server/DataService.luau:12:7\n  │\n"


def cp1252_stream() -> io.TextIOWrapper:
    """What Windows gives a redirected stdout."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")


def test_a_cp1252_stream_cannot_take_selene_output_unaided():
    """The bug, stated as a test: without this the rest of the file passes
    trivially on any platform whose default encoding is UTF-8."""
    stream = cp1252_stream()
    try:
        stream.write(SELENE_DIAGNOSTIC)
        stream.flush()
    except UnicodeEncodeError:
        return
    raise AssertionError("cp1252 accepted box-drawing characters; the test is no longer testing anything")


def test_use_utf8_makes_the_stream_carry_them():
    stream = cp1252_stream()
    use_utf8(stream)
    stream.write(SELENE_DIAGNOSTIC)
    stream.flush()
    assert stream.buffer.getvalue().decode("utf-8") == SELENE_DIAGNOSTIC


def test_output_no_encoding_covers_is_replaced_not_raised():
    """A lone surrogate survives a model's JSON and reaches print. Replacing it
    keeps a run alive; raising loses every remaining attempt."""
    stream = cp1252_stream()
    use_utf8(stream)
    stream.write("before \udc80 after")
    stream.flush()
    assert b"before " in stream.buffer.getvalue()


def test_a_stream_that_cannot_be_reconfigured_is_left_alone():
    """pytest and some CI wrappers replace sys.stdout with an object that has
    no `reconfigure`. Touching it must not be an error."""
    use_utf8(io.StringIO())
