"""What Studio itself says, read from its own log file.

The plugin forwards errors through LogService, but only from the DataModel it
was handed a batch in -- and a playtest runs in new ones, where the plugin
starts again knowing no build. Whether anything reaches the bridge from a
running test is unproven (docs/STUDIO_EXPERIMENTS.md, F and G).

Studio also writes everything its Output window shows into a log file of its
own, one per session, whichever DataModel produced it. That file is the
measured record of a playtest:

    Info    [FLog::CreatorOutput]   print, and each line of a stack trace
    Warning [FLog::CreatorWarning]  warn
    Error   [FLog::CreatorError]    an error

Studio's own chatter goes to other channels (`FLog::Output` and the rest), so
reading only the Creator ones is reading only what the place's scripts said.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

LEVELS: dict[str, str] = {
    "CreatorOutput": "info",
    "CreatorWarning": "warning",
    "CreatorError": "error",
}
_CREATOR = re.compile(r"\[FLog::(Creator(?:Output|Warning|Error))\] ?(.*)$")
# Studio repeats the level at the front of the message; the level is already
# known from the channel, so the repeat is noise.
_REPEATED = {"info": "Info: ", "warning": "Warning: ", "error": "Error: "}


@dataclass(frozen=True)
class Entry:
    at: str
    level: str
    message: str


def log_folder() -> Path:
    """Where Roblox writes its logs on Windows."""
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "Roblox" / "logs"


def newest(folder: Path | None = None) -> Path | None:
    """The log of the Studio session running now: the one written last."""
    where = folder or log_folder()
    if not where.is_dir():
        return None
    logs = list(where.glob("*_Studio_*.log"))
    return max(logs, key=lambda path: path.stat().st_mtime) if logs else None


def parse(text: str) -> list[Entry]:
    entries: list[Entry] = []
    for line in text.splitlines():
        match = _CREATOR.search(line)
        if match is None:
            continue
        level = LEVELS[match.group(1)]
        message = match.group(2).removeprefix(_REPEATED[level])
        entries.append(Entry(at=line.split(",", 1)[0], level=level, message=message))
    return entries


def read_since(path: Path, offset: int) -> tuple[list[Entry], int]:
    """What was written after `offset`, and where the complete lines end now.

    Read as bytes up to the last line break, so a line Studio is halfway
    through writing is left for the next read rather than cut in two.
    """
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
    end = data.rfind(b"\n") + 1
    return parse(data[:end].decode("utf-8", "replace")), offset + end
