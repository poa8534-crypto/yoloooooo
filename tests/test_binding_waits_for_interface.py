"""The service must outlive an interface that is slower to boot than it is.

A power cut took the machine down. It came back, Ollama came back, and the
dashboard did not: Task Scheduler started the service sixteen seconds after
boot, Tailscale had not finished bringing its interface up, and the bind died
with `could not bind on any address out of [('100.107.211.38', 8742)]`. The
service was dead for two hours and nothing said so.

Waiting is only correct for one failure. "That address is not on this machine
yet" resolves itself; "that port is already taken" means another instance is
serving and this one must not sit in a loop hiding it.
"""

from __future__ import annotations

import errno
import socket

import pytest

from app import binding
from app.binding import AddressNeverArrived, address_exists, is_loopback, wait_for_address


class Clock:
    """A clock that only moves when something sleeps on it."""

    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def appears_after(count: int, clock: Clock):
    """An address that turns up on the `count`-th probe."""
    probes = {"n": 0}

    def exists(_host: str) -> bool:
        probes["n"] += 1
        return probes["n"] > count

    return exists, probes


@pytest.fixture
def clock():
    return Clock()


def test_a_late_interface_is_waited_for_rather_than_fatal(clock, monkeypatch):
    exists, probes = appears_after(3, clock)
    monkeypatch.setattr(binding, "address_exists", exists)

    waited = wait_for_address("100.107.211.38", poll=2.0, clock=clock, sleep=clock.sleep)

    assert probes["n"] == 4, "the address was not re-probed after it was absent"
    assert waited == pytest.approx(6.0), "the wait was not reported honestly"


def test_an_address_that_never_arrives_is_not_waited_for_forever(clock, monkeypatch):
    """Without the deadline this loops forever and the service never starts,
    which is worse than the crash it replaced. The sleep is capped so a missing
    deadline fails the test instead of hanging the suite."""
    monkeypatch.setattr(binding, "address_exists", lambda _host: False)

    def sleep(seconds):
        assert len(clock.slept) < 20, "the wait had no deadline"
        clock.sleep(seconds)

    with pytest.raises(AddressNeverArrived, match="100.107.211.38"):
        wait_for_address("100.107.211.38", timeout=10.0, poll=2.0,
                         clock=clock, sleep=sleep)

    assert clock.now <= 12.0, "the wait overran its own timeout"


def test_the_failure_says_how_long_it_waited(clock, monkeypatch):
    monkeypatch.setattr(binding, "address_exists", lambda _host: False)

    with pytest.raises(AddressNeverArrived, match="180 seconds"):
        wait_for_address("100.107.211.38", timeout=180.0, poll=60.0,
                         clock=clock, sleep=clock.sleep)


def test_the_failure_carries_the_errno_that_caused_it(clock, monkeypatch):
    """It is an OSError so a caller that inspects errno keeps working."""
    monkeypatch.setattr(binding, "address_exists", lambda _host: False)

    with pytest.raises(AddressNeverArrived) as failure:
        wait_for_address("100.107.211.38", timeout=0.0, clock=clock, sleep=clock.sleep)

    assert failure.value.errno == errno.EADDRNOTAVAIL


def test_loopback_is_never_waited_for(clock, monkeypatch):
    """Loopback exists before anything else does. A wait here would be a
    startup delay bought for nothing."""
    monkeypatch.setattr(binding, "address_exists",
                        lambda _host: pytest.fail("loopback was probed"))

    assert wait_for_address("127.0.0.1", clock=clock, sleep=clock.sleep) == 0.0
    assert clock.slept == []


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", ""])
def test_every_spelling_of_loopback_skips_the_wait(host):
    assert is_loopback(host)


@pytest.mark.parametrize("host", ["100.107.211.38", "0.0.0.0", "192.168.1.4"])
def test_a_real_interface_is_not_mistaken_for_loopback(host):
    assert not is_loopback(host)


