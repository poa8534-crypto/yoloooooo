"""Discovery asks several sources, and an analysis is about the game picked.

Both came out of live use: an exhausted third-party allowance ended discovery
for the day, and analysing a game produced a design for whatever niche the run
that first captured it happened to have.
"""

from __future__ import annotations

import pytest

from app.connectors import ConnectorResult, extract_roblox_place_ids, extract_roblox_universe_ids


def test_universe_ids_come_straight_out_of_roblox_search():
    """Saves a resolution call per result, and a universe ID from Roblox is a
    stronger identifier than one scraped from a URL."""
    payload = {"searchResults": [
        {"contents": [{"universeId": 101, "rootPlaceId": 1}, {"universeId": 202, "rootPlaceId": 2}]},
        {"contents": [{"universeId": 101}]},
    ]}
    assert extract_roblox_universe_ids(payload) == ["101", "202"]


def test_a_search_response_with_no_games_yields_nothing():
    assert extract_roblox_universe_ids({"searchResults": []}) == []
    assert extract_roblox_universe_ids({}) == []
    assert extract_roblox_place_ids({"results": [{"url": "https://example.com/x"}]}) == []


def test_only_actual_roblox_hosts_can_supply_game_place_ids():
    from app.connectors import roblox_place_id_from_url

    for url in ("https://evilroblox.com/games/123/Farm",
                "https://roblox.com.evil.example/games/123/Farm",
                "https://search.example/?next=https://www.roblox.com/games/123/Farm"):
        assert roblox_place_id_from_url(url) is None
    assert roblox_place_id_from_url("https://www.roblox.com/en-us/games/123/Farm") == "123"


class Sources:
    """Connectors where each source can be made to fail independently."""

    def __init__(self, working=("roblox_search", "searxng_search", "tavily_search")):
        self.working = set(working)
        self.asked: list[str] = []
        self.queries: dict[str, str] = {}

    async def _answer(self, name, query):
        self.asked.append(name)
        self.queries[name] = query
        if name not in self.working:
            raise RuntimeError(f"{name} unavailable")
        if name == "roblox_search":
            return ConnectorResult("https://apis.roblox.com/search-api/omni-search",
                                   {"searchResults": [{"contents": [{"universeId": 77}]}]})
        return ConnectorResult(f"https://{name}/search",
                               {"results": [{"url": "https://www.roblox.com/games/123/Cozy-Farming"}]})

    async def roblox_search(self, query): return await self._answer("roblox_search", query)
    async def searxng_search(self, query): return await self._answer("searxng_search", query)
    async def tavily_search(self, query): return await self._answer("tavily_search", query)


@pytest.fixture
def run_state(session_factory, settings):
    from app.deep_research import DeepResearch
    from tests.test_deep_research import orchestrator, seed

    run_id, *_ = seed(session_factory)
    orchestra = orchestrator(session_factory)

    def build(sources):
        orchestra.connectors = sources
        return DeepResearch(orchestra, run_id)

    return build


@pytest.mark.asyncio
async def test_every_configured_source_is_asked(run_state):
    sources = Sources()
    deep = run_state(sources)
    universes, places = await deep.search_sources("cozy farming")
    assert sources.asked == ["roblox_search", "searxng_search", "tavily_search"]
    assert universes == ["77"]
    assert places == ["123"]
    assert sources.queries["roblox_search"] == "cozy farming"
    assert sources.queries["searxng_search"] == "site:roblox.com/games cozy farming"
    assert sources.queries["tavily_search"] == sources.queries["searxng_search"]


@pytest.mark.asyncio
async def test_web_site_operator_is_not_sent_to_native_roblox_search(run_state):
    sources = Sources()
    await run_state(sources).search_sources("site:roblox.com/games cozy farming")
    assert sources.queries["roblox_search"] == "cozy farming"
    assert sources.queries["searxng_search"] == "site:roblox.com/games cozy farming"


@pytest.mark.asyncio
async def test_discovery_continues_when_a_source_is_unavailable(run_state):
    """The finding: one exhausted allowance ended discovery for the day."""
    sources = Sources(working=("roblox_search",))
    deep = run_state(sources)
    universes, places = await deep.search_sources("cozy farming")
    assert universes == ["77"]
    assert places == []
    # An absent source is not a failure of the run.
    assert deep.b.state["errors"] == []
    assert [entry["stage"] for entry in deep.b.state["abstentions"]] == [
        "discovery:searxng_search", "discovery:tavily_search"]


@pytest.mark.asyncio
async def test_discovery_fails_only_when_nothing_answers(run_state):
    """Positive control: continuing silently with no results at all would hide
    a total outage."""
    from app.connectors import ConnectorError

    deep = run_state(Sources(working=()))
    with pytest.raises(ConnectorError):
        await deep.search_sources("cozy farming")


@pytest.mark.asyncio
async def test_a_local_source_alone_is_enough(run_state):
    sources = Sources(working=("searxng_search",))
    deep = run_state(sources)
    universes, places = await deep.search_sources("cozy farming")
    assert places == ["123"] and universes == []


