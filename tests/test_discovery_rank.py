"""Discovery leads are ordered, not promoted to verified niche matches."""

from app.discovery_rank import leads_from_search, select_diverse


def test_relevant_native_game_after_thirty_noisy_results_is_not_lost():
    payload = {"searchResults": [{"contents": [
        *({"universeId": n, "name": f"Tower Defense {n}"} for n in range(1, 36)),
        {"universeId": 99, "name": "Cozy Farm Together"},
    ]}]}
    leads = leads_from_search(payload, "roblox_search", "cozy farming", "farming")
    assert [lead.entity_id for lead in leads] == ["99"]


def test_search_snippet_and_title_rank_leads_but_are_not_evidence():
    payload = {"results": [
        {"url": "https://www.roblox.com/games/1/Tower-Defense", "title": "Tower Defense"},
        {"url": "https://www.roblox.com/games/2/Farming-and-Friends", "title": "Farming and Friends"},
        {"url": "https://www.roblox.com/games/3/Cosy-Game", "title": "Cosy Game", "content": "Farming together"},
    ]}
    leads = leads_from_search(payload, "searxng_search", "cozy farming", "cozy farming")
    assert [lead.entity_id for lead in leads] == ["2", "3"]
    assert all(lead.kind == "place" for lead in leads)


def test_three_sources_share_limit_and_duplicate_web_links_do_not_inflate_it():
    native = leads_from_search({"searchResults": [{"contents": [
        {"universeId": n, "name": f"Farming {n}"} for n in range(1, 41)
    ]}]}, "roblox_search", "farming", "farming")
    searx = leads_from_search({"results": [
        {"url": f"https://www.roblox.com/games/{n}/Farming", "title": "Farming"}
        for n in range(100, 121)
    ]}, "searxng_search", "farming", "farming")
    tavily = leads_from_search({"results": [
        {"url": f"https://www.roblox.com/games/{n}/Farming", "title": "Farming"}
        for n in range(100, 121)
    ]}, "tavily_search", "farming", "farming")
    selected = select_diverse({"roblox_search": native, "searxng_search": searx,
                               "tavily_search": tavily}, 30)
    assert len(selected) == 30
    assert len({(lead.kind, lead.entity_id) for lead in selected}) == 30
    assert any(lead.source == "roblox_search" for lead in selected)
    assert any(lead.source == "searxng_search" for lead in selected)
    # Each Tavily link is a duplicate in the full SearXNG pool, but only leads
    # already chosen from another source count as repeats.
    assert any(lead.source == "tavily_search" for lead in selected)


def test_other_source_fills_slots_if_one_source_is_empty():
    web = leads_from_search({"results": [
        {"url": f"https://www.roblox.com/games/{n}/Farm", "title": "Farm"}
        for n in range(1, 8)
    ]}, "searxng_search", "farming", "farm")
    assert len(select_diverse({"searxng_search": web}, 5)) == 5


def test_spoofed_search_result_cannot_enter_discovery():
    payload = {"results": [{"url": "https://evilroblox.com/games/9/Cozy-Farm", "title": "Cozy Farm"},
                           {"url": "https://www.roblox.com/games/10/Cozy-Farm", "title": "Cozy Farm"}]}
    leads = leads_from_search(payload, "searxng_search", "cozy farming", "cozy farming")
    assert [lead.entity_id for lead in leads] == ["10"]


def test_generic_query_expansion_does_not_qualify_unrelated_simulators():
    payload = {"searchResults": [{"contents": [
        {"universeId": 1, "name": "Cabin Crew Simulator"},
        {"universeId": 2, "name": "Iron Man Simulator"},
        {"universeId": 3, "name": "Team Tower Defense"},
        {"universeId": 4, "name": "Farming and Friends"},
        {"universeId": 5, "name": "Grow a Garden"},
    ]}]}
    leads = leads_from_search(payload, "roblox_search", "cooperative farming",
                              "cooperative farming simulator")
    assert [lead.entity_id for lead in leads] == ["4", "5"]


def test_a_broad_niche_can_still_find_its_genre():
    payload = {"searchResults": [{"contents": [{"universeId": 1, "name": "Cabin Crew Simulator"}]}]}
    assert [lead.entity_id for lead in leads_from_search(payload, "roblox_search", "simulator", "simulator")] == ["1"]


def test_hunter_does_not_select_unrelated_high_demand_games():
    from types import SimpleNamespace

    from app.deep_research import select_concept_dossiers

    contexts = [SimpleNamespace(candidate_id="flight", display_name="Cabin Crew Simulator", description="Fly a plane."),
                SimpleNamespace(candidate_id="farm", display_name="Chicken Farm", description="Raise chickens together."),
                SimpleNamespace(candidate_id="garden", display_name="Grow a Garden", description="Plant seeds with friends.")]
    dossiers = [{"candidate_id": cid, "universe_id": cid,
                 "facts": [{"template_id": "roblox_playing", "slots": [{"value": ccu}]}]}
                for cid, ccu in (("flight", 100_000), ("farm", 100), ("garden", 50))]
    assert [d["candidate_id"] for d in select_concept_dossiers(dossiers, contexts,
                                                                "cooperative farming")] == ["farm", "garden"]
