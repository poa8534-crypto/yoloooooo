"""Who is running something, as the operating system sees it.

Two kinds of record in this system say that work is in flight: the ledger's
research runs and audits, and the build records. Both were trusted without
asking whether anything was still doing the work, and both were wrong.

- A second copy of the service, launched while the first was still serving,
  ran its startup before it failed to bind a port -- and startup marks every
  run in flight as interrupted by a previous shutdown. The watchdog did this
  21 times in one afternoon. Nothing was in flight that day; any run that had
  been would have been recorded as interrupted while it was still running.
- Five build records sat in `generating`, `planning` and `building` for up to
  seventeen hours after the processes running them had gone, and the
  dashboard drew each one as a build in progress.

Both need the same fact: is some live process holding this? A lock the
operating system releases when its holder exits answers that, however the
holder exits -- including being killed, which no `finally` survives. So the
answer is measured rather than inferred from timestamps or process names.

The lock is an exclusive byte-range lock (`msvcrt.locking`) on Windows and
`flock` elsewhere. Both conflict between two handles in the same process as
well as between processes, so a process can ask about a lock it holds itself
and get the true answer.

An `agy` or `git` started during a build can outlive the build's process, and
must not keep the build looking alive. On Windows a byte-range lock belongs to
the process that took it, so not even an inherited handle keeps it -- measured,
by making the handle inheritable on purpose and killing the holder. `flock`
belongs to the open file instead, and there it is PEP 446 -- descriptors are
not inherited unless asked -- that keeps a child from holding it.

Lock files are never deleted. Removing one while another process is opening it
is the classic way two holders end up with a lock each on different files.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

_FLAGS = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)


def _lock(fd: int) -> None:
    """Take the lock without waiting, or raise OSError."""
    if os.name == "nt":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if os.name == "nt":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


def whoami() -> dict:
    """This process, as a lock holder describes itself."""
    return {"pid": os.getpid(), "host": socket.gethostname(),
            "since": datetime.now(UTC).isoformat()}


class Held:
    """An exclusive lock on one file, held until released or until this
    process exits."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._fd: int | None = None

    @property
    def held_here(self) -> bool:
        return self._fd is not None

    @property
    def holder_path(self) -> Path:
        return self.path.with_name(self.path.name + ".holder")

    def acquire(self, describe: bool = False) -> bool:
        """Take the lock if nobody holds it. Never waits.

        `describe` also writes who took it beside the lock, so whoever is
        refused can say which process it lost to. The description is only
        ever read while the lock is held, so a stale one is never believed.
        """
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, _FLAGS, 0o644)
        try:
            _lock(fd)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        if describe:
            try:
                self.holder_path.write_text(json.dumps(
                    {**whoami(), "command": " ".join(sys.argv)[:300]}), encoding="utf-8")
            except OSError:
                pass  # the description is a courtesy; the lock is the fact
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        except OSError:
            pass  # closing releases it regardless
        finally:
            os.close(fd)

    def holder(self) -> dict:
        """Who described themselves as holding this, or {} if nobody did."""
        try:
            return json.loads(self.holder_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def __enter__(self) -> Held:
        if not self.acquire():
            raise HeldElsewhere(self.path)
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class HeldElsewhere(RuntimeError):
    def __init__(self, path: Path):
        super().__init__(f"{path} is held by another process")
        self.path = path


def is_held(path: Path | str) -> bool:
    """Whether a live process holds the lock at `path` -- this one included.

    Asked by trying to take it. A file that does not exist was never locked,
    and is not created just to find that out.
    """
    path = Path(path)
    if not path.exists():
        return False
    probe = Held(path)
    if probe.acquire():
        probe.release()
        return False
    return True
