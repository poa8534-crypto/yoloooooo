from __future__ import annotations

import logging
import sys
import traceback
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from .config import ROOT
from .main import run

MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3


class _StreamToLog:
    """Adapts print()-style writes onto a rotating log handler.

    This replaces `sys.stdout`/`sys.stderr`, so it has to satisfy the parts of
    the stream protocol that other libraries probe. uvicorn's colourized
    formatter calls `sys.stdout.isatty()` while configuring logging, and a
    missing method there kills the service before it can bind a port.
    """

    encoding = "utf-8"

    def __init__(self, logger: logging.Logger, level: int):
        self._logger = logger
        self._level = level
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._logger.log(self._level, line)
        return len(text)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        if self._buffer.strip():
            self._logger.log(self._level, self._buffer)
        self._buffer = ""

    def isatty(self) -> bool:
        """A log file is never a terminal, so colour codes stay out of it."""
        return False

    def readable(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def fileno(self) -> int:
        # There is no underlying descriptor. OSError is what callers expect.
        raise OSError("this stream is a logging adapter with no file descriptor")

    def close(self) -> None:
        self.flush()


def main() -> None:
    """Run the local server with durable, rotating logs for a hidden task."""
    log_directory = ROOT / "data"
    log_directory.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        log_directory / "service.log",
        maxBytes=MAX_LOG_BYTES,
        backupCount=LOG_BACKUPS,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logger = logging.getLogger("venture-agents")

    sys.stdout = _StreamToLog(logger, logging.INFO)
    sys.stderr = _StreamToLog(logger, logging.ERROR)
    logger.info("Starting Roblox Venture Agents at %s", datetime.now(UTC).isoformat())
    try:
        run()
    except BaseException:
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
