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
    """Adapts print()-style writes onto a rotating log handler."""

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

    def flush(self) -> None:
        if self._buffer.strip():
            self._logger.log(self._level, self._buffer)
        self._buffer = ""


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