def test_a_port_already_in_use_is_raised_instead_of_retried(monkeypatch):
    """Another instance is already serving. Looping would hide it, and the
    operator would see a service that never starts and never explains why."""
    taken = OSError(errno.EADDRINUSE, "address already in use")
    taken.errno = errno.EADDRINUSE

    class Refusing:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def bind(self, _address): raise taken

    monkeypatch.setattr(binding.socket, "socket", lambda *a, **k: Refusing())

    with pytest.raises(OSError) as failure:
        address_exists("100.107.211.38")

    assert failure.value.errno == errno.EADDRINUSE


@pytest.mark.parametrize("code", sorted(binding.NOT_YET_THERE))
def test_both_platforms_spellings_of_absent_are_recognised(monkeypatch, code):
    """Windows says 10049, POSIX says its own EADDRNOTAVAIL. The service runs
    on the first and the tests run on both."""
    absent = OSError(code, "cannot assign requested address")
    absent.errno = code

    class Absent:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def bind(self, _address): raise absent

    monkeypatch.setattr(binding.socket, "socket", lambda *a, **k: Absent())

    assert address_exists("100.107.211.38") is False


def test_the_probe_does_not_touch_the_port_the_service_wants(monkeypatch):
    """Probing the real port would report "already running" as "not up yet"."""
    bound: list[tuple[str, int]] = []

    class Recording:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def bind(self, address): bound.append(address)

    monkeypatch.setattr(binding.socket, "socket", lambda *a, **k: Recording())

    address_exists("100.107.211.38")

    assert bound == [("100.107.211.38", 0)], "the probe claimed a real port"


def test_the_operator_is_told_once_and_not_once_per_poll(clock, monkeypatch):
    exists, _ = appears_after(5, clock)
    monkeypatch.setattr(binding, "address_exists", exists)
    said: list[str] = []

    wait_for_address("100.107.211.38", poll=1.0, clock=clock, sleep=clock.sleep,
                     on_wait=said.append)

    assert said == ["100.107.211.38"], "the log would fill with the same line"


def test_loopback_really_is_bindable_on_this_machine():
    """No mock. If the probe itself is wrong, everything above tests nothing."""
    assert address_exists("127.0.0.1") is True


def test_an_address_this_machine_does_not_have_reads_as_absent():
    """203.0.113.0/24 is reserved for documentation and is on no interface."""
    assert address_exists("203.0.113.1") is False


def test_run_waits_before_binding(monkeypatch, settings):
    """The wait has to happen in the entrypoint. A correct helper nobody calls
    is what the crash log already looked like."""
    from app import main as main_module

    order: list[str] = []
    monkeypatch.setattr(main_module, "wait_for_address",
                        lambda host, **kw: order.append(f"wait:{host}") or 0.0)
    monkeypatch.setattr(main_module.uvicorn, "run",
                        lambda *a, **kw: order.append(f"bind:{kw['host']}"))

    main_module.run()

    assert order == [f"wait:{settings.host}", f"bind:{settings.host}"]


def test_a_probe_failure_that_is_neither_case_is_not_swallowed(monkeypatch):
    """An unexpected socket error must surface, not read as "not up yet" and
    turn into a three-minute silence."""
    class Broken:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def bind(self, _address): raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(binding.socket, "socket", lambda *a, **k: Broken())

    with pytest.raises(OSError) as failure:
        address_exists("100.107.211.38")

    assert failure.value.errno == errno.EACCES


def test_an_ipv6_host_is_probed_as_ipv6(monkeypatch):
    families: list[int] = []

    class Recording:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def bind(self, _address): return None

    def fake_socket(family, _kind):
        families.append(family)
        return Recording()

    monkeypatch.setattr(binding.socket, "socket", fake_socket)

    address_exists("fd7a:115c:a1e0::1")

    assert families == [socket.AF_INET6], "an IPv6 host was probed on an IPv4 socket"
