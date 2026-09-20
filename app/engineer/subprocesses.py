"""Every child process this pipeline starts, tied to the run that started it.

`subprocess.run` is not enough on its own, and the difference cost a build.

Two ways a child outlives its purpose:

1. A timeout. `subprocess.run` kills the process it started and nothing it
   started in turn. `agy` launches Antigravity helpers, so a timed-out model
   call left those helpers running.
2. The parent being killed. Stopping a build with Stop-Process ends the Python
   process and leaves every child it had spawned. Seven orphaned Antigravity
   processes accumulated over an afternoon of stopped builds, and the next
   build died with "RuntimeError: can't start new thread" -- a failure that
   names nothing about its cause and looks exactly like a bug in the run that
   hit it.

On Windows both are answered by a Job Object created with
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: every process assigned to it dies when the
last handle to the job closes, and the handle is held by this process, so it
closes when this process exits -- whether it exits cleanly, crashes, or is
killed. On other platforms the children get their own process group, which
`killpg` ends on a timeout; a parent killed with SIGKILL there still leaves
them, which is a smaller problem on the machines this runs on and is written
here so nobody has to rediscover it.

Nothing is installed for this: ctypes speaks to the Win32 API directly.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading

_WINDOWS = sys.platform == "win32"
_lock = threading.Lock()
_job = None
_job_failed = False

# From Windows' JOBOBJECT_EXTENDED_LIMIT_INFORMATION.
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x0100
_JOBOBJECTEXTENDEDLIMITINFORMATION = 9

#[[
#   What one child may take before Windows refuses it more.
#
#   A behaviour spec generated in a worktree looped -- `task.wait` does
#   nothing under Lune and `task.spawn` is synchronous, so a `while` loop in a
#   Start() never yields -- and two `lune` processes grew to 16GB and 9GB.
#   That is 25 of this machine's 32GB, and the machine went down with them,
#   taking the build, the bridge and the owner's session with it.
#
#   Four gigabytes is far above anything the toolchain legitimately uses: a
#   whole-project Lune run of 230 checks sits in the low hundreds of
#   megabytes, and rojo, selene and luau-lsp are smaller again. A child that
#   reaches this limit is not working, and failing it is cheaper for everyone
#   than letting it take the machine.
#]]
_PROCESS_MEMORY_LIMIT = int(os.environ.get("ENGINEER_CHILD_MEMORY_LIMIT_BYTES") or 4 * 1024**3)
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


def _windows_job():
    """One job for this process, made once and kept.

    Returns None when the job cannot be made, in which case children are
    started normally: leaking a process is bad, and refusing to run the
    pipeline over it would be worse.
    """
    global _job, _job_failed
    with _lock:
        if _job is not None or _job_failed:
            return _job
        try:
            import ctypes
            from ctypes import wintypes

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                            ("WriteOperationCount", ctypes.c_ulonglong),
                            ("OtherOperationCount", ctypes.c_ulonglong),
                            ("ReadTransferCount", ctypes.c_ulonglong),
                            ("WriteTransferCount", ctypes.c_ulonglong),
                            ("OtherTransferCount", ctypes.c_ulonglong)]

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.POINTER(wintypes.ULONG)),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                            ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

            limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            limits.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_PROCESS_MEMORY)
            limits.ProcessMemoryLimit = ctypes.c_size_t(_PROCESS_MEMORY_LIMIT)
            if not kernel32.SetInformationJobObject(
                    handle, _JOBOBJECTEXTENDEDLIMITINFORMATION,
                    ctypes.byref(limits), ctypes.sizeof(limits)):
                raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
            _job = (kernel32, handle)
        except Exception:  # noqa: BLE001 - a missing job is a leak, not a stop
            _job_failed = True
            _job = None
        return _job


def _adopt(pid: int) -> None:
    """Put a started process into this run's job, so it cannot outlive it."""
    job = _windows_job()
    if job is None:
        return
    kernel32, handle = job
    import ctypes

    process = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
    if not process:
        return
    try:
        kernel32.AssignProcessToJobObject(handle, process)
    except Exception:  # noqa: BLE001 - see _windows_job
        pass
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(process))


def _kill_tree(popen: subprocess.Popen) -> None:
    """End the child AND whatever it started.

    On Windows `taskkill /T` walks the tree, which is what `agy` needs: the
    executable named on the command line is a launcher, and the model runs in
    the processes it spawns.
    """
    if _WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(popen.pid)],
                       capture_output=True, check=False)
    else:
        try:
            os.killpg(os.getpgid(popen.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    popen.kill()


def run(command: list[str], *, input: bytes | None = None, cwd: str | None = None,
        timeout: float | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    """`subprocess.run`, with the child tied to this process's lifetime.

    The same shape in and out, so callers need not care: what changes is that
    a timeout takes the whole tree, and a parent that dies takes the tree with
    it.
    """
    creationflags = 0
    start_new_session = False
    if _WINDOWS:
        # Its own group, so a Ctrl-C in a terminal does not race the job.
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        start_new_session = True

    popen = subprocess.Popen(  # noqa: S603 - commands are built by this package
        command, stdin=subprocess.PIPE if input is not None else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env,
        creationflags=creationflags, start_new_session=start_new_session)
    if _WINDOWS:
        _adopt(popen.pid)
    try:
        stdout, stderr = popen.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(popen)
        stdout, stderr = popen.communicate()
        raise subprocess.TimeoutExpired(command, timeout or 0.0, output=stdout, stderr=stderr) from None
    return subprocess.CompletedProcess(command, popen.returncode, stdout, stderr)


__all__ = ["run"]
