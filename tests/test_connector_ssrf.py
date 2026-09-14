"""Page capture must refuse private hosts, including via redirects."""

from __future__ import annotations

import httpx
import pytest

from app.connectors import (
    MAX_CAPTURE_REDIRECTS,
    ConnectorError,
    Connectors,
    _is_forbidden_address,
    assert_public_http_url,
)


@pytest.fixture
def public_dns(monkeypatch):
    """Resolve every host to a public address unless the name says otherwise."""
    def fake_getaddrinfo(host, *_args, **_kwargs):
        mapping = {
            "internal.example": "10.1.2.3",
            "localhost": "127.0.0.1",
            "metadata.example": "169.254.169.254",
            "rebind.example": "203.0.113.9",
        }
        address = mapping.get(host, "93.184.216.34")
        return [(2, 1, 6, "", (address, 0))]

    monkeypatch.setattr("app.connectors.socket.getaddrinfo", fake_getaddrinfo)


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254",
    "::1", "::ffff:127.0.0.1", "224.0.0.1", "0.0.0.0", "not-an-address",
])
def test_non_public_addresses_are_refused(address):
    assert _is_forbidden_address(address) is True


@pytest.mark.parametrize("address", ["8.8.8.8", "93.184.216.34", "2606:4700::1111"])
def test_public_addresses_are_allowed(address):
    assert _is_forbidden_address(address) is False


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com/x", "gopher://example.com",
    "http://", "not a url",
])
def test_only_http_urls_are_captured(url, public_dns):
    with pytest.raises(ConnectorError, match="only public HTTP"):
        assert_public_http_url(url)


def test_a_private_host_is_refused_outright(public_dns):
    with pytest.raises(ConnectorError, match="private network"):
        assert_public_http_url("http://internal.example/admin")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


@pytest.mark.asyncio
async def test_a_redirect_to_a_private_host_is_blocked(public_dns):
    """The original host is public; the redirect target is not."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://internal.example/secrets"})
        return httpx.Response(200, text="should never be reached")

    connectors = Connectors(client=_client(handler))
    with pytest.raises(ConnectorError, match="private network"):
        await connectors.capture_page("http://public.example/start")
    # The first hop was fetched; the private hop never was.
    assert seen == ["http://public.example/start"]


@pytest.mark.asyncio
async def test_a_redirect_to_the_local_service_is_blocked(public_dns):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "public.example":
            return httpx.Response(301, headers={"location": "http://localhost:8742/api/health"})
        raise AssertionError("the loopback hop must never be requested")

    connectors = Connectors(client=_client(handler))
    with pytest.raises(ConnectorError, match="private network"):
        await connectors.capture_page("http://public.example/start")


@pytest.mark.asyncio
async def test_a_redirect_to_cloud_metadata_is_blocked(public_dns):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://metadata.example/latest/meta-data/"})
        raise AssertionError("the metadata hop must never be requested")

    connectors = Connectors(client=_client(handler))
    with pytest.raises(ConnectorError, match="private network"):
        await connectors.capture_page("http://public.example/start")


@pytest.mark.asyncio
async def test_a_public_redirect_chain_is_followed(public_dns):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://other.example/final"})
        return httpx.Response(
            200, text="<html><body>Captured body</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )

    connectors = Connectors(client=_client(handler))
    result = await connectors.capture_page("http://public.example/start")
    assert result.payload == "Captured body"
    assert result.content_type == "text/plain"


@pytest.mark.asyncio
async def test_a_redirect_loop_terminates(public_dns):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://public.example/loop"})

    connectors = Connectors(client=_client(handler))
    with pytest.raises(ConnectorError, match="too many redirects"):
        await connectors.capture_page("https://public.example/loop")


@pytest.mark.asyncio
async def test_oversized_and_wrong_type_pages_are_refused(public_dns):
    def big(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 2_000_001, headers={"content-type": "text/html"})

    def binary(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"})

    with pytest.raises(ConnectorError, match="two-megabyte"):
        await Connectors(client=_client(big)).capture_page("https://public.example/big")
    with pytest.raises(ConnectorError, match="HTML and plain-text"):
        await Connectors(client=_client(binary)).capture_page("https://public.example/doc")


def test_the_redirect_budget_is_bounded():
    assert 1 <= MAX_CAPTURE_REDIRECTS <= 5
