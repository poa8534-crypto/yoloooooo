"""Waiting for a network interface that is not up yet.

The service binds the tailnet address rather than loopback, so the dashboard is
reachable from the other machines on the tailnet. At logon that address does
not exist yet. Task Scheduler starts the service about sixteen seconds after
boot and Tailscale has not finished bringing its interface up, so the bind
fails with EADDRNOTAVAIL and uvicorn exits 3.

This is not hypothetical. After a power cut the machine came back, Ollama came
back, and the dashboard did not:

    OSError: could not bind on any address out of [('100.107.211.38', 8742)]

The service had been dead for two hours by the time anyone noticed, and
nothing in the system said so.

Only "that address is not on this machine yet" is worth waiting for. A port
already in use means another instance is running, and this one should exit
rather than sit in a loop hiding a duplicate.
"""

from __future__ import annotations

import errno
import ipaddress
import socket
import time
from collections.abc import Callable

WAIT_SECONDS = 180.0
POLL_SECONDS = 2.0

# Windows raises WSAEADDRNOTAVAIL (10049); POSIX raises EADDRNOTAVAIL, whose
# value differs per platform and comes from `errno`. Both mean the same thing.
NOT_YET_THERE = frozenset({errno.EADDRNOTAVAIL, 10049})


class AddressNeverArrived(OSError):
    """The configured host stayed absent for the whole wait."""


def is_loopback(host: str) -> bool:
    """Loopback and the wildcard always exist, so they are never waited for."""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost", ""}


def _family(host: str) -> int:
    try:
        version = ipaddress.ip_address(host).version
    except ValueError:
        return socket.AF_INET
    return socket.AF_INET6 if version == 6 else socket.AF_INET


def address_exists(host: str) -> bool:
    """Can this machine bind `host` right now?

    Probes on port 0, so the kernel picks an ephemeral port. Probing the real
    port would confuse "the interface is not up" with "the service is already
    running", and only the first of those is worth waiting for.
    """
    with socket.socket(_family(host), socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, 0))
        except OSError as failure:
            if failure.errno in NOT_YET_THERE:
                return False
            raise
    return True


def wait_for_address(
    host: str,
    *,
    timeout: float = WAIT_SECONDS,
    poll: float = POLL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    on_wait: Callable[[str], None] | None = None,
) -> float:
    """Block until `host` is bindable. Returns the seconds spent waiting.

    Raises `AddressNeverArrived` if it never turns up, because starting the
    service on some other address would put the dashboard somewhere nobody is
    looking for it.
    """
    if is_loopback(host):
        return 0.0

    started = clock()
    deadline = started + timeout
    announced = False
    while True:
        if address_exists(host):
            return clock() - started
        if clock() >= deadline:
            raise AddressNeverArrived(
                errno.EADDRNOTAVAIL,
                f"{host} was still not an address on this machine after "
                f"{timeout:.0f} seconds",
            )
        if on_wait is not None and not announced:
            on_wait(host)
            announced = True
        sleep(poll)
