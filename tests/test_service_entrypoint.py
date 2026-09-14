"""The windowless service entrypoint, which nothing covered.

Its stdout replacement broke uvicorn's logging configuration, so the scheduled
task exited before binding a port. The failure was invisible to the suite
because no test imported `app.service`.
"""

from __future__ import annotations

import logging

import pytest

from app.service import LOG_BACKUPS, MAX_LOG_BYTES, _StreamToLog


@pytest.fixture
def stream() -> _StreamToLog:
    return _StreamToLog(logging.getLogger("service-entrypoint-test"), logging.INFO)


def test_uvicorn_can_configure_logging_against_the_adapter(stream):
    """uvicorn's colourized formatter calls isatty() while configuring."""
    from uvicorn.logging import DefaultFormatter

    assert stream.isatty() is False
    formatter = DefaultFormatter(fmt="%(levelprefix)s %(message)s")
    assert formatter.use_colors is False


def test_the_adapter_satisfies_the_stream_protocol_uvicorn_probes(stream):
    assert stream.writable() is True
    assert stream.readable() is False
    assert stream.seekable() is False
    assert stream.encoding == "utf-8"
    with pytest.raises(OSError):
        stream.fileno()


def test_writes_are_forwarded_line_by_line(stream, caplog):
    with caplog.at_level(logging.INFO, logger="service-entrypoint-test"):
        stream.write("first line\nsecond line\n")
        stream.write("partial")
        stream.flush()
    assert "first line" in caplog.text
    assert "second line" in caplog.text
    assert "partial" in caplog.text


def test_writelines_and_close_do_not_lose_output(stream, caplog):
    with caplog.at_level(logging.INFO, logger="service-entrypoint-test"):
        stream.writelines(["alpha\n", "beta\n"])
        stream.write("trailing without newline")
        stream.close()
    assert "alpha" in caplog.text and "beta" in caplog.text
    assert "trailing without newline" in caplog.text


def test_blank_lines_are_not_logged(stream, caplog):
    with caplog.at_level(logging.INFO, logger="service-entrypoint-test"):
        stream.write("\n\n   \n")
        stream.flush()
    assert caplog.records == []


def test_log_rotation_is_bounded():
    assert 0 < MAX_LOG_BYTES <= 50 * 1024 * 1024
    assert LOG_BACKUPS >= 1
