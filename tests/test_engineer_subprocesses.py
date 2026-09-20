"""A child process does not outlive the run that started it.

Seven orphaned Antigravity processes accumulated over an afternoon of stopped
builds, and the next build died with "RuntimeError: can't start new thread" --
a message that names nothing about its cause. Two holes made them:
`subprocess.run` kills a child on timeout and not what the child started, and
a parent that is killed leaves everything it started running.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from app.engineer import subprocesses

SLEEP = [sys.executable, "-c", "import time; time.sleep(30)"]


def test_output_and_exit_code_come_back_as_from_subprocess_run():
    done = subprocesses.run([sys.executable, "-c", "print('hello'); raise SystemExit(3)"],
                            timeout=60)
    assert done.returncode == 3
    assert b"hello" in done.stdout


def test_input_reaches_the_child():
    done = subprocesses.run([sys.executable, "-c",
                             "import sys; sys.stdout.write(sys.stdin.read().upper())"],
                            input=b"quiet", timeout=60)
    assert done.stdout.strip() == b"QUIET"


def test_a_timeout_raises_and_does_not_leave_the_child_running():
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        subprocesses.run(SLEEP, timeout=1.0)
    # It returns when the child is gone, not when the child would have ended:
    # the sleep is thirty seconds and the timeout is one.
    assert time.monotonic() - started < 20, "the timeout waited for the child to finish by itself"


def test_a_child_that_starts_a_child_is_killed_with_it():
    """`agy` is a launcher; the model runs in what it spawns. Killing only the
    named process left those spawned processes running."""
    grandchild = (
        "import subprocess, sys, time; "
        f"p = subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(30)']); "
        "print(p.pid, flush=True); time.sleep(30)"
    )
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        subprocesses.run([sys.executable, "-c", grandchild], timeout=2.0)

    printed = (raised.value.output or b"").decode().strip()
    if not printed:
        pytest.skip("the child did not report its own child's pid before it was killed")
    pid = int(printed.splitlines()[0])

    # Give the kill a moment to walk the tree, then ask whether it is gone.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not _alive(pid):
            return
        time.sleep(0.2)
    pytest.fail(f"process {pid}, started by the child, is still running")


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                                 capture_output=True, check=False)
        return str(pid) in listing.stdout.decode("utf-8", "replace")
    try:
        import os

        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(sys.platform != "win32", reason="the job object is the Windows answer")
def test_the_job_is_made_once_and_kills_what_it_holds_when_this_process_ends():
    """The job carries KILL_ON_JOB_CLOSE, and this process holds the only
    handle -- so the children go when it goes, however it goes."""
    first = subprocesses._windows_job()
    second = subprocesses._windows_job()
    assert first is not None, "the job could not be created; children would be left behind"
    assert first is second, "a second job was made, so the first one's children answer to nobody"


@pytest.mark.skipif(sys.platform != "win32", reason="the job object is the Windows answer")
def test_a_child_that_eats_memory_is_refused_before_the_machine_is():
    """A generated spec looped under Lune and two runners grew to 16GB and
    9GB -- 25 of this machine's 32 -- and the machine went down with them.
    A child that reaches the limit must fail on its own."""
    greedy = (
        "blocks = []\n"
        "while True:\n"
        "    blocks.append(bytearray(64 * 1024 * 1024))\n"
    )
    done = subprocesses.run([sys.executable, "-c", greedy], timeout=120)
    assert done.returncode != 0, "a child allocating without end was allowed to keep going"


@pytest.mark.skipif(sys.platform != "win32", reason="the job object is the Windows answer")
def test_the_limit_is_far_above_what_the_toolchain_uses():
    """Four gigabytes must not fail honest work: a whole-project Lune run sits
    in the low hundreds of megabytes."""
    modest = "data = bytearray(256 * 1024 * 1024); print(len(data))"
    done = subprocesses.run([sys.executable, "-c", modest], timeout=120)
    assert done.returncode == 0, "a quarter of a gigabyte was refused; the limit is too tight"
