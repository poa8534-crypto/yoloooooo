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


class Sources:
    """Connectors where each source can be made to fail independently."""

    def __init__(self, working=("roblox_search", "searxng_search", "tavily_search")):
        self.working = set(working)
        self.asked: list[str] = []

    async def _answer(self, name, query):
        self.asked.append(name)
        if name not in self.working:
            raise RuntimeError(f"{name} unavailable")
        if name == "roblox_search":
            return ConnectorResult("https://apis.roblox.com/search-api/omni-search",
                                   {"searchResults": [{"contents": [{"universeId": 77}]}]})
        return ConnectorResult(f"https://{name}/search",
                               {"results": [{"url": "https://www.roblox.com/games/123/Test"}]})

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
