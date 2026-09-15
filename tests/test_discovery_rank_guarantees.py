"""What lead ranking is allowed to do, and what it must never do.

Ranking search hits before spending the inspection budget is worth doing: a
single native search can return forty titles and only a handful are worth the
calls. The danger is that the filter is the only thing between a run and an
empty result set, and an empty result set caused by a filter looks exactly
like a niche with no games in it -- every source answered, nothing failed, no
abstention recorded, no candidates found.

These tests fix the boundary. Ranking may reorder and it may trim. It may not
be the reason a run found nothing.
"""

from __future__ import annotations

import inspect
import re

from app import query_planner
from app.discovery_rank import _matches, leads_from_search, select_diverse


def native(*titles):
    return {"searchResults": [{"contents": [
        {"universeId": index, "name": title} for index, title in enumerate(titles, start=1)
    ]}]}


def web(*pairs):
    return {"results": [{"url": f"https://www.roblox.com/games/{pid}/Game", "title": title}
                        for pid, title in pairs]}


def test_a_source_that_answered_is_never_emptied_by_ranking():
    """The failure this guards against was reachable with ordinary input.

    "toilet simulator" is a real niche on this platform and none of the games
    that serve it carry either word. Dropping every unmatched lead meant the
    round captured nothing while reporting that all three sources answered.
    """
    payload = native("Skibidi Battle", "Bathroom Escape Obby", "Plumbing Tycoon",
                     "Brookhaven RP")
    leads = leads_from_search(payload, "roblox_search", "toilet simulator", "toilet simulator")

    assert len(leads) == 4, "ranking discarded a source's entire answer"
    assert {lead.rank for lead in leads} == {0}, "kept leads should rank below any real match"


def test_ranking_still_trims_when_it_has_something_to_go_on():
    """The guarantee above must not turn the ranker off.

    When at least one lead matches the niche, the unrelated ones are still
    dropped rather than allowed to consume inspection slots.
    """
    payload = native(*[f"Tower Defense {n}" for n in range(30)], "Cozy Farm Together")
    leads = leads_from_search(payload, "roblox_search", "cozy farming", "farming")

    assert [lead.title for lead in leads] == ["Cozy Farm Together"]


def test_web_results_are_not_emptied_either():
    payload = web(("1", "Skibidi Battle"), ("2", "Brookhaven RP"))
    leads = leads_from_search(payload, "searxng_search", "toilet simulator", "toilet simulator")

    assert [lead.entity_id for lead in leads] == ["1", "2"]
    assert all(lead.kind == "place" for lead in leads)


def test_short_genre_words_reach_their_inflections():
    """The curated vocabulary is built on words like "pet", "obby" and "rp".

    A fixed four-character comparison window could never match any of them, so
    the terms the planner works hardest to produce were the terms ranking was
    blind to.
    """
    assert _matches("pet", "pets")
    assert _matches("obby", "obbies") is False, "not an inflection this rule can see"
    assert _matches("farm", "farming")
    assert _matches("fish", "fishing")
    assert _matches("simulator", "simulators")


def test_words_that_merely_start_alike_are_not_treated_as_the_same_word():
    for term, word in (("tower", "towel"), ("trade", "tradition"), ("horror", "horse")):
        assert not _matches(term, word), f"{term!r} should not match {word!r}"


def test_an_unrelated_title_still_loses_to_a_related_one():
    payload = native("Tower Defense Simulator", "Cozy Farm Together")
    leads = leads_from_search(payload, "roblox_search", "cozy farming", "cozy farming")

    assert leads[0].title == "Cozy Farm Together"


def test_a_lead_with_no_metadata_is_kept_rather_than_judged():
    """Roblox occasionally answers with an id and nothing else. Absent
    metadata is not evidence of irrelevance, so the lead is ranked last rather
    than discarded."""
    leads = leads_from_search({"searchResults": [{"contents": [{"universeId": 5}]}]},
                              "roblox_search", "cozy farming", "farming")

    assert [lead.entity_id for lead in leads] == ["5"]


def test_a_zero_budget_selects_nothing_rather_than_raising():
    leads = leads_from_search(native("Cozy Farm"), "roblox_search", "farming", "farming")

    assert select_diverse({"roblox_search": leads}, 0) == []


def test_the_native_search_strip_matches_the_prefix_the_planner_adds():
    """Two modules agree on one string by coincidence today.

    `query_planner.finalise` writes the site restriction and `search_sources`
    removes it again before asking Roblox, which does not understand web
    operators. They are coupled by a literal, so the coupling is asserted.
    """
    from app import deep_research

    prefix = inspect.signature(query_planner.finalise).parameters["prefix"].default
    source = inspect.getsource(deep_research.DeepResearch.search_sources)
    pattern = re.search(r'''re\.sub\(r?["']([^"']+)["']''', source)

    assert pattern, "search_sources no longer strips the prefix"
    planned = query_planner.finalise([], "cozy farming")[0]
    assert planned.startswith(prefix)
    assert re.sub(pattern.group(1), "", planned).strip() == "cozy farming", (
        "the planner's prefix and the strip in search_sources have diverged; "
        "Roblox's native search would receive a site: operator"
    )


def test_the_per_source_diagnostics_reach_the_report(session_factory, settings):
    """Recorded and never read is the same as not recorded.

    `search_sources` writes how many leads each source produced and how many
    survived. Without it, a source that answered with nothing usable is
    indistinguishable in the UI from a source that was never asked.
    """
    from app.models import ResearchRun
    from app.research_budget import RunBudget, progress
    from tests.test_deep_research import seed

    run_id, *_ = seed(session_factory)
    budget = RunBudget(session_factory, run_id)
    recorded = [{"source": "searxng_search", "query": "site:roblox.com/games cozy farming",
                 "usable_leads": 0, "selected_leads": 0, "engine_errors": 5}]
    budget.save(discovery_sources=recorded)

    with session_factory() as db:
        reported = progress(db, db.get(ResearchRun, run_id))

    assert reported["discovery_sources"] == recorded, (
        "the diagnostics never left the checkpoint"
    )
