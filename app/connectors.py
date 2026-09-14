from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .config import Settings, get_settings

ROBLOX_PLACE_RE = re.compile(r"roblox\.com/(?:[a-z]{2}/)?games/(\d+)", re.IGNORECASE)


class ConnectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConnectorResult:
    url: str
    payload: Any
    content_type: str = "application/json"


class Connectors:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
            headers={"User-Agent": "RobloxVentureAgents/0.1 evidence-research"},
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _json(self, method: str, url: str, **kwargs) -> ConnectorResult:
        try:
            response = await self.client.request(method, url, **kwargs)
            response.raise_for_status()
            return ConnectorResult(str(response.url), response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise ConnectorError(f"request failed for {url}: {exc}") from exc

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
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConnectorError("only public HTTP(S) pages may be captured")
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, None)}
        except socket.gaierror as exc:
            raise ConnectorError("page host cannot be resolved") from exc
        if any(ipaddress.ip_address(address).is_private or ipaddress.ip_address(address).is_loopback for address in addresses):
            raise ConnectorError("private network pages are blocked")
        response = await self.client.get(url)
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise ConnectorError("page exceeds the two-megabyte capture limit")
        content_type = response.headers.get("content-type", "text/html").split(";", 1)[0]
        if content_type not in {"text/html", "text/plain"}:
            raise ConnectorError("only HTML and plain-text pages may be captured")
        text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
        return ConnectorResult(str(response.url), text, "text/plain")


def extract_roblox_place_ids(search_payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for result in search_payload.get("results", []):
        url = str(result.get("url", ""))
        if match := ROBLOX_PLACE_RE.search(url):
            ids.append(match.group(1))
    return list(dict.fromkeys(ids))

