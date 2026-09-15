from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .config import Settings, get_settings
from . import dependency_health
from .security import sanitize_url

ROBLOX_PLACE_RE = re.compile(r"roblox\.com/(?:[a-z]{2}/)?games/(\d+)", re.IGNORECASE)

MAX_CAPTURE_REDIRECTS = 3


class ConnectorError(RuntimeError):
    """A connector refused or failed a request.

    `planned` marks a refusal the run was configured to reach -- a local daily
    allowance being exhausted -- as opposed to something going wrong. Reaching
    a configured allowance is the quota meter working, and recording it as a
    failure marked an otherwise clean run as degraded.
    """

    def __init__(self, message, planned: bool = False):
        super().__init__(message)
        self.planned = planned


def _is_forbidden_address(address: str) -> bool:
    """True for anything that is not a routable public address."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return True
    # An IPv4 address tunnelled inside IPv6 has to be judged on the IPv4 value.
    if getattr(parsed, "ipv4_mapped", None) is not None:
        parsed = parsed.ipv4_mapped
    return bool(
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    )


def assert_public_http_url(url: str) -> None:
    """Refuse anything that is not a public HTTP(S) URL.

    Raises `ConnectorError` for a bad scheme, a host that will not resolve, or
    a host that resolves to *any* private, loopback, link-local, multicast,
    reserved or unspecified address.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConnectorError("only public HTTP(S) pages may be captured")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, None)}
    except socket.gaierror as exc:
        raise ConnectorError("page host cannot be resolved") from exc
    if not addresses:
        raise ConnectorError("page host cannot be resolved")
    # Every resolved address has to be public: one bad answer is enough to
    # reach a private host.
    if any(_is_forbidden_address(address) for address in addresses):
        raise ConnectorError("private network pages are blocked")


@dataclass(frozen=True)
class ConnectorResult:
    url: str
    payload: Any
    content_type: str = "application/json"


