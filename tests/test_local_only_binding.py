"""The service must refuse to serve an unauthenticated API off loopback."""

from __future__ import annotations

import pytest

from app.main import LOOPBACK_HOSTS, assert_local_only


@pytest.fixture(autouse=True)
def no_token(monkeypatch):
    """No credential configured: the state this module is about."""
    from types import SimpleNamespace

    from app import main as main_module
    monkeypatch.setattr(main_module, "get_settings",
                        lambda: SimpleNamespace(dashboard_token=""))


@pytest.mark.parametrize("host", sorted(LOOPBACK_HOSTS))
def test_loopback_hosts_are_allowed(host):
    assert assert_local_only(host) is None


@pytest.mark.parametrize("host", [
    "0.0.0.0", "::", "192.168.1.10", "10.0.0.4", "example.com", "",
])
def test_every_other_host_is_refused(host):
    with pytest.raises(RuntimeError, match="no authentication"):
        assert_local_only(host)


def test_the_refusal_explains_both_ways_out():
    with pytest.raises(RuntimeError) as caught:
        assert_local_only("0.0.0.0")
    message = str(caught.value)
    assert "loopback" in message
    assert "DASHBOARD_TOKEN" in message
    assert "tunnel" in message


def test_startup_refuses_a_wide_bind(monkeypatch):
    """The guard runs on the real startup path, not just as a helper."""
    from types import SimpleNamespace

    from app import main as main_module

    monkeypatch.setattr(
        main_module, "get_settings",
        lambda: SimpleNamespace(host="0.0.0.0", port=8742, dashboard_token="")
    )
    with pytest.raises(RuntimeError, match="refusing to bind"):
        main_module.run()