@pytest.mark.asyncio
async def test_filled_inspection_budget_stops_further_discovery(run_state):
    from types import SimpleNamespace

    sources = Sources()
    deep = run_state(sources)
    deep.contexts = [SimpleNamespace(candidate_id="one", display_name="Cozy Farm", video_ids=[])]
    deep.b.state["limits"]["universes"] = 1
    deep.b.state["search_queries"] = ["site:roblox.com/games farming"]
    deep.b.state["questions"] = [{"id": "relevance", "state": "open"}]
    assert await deep.investigate() == "candidate_inspection_limit"
    assert sources.asked == [], "no connector search can add a game after the limit"


def test_reserved_universe_budget_also_closes_discovery():
    from app.deep_research import remaining_universe_slots

    assert remaining_universe_slots({"limits": {"universes": 30},
                                     "usage": {"universes": 30}}, []) == 0
    assert remaining_universe_slots({"limits": {"universes": 30},
                                     "usage": {"universes": 5}}, [object()]) == 25


# --- Analyze game is about the game -----------------------------------------


@pytest.mark.asyncio
async def test_analyze_game_is_about_the_game_not_the_capturing_run(session_factory, settings):
    """A game captured by a "Toilet Simulator" run made the model design a
    toilet game from that game's evidence, because the audit inherited the
    run's niche."""
    from tests.test_deep_research import FakeLLM, orchestrator, seed

    _, candidate_id, *_ = seed(session_factory, hunter=False)
    llm = FakeLLM()
    result = await orchestrator(session_factory, llm).audit(candidate_id, operation="analyze_game")

    assert result["operation"] == "analyze_game"
    # The seeded run's niche is "cozy farming"; the game is "Evidence Garden".
    assert result["niche"] == "Evidence Garden", result["niche"]
    assert llm.calls[0]["niche"] == "Evidence Garden"


@pytest.mark.asyncio
async def test_audit_idea_still_inherits_the_runs_niche(session_factory, settings):
    """Positive control: a Hunter proposal was written for that niche, so
    critiquing it in another one would be wrong."""
    from tests.test_deep_research import FakeLLM, orchestrator, seed

    _, candidate_id, _, proposal_id = seed(session_factory)
    llm = FakeLLM()
    result = await orchestrator(session_factory, llm).audit(
        candidate_id, proposal_id, operation="audit_idea")

    assert result["operation"] == "audit_idea"
    assert result["niche"] == "cozy farming"
    assert llm.calls[0]["niche"] == "cozy farming"


# --- the search cache -------------------------------------------------------


def test_a_repeated_search_costs_no_request(session_factory, settings):
    """Engines suspend an instance that queries them in bursts, so the same
    question asked twice must not go out twice."""
    import asyncio

    import httpx

    from app.connectors import Connectors

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"results": [{"url": "https://www.roblox.com/games/1/A"}]})

    class Meter:
        factory = staticmethod(session_factory)

        def reserve(self, provider, cost):
            return None

    async def run():
        fast = settings.model_copy(update={"search_min_interval_seconds": 0})
        connectors = Connectors(fast, httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                                quota_meter=Meter())
        first = await connectors.searxng_search("obby parkour")
        second = await connectors.searxng_search("obby parkour")
        await connectors.close()
        return first, second

    first, second = asyncio.run(run())
    assert calls["n"] == 1, "the same query was sent twice"
    assert second.payload == first.payload


def test_a_different_search_is_not_served_from_the_cache(session_factory, settings):
    """Positive control: a cache that answered everything would be worse than
    none at all."""
    import asyncio

    import httpx

    from app.connectors import Connectors

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"results": []})

    class Meter:
        factory = staticmethod(session_factory)

        def reserve(self, provider, cost):
            return None

    async def run():
        fast = settings.model_copy(update={"search_min_interval_seconds": 0})
        connectors = Connectors(fast, httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                                quota_meter=Meter())
        await connectors.searxng_search("obby parkour")
        await connectors.searxng_search("pet hatching")
        await connectors.close()

    asyncio.run(run())
    assert len(seen) == 2


def test_an_expired_entry_is_a_miss(session_factory, settings):
    """A cached answer is the web as it was. Past the window it is refetched."""
    from datetime import UTC, datetime, timedelta

    from app import search_cache
    from app.models import SystemState

    search_cache.put(session_factory, "searxng_search", "obby", "http://local/search", {"results": []})
    assert search_cache.get(session_factory, "searxng_search", "obby", 3600) is not None
    assert search_cache.get(session_factory, "searxng_search", "other", 3600) is None

    # Age the stored entry rather than passing a zero window, which is caught
    # by a different guard and left the expiry check unexercised.
    with session_factory() as db:
        row = next(r for r in db.query(SystemState).all() if r.key.startswith("search-cache:"))
        row.value_json = {**row.value_json,
                          "at": (datetime.now(UTC) - timedelta(hours=48)).isoformat()}
        db.commit()
    assert search_cache.get(session_factory, "searxng_search", "obby", 3600) is None
    assert search_cache.get(session_factory, "searxng_search", "obby", 86_400 * 7) is not None
