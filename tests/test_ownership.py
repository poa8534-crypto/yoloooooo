"""A lock the operating system releases when its holder dies.

Everything that decides whether a build or a service is still running rests
on this, so the claims are tested against real processes: one that is killed
outright, which no `finally` survives, and one whose child outlives it, which
is what a build looks like when `agy` is still running and the build's own
process is gone.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.ownership import Held, HeldElsewhere, is_held

ROOT = Path(__file__).resolve().parent.parent

HOLDER = """
import os, subprocess, sys, time
from app.ownership import Held
lock = Held(sys.argv[1])
assert lock.acquire(describe=True)
if sys.argv[2] == "with-child":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    print(child.pid, flush=True)
print("held", os.getpid(), flush=True)
time.sleep(60)
"""

# The base interpreter, not the venv's. On Windows the venv's python.exe is a
# launcher that runs the real interpreter as its child, inside a job that
# kills the whole tree when the launcher dies -- so killing it would also kill
# the child this file needs to outlive its parent, and the test would pass for
# the wrong reason. The holder imports only the standard library.
PYTHON = getattr(sys, "_base_executable", None) or sys.executable


def hold_in_another_process(path: Path, mode: str = "alone"):
    process = subprocess.Popen([PYTHON, "-c", HOLDER, str(path), mode],
                               cwd=ROOT, stdout=subprocess.PIPE, text=True)
    child_pid = int(process.stdout.readline()) if mode == "with-child" else None
    word, pid = process.stdout.readline().split()
    assert word == "held" and int(pid) == process.pid
    return process, child_pid


def alive(pid: int) -> bool:
    """Whether a process exists. Not os.kill(pid, 0): on Windows that
    terminates the process it was asked about."""
    if sys.platform == "win32":
        listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                                 capture_output=True, text=True, check=False).stdout
        return f'"{pid}"' in listing
    return Path(f"/proc/{pid}").exists()


def wait_until_free(path: Path, seconds: float = 5.0) -> bool:
    """The OS releases a dead holder's lock promptly, but not synchronously
    with the kill returning, so this allows it a moment."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not is_held(path):
            return True
        time.sleep(0.05)
    return False


def test_a_lock_is_held_until_released(tmp_path):
    path = tmp_path / "thing.lock"
    lock = Held(path)

    assert lock.acquire()
    assert is_held(path)
    lock.release()
    assert not is_held(path)


def test_a_second_holder_in_the_same_process_is_refused(tmp_path):
    # The dashboard runs builds in-process and also answers questions about
    # them. It must get the true answer about a lock it holds itself.
    path = tmp_path / "thing.lock"
    first, second = Held(path), Held(path)

    assert first.acquire()
    assert not second.acquire()
    with pytest.raises(HeldElsewhere):
        with Held(path):
            pass
    first.release()
    assert second.acquire()
    second.release()


def test_asking_about_a_lock_that_never_existed_creates_nothing(tmp_path):
    path = tmp_path / "never.lock"

    assert not is_held(path)
    assert not path.exists()


def test_a_killed_holder_releases_its_lock(tmp_path):
    path = tmp_path / "service.lock"
    process, _ = hold_in_another_process(path)
    try:
        assert is_held(path)
        assert Held(path).holder()["pid"] == process.pid
    finally:
        process.kill()
        process.wait()

    assert wait_until_free(path)


def test_a_child_that_outlives_its_parent_does_not_keep_the_lock(tmp_path):
    # A build's `agy` or `git` can still be running when the build's own
    # process is killed. If the child inherited the lock, the dead build would
    # be reported as running for as long as the child lived. On Windows the
    # lock belongs to the process, so this holds even for an inherited handle;
    # under flock it holds because descriptors are not inherited (PEP 446).
    path = tmp_path / "build.lock"
    process, child_pid = hold_in_another_process(path, "with-child")
    try:
        process.kill()
        process.wait()
        assert alive(child_pid), "the child died with its parent, so this proves nothing"
        assert wait_until_free(path)
    finally:
        subprocess.run(["taskkill", "/F", "/PID", str(child_pid)] if sys.platform == "win32"
                       else ["kill", "-9", str(child_pid)], capture_output=True, check=False)
