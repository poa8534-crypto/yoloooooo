"""A child process does not outlive its run, and cannot take the machine.

Three failures in one evening, all from one hole: children were started with
`subprocess.run` and nothing owned them afterwards.

Seven orphaned Antigravity processes accumulated across stopped builds, until
a build died with "RuntimeError: can't start new thread". Then a generated
spec looped under Lune -- `task.wait` does nothing there and `task.spawn` is
synchronous, so a `while` loop in a Start never yields -- and two runners grew
to 16GB and 9GB of this machine's 32. The machine went down twice.

The tests that prove the limits bite are themselves bounded. Trusting the
limit to be the only thing between a test and the machine is how it went down
the second time, while this safety net was being written.
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
    """Two Lune runners reached 16GB and 9GB, and the machine went down.

    The allocation here stops at a bound of its own: four gigabytes of
    attempts against a two-gigabyte limit is enough to prove the limit bites,
    and bounded so that a limit which does NOT bite costs four gigabytes and
    a failed assertion rather than the machine.
    """
    if not subprocesses.limits_are_in_force():
        pytest.skip("the job object is not in force here; this must not run without it")

    greedy = (
        "blocks = []\n"
        "for _ in range(64):\n"
        "    blocks.append(bytearray(64 * 1024 * 1024))\n"
        "print('allocated', len(blocks) * 64, 'MB')\n"
    )
    done = subprocesses.run([sys.executable, "-c", greedy], timeout=120)
    assert done.returncode != 0, (
        "a child allocated four gigabytes against a two-gigabyte limit and was not stopped")


@pytest.mark.skipif(sys.platform != "win32", reason="the job object is the Windows answer")
def test_the_limit_is_far_above_what_the_toolchain_uses():
    """The limit must not fail honest work: a whole-project Lune run of 230
    checks sits in the low hundreds of megabytes."""
    modest = "data = bytearray(256 * 1024 * 1024); print(len(data))"
    done = subprocesses.run([sys.executable, "-c", modest], timeout=120)
    assert done.returncode == 0, "a quarter of a gigabyte was refused; the limit is too tight"


@pytest.mark.skipif(sys.platform != "win32", reason="the job object is the Windows answer")
def test_the_limits_are_reported_honestly():
    """Whether the net exists is a fact the test above depends on, so it is
    not allowed to be a guess."""
    assert subprocesses.limits_are_in_force() is True
    assert subprocesses._PROCESS_MEMORY_LIMIT <= 4 * 1024**3, "one child may take too much"
    assert subprocesses._JOB_MEMORY_LIMIT <= 12 * 1024**3, "the children together may take too much"