class Connectors:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None, quota_meter=None):
        self.settings = settings or get_settings()
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
            headers={"User-Agent": "RobloxVentureAgents/0.1 evidence-research"},
        )
        self._owns_client = client is None
        self.quota_meter = quota_meter
        # Reuses the meter's session factory: anything metering quota already
        # has one, and nothing else needs to grow a parameter for this.
        self.health_factory = getattr(quota_meter, "factory", None)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _json(self, method: str, url: str, **kwargs) -> ConnectorResult:
        if self.quota_meter:
            from .quotas import QuotaExceeded
            try:
                if "api.tavily.com/" in url:
                    self.quota_meter.reserve("tavily", 1)
                elif "googleapis.com/youtube/" in url:
                    self.quota_meter.reserve("youtube", 100 if url.endswith("/search") else 1)
            except QuotaExceeded as exc:
                raise ConnectorError(str(exc), planned=True) from None
        try:
            response = await self.client.request(method, url, **kwargs)
            response.raise_for_status()
            result = ConnectorResult(sanitize_url(str(response.url)), response.json())
        except (httpx.HTTPError, ValueError) as exc:
            # What the dependency was last observed to do, rather than whether
            # a key happens to be configured.
            dependency_health.record(self.health_factory, url, ok=False,
                                     detail=f"{type(exc).__name__} on the last request")
            raise ConnectorError(f"request failed for {sanitize_url(url)}: {type(exc).__name__}") from None
        dependency_health.record(self.health_factory, url, ok=True)
        return result

    async def tavily_search(self, query: str) -> ConnectorResult:
        if not self.settings.tavily_api_key:
            raise ConnectorError("TAVILY_API_KEY is not configured")
        return await self._json(
            "POST",
            "https://api.tavily.com/search",
            json={
                "api_key": self.settings.tavily_api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": self.settings.max_search_results,
                "include_answer": False,
                "include_raw_content": False,
            },
        )

    async def searxng_search(self, query: str) -> ConnectorResult:
        """A local metasearch instance, so discovery needs no third-party key.

        Results are web pages, which makes them discovery-tier like any other
        search: they can point at a game, never testify about one.
        """
        if not self.settings.searxng_enabled:
            raise ConnectorError("local search is disabled")
        return await self._json(
            "GET",
            self.settings.searxng_url.rstrip("/") + "/search",
            params={"q": query, "format": "json"},
        )

    async def roblox_search(self, query: str) -> ConnectorResult:
        """Roblox's own search, which answers with universe IDs directly.

        Undocumented, so it is treated as one source among several rather than
        the only one: if its shape changes, discovery degrades instead of
        stopping.
        """
        if not self.settings.roblox_search_enabled:
            raise ConnectorError("Roblox search is disabled")
        return await self._json(
            "GET",
            "https://apis.roblox.com/search-api/omni-search",
            params={"searchQuery": query, "pageToken": "", "sessionId": "venture-agents"},
        )

    async def universe_for_place(self, place_id: str) -> ConnectorResult:
        return await self._json(
            "GET", f"https://apis.roblox.com/universes/v1/places/{place_id}/universe"
        )

    async def roblox_games(self, universe_ids: list[str]) -> ConnectorResult:
        if not universe_ids:
            raise ConnectorError("no universe IDs supplied")
        ids = ",".join(dict.fromkeys(universe_ids))
        return await self._json(
            "GET", "https://games.roblox.com/v1/games", params={"universeIds": ids}
        )

    async def youtube_search(self, query: str) -> ConnectorResult:
        if not self.settings.youtube_api_key:
            raise ConnectorError("YOUTUBE_API_KEY is not configured")
        return await self._json(
            "GET",
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "key": self.settings.youtube_api_key,
                "part": "snippet",
                "type": "video",
                "maxResults": 25,
                "order": "relevance",
                "q": query,
                "relevanceLanguage": "en",
            },
        )

    async def youtube_videos(self, video_ids: list[str]) -> ConnectorResult:
        if not self.settings.youtube_api_key:
            raise ConnectorError("YOUTUBE_API_KEY is not configured")
        return await self._json(
            "GET",
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "key": self.settings.youtube_api_key,
                "part": "snippet,statistics",
                "id": ",".join(dict.fromkeys(video_ids)),
            },
        )

    async def capture_page(self, url: str) -> ConnectorResult:
        """Capture a public web page.

        Redirects are followed by hand so that *every* hop is checked against
        the private-address rules. Letting httpx follow them would validate
        only the first URL, and a public host that redirects to 127.0.0.1
        would be fetched.

        Residual risk, stated plainly: the hostname is resolved for the check
        and resolved again by the connection, so a DNS entry that flips between
        the two (rebinding) is not caught here. Closing that needs connecting
        to a pinned address, which httpx does not expose cleanly.
        """
        current = url
        for _hop in range(MAX_CAPTURE_REDIRECTS + 1):
            assert_public_http_url(current)
            response = await self.client.get(current, follow_redirects=False)
            if response.is_redirect:
                location = response.headers.get("location", "")
                if not location:
                    raise ConnectorError("redirect without a destination")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise ConnectorError("page exceeds the two-megabyte capture limit")
            content_type = response.headers.get("content-type", "text/html").split(";", 1)[0]
            if content_type not in {"text/html", "text/plain"}:
                raise ConnectorError("only HTML and plain-text pages may be captured")
            text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
            return ConnectorResult(sanitize_url(str(response.url)), text, "text/plain")
        raise ConnectorError("too many redirects while capturing the page")


def extract_roblox_place_ids(search_payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for result in search_payload.get("results", []):
        url = str(result.get("url", ""))
        if match := ROBLOX_PLACE_RE.search(url):
            ids.append(match.group(1))
    return list(dict.fromkeys(ids))


def extract_roblox_universe_ids(search_payload: dict[str, Any]) -> list[str]:
    """Universe IDs straight out of Roblox's own search response.

    Saves a resolution call per result, and a universe ID from Roblox is a
    stronger identifier than one inferred from a URL found on the open web.
    """
    ids: list[str] = []
    for group in search_payload.get("searchResults", []) or []:
        for item in group.get("contents", []) or []:
            universe = item.get("universeId")
            if universe:
                ids.append(str(universe))
    return list(dict.fromkeys(ids))

