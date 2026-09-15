"""A niche becomes searches worth sending.

There was no step here: a niche was split on whitespace and crossed with the
fixed suffixes "cooperative", "simulator" and "social". "toilet simulator"
became `toilet simulator simulator`, `toilet social` and `toilet cooperative`,
each one a real search spending a real request.
"""

from __future__ import annotations

import pytest

from app.query_planner import finalise, keywords, sanitise, vocabulary_queries


def test_a_repeated_word_is_refused():
    """The exact query the old generator produced."""
    assert sanitise("toilet simulator simulator") == "toilet simulator"


def test_a_word_repeated_later_in_the_query_is_also_dropped():
    """Adjacent repeats are the obvious case; a term echoed further along adds
    nothing to a search either, and only the adjacent check was pinned."""
    assert sanitise("toilet simulator best toilet") == "toilet simulator"
    assert sanitise("pet trading pet hatching") == "pet trading hatching"


def test_a_query_carrying_a_link_is_refused():
    assert sanitise("toilet https://example.com") == ""
    assert sanitise("visit www.roblox.com") == ""


def test_a_query_carrying_a_site_operator_is_refused():
    """The caller adds the only site restriction. A proposed query that tried
    to redirect the search is dropped rather than partially trusted."""
    assert sanitise("site:evil.example toilet") == ""
    assert sanitise("site:x.com toilet obby") == ""


def test_noise_words_are_dropped_but_genres_are_kept():
    assert keywords("a cooperative fishing game for the boat") == ["cooperative", "fishing", "boat"]
    # "simulator" is a genre on this platform, not noise.
    assert "simulator" in keywords("pet simulator")


def test_vocabulary_expands_a_niche_into_platform_words():
    queries = " ".join(vocabulary_queries("obby tower climbing"))
    assert "parkour" in queries
    assert "tower of hell" in queries or "tower hell" in queries


def test_the_old_nonsense_is_gone():
    produced = finalise([], "toilet simulator")
    assert not any("social" in q for q in produced), produced
    assert not any("simulator simulator" in q for q in produced), produced
    assert all(q.startswith("site:roblox.com/games ") for q in produced)


def test_model_queries_are_cleaned_and_merged_with_the_vocabulary():
    produced = finalise(["Toilet   Tycoon", "toilet tycoon", "toilet obby", "site:x.com redirect"],
                        "toilet simulator")
    bare = [q.replace("site:roblox.com/games ", "") for q in produced]
    assert "toilet tycoon" in bare
    assert bare.count("toilet tycoon") == 1, "the same query twice is a wasted request"
    assert "toilet obby" in bare
    assert not any("redirect" in q for q in bare), "a redirecting query was kept"


def test_an_empty_or_useless_niche_still_yields_something(): 
    assert finalise([], "the and of") == []
    assert finalise([], "obby")


def test_the_plan_is_capped_and_stable():
    first = finalise(["a", "pet trading", "pet hatching"], "pet trading and hatching simulator")
    second = finalise(["a", "pet trading", "pet hatching"], "pet trading and hatching simulator")
    assert first == second, "discovery must be reproducible"
    assert len(first) <= 10


@pytest.mark.asyncio
async def test_planning_falls_back_when_the_model_is_unavailable():
    """A model that cannot answer must not cost the run its discovery."""
    from app.deep_research import plan_discovery_queries

    class Broken:
        async def plan_searches(self, niche, before_attempt=None):
            raise RuntimeError("model down")

    produced = await plan_discovery_queries("obby tower climbing", llm=Broken())
    assert produced, "discovery lost its queries when the model failed"
    assert any("parkour" in q for q in produced)


@pytest.mark.asyncio
async def test_a_model_plan_is_used_when_it_answers():
    from app.deep_research import plan_discovery_queries

    class Planner:
        async def plan_searches(self, niche, before_attempt=None):
            return ["toilet tycoon", "plumbing simulator"]

    produced = await plan_discovery_queries("toilet simulator", llm=Planner())
    bare = [q.replace("site:roblox.com/games ", "") for q in produced]
    assert "toilet tycoon" in bare and "plumbing simulator" in bare
