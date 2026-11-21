"""Observed base rates for a game idea, by genre, backtested.

A game that does not exist yet has no player count, so nothing in
`success_model` applies to it. What the ledger can honestly say about an idea
is how often games *like it* -- same Roblox genre, recently created -- went on
to reach a target, and how well that kind of rate has predicted later games.

The cohort for one idea definition (see `config/success_definitions.json`):

* games whose creation date is known (`game_snapshots`),
* first seen by the census within `young_days` of creation,
* whose whole `horizon_days` after creation has passed.

Success is the highest player count the census recorded inside those days.
Two biases are stated in every answer rather than corrected for:

* **Noticed games only.** Most of the cohort was found on Roblox's own
  shelves, so it is "given the game got noticed", not "given it was published".
  Millions of games are published; almost none appear here.
* **Peaks are recorded low.** The census misses nights and stops watching a
  shelf game once it leaves the shelf, so a peak that happened unobserved is
  missed. Rates are lower bounds on reach among noticed games.

The rate for a genre is shrunk towards the all-genre rate (a Beta prior worth
`PRIOR_STRENGTH` games), so a genre with four games in it does not report 0 %
or 100 %. The backtest compares that against the plain all-genre rate on games
created after the split, and the answer says whether genre helped.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
from scipy.stats import beta as beta_distribution

from .success_model import Census, _bootstrap_share_better, definitions, evaluation_settings

IDEA_VERSION = "idea-rates-v1"
# How many games' worth of weight the all-genre rate carries in a genre's rate.
PRIOR_STRENGTH = 10.0


def idea_definition(key: str | None = None) -> dict:
    entries = definitions().get("idea") or []
    if not entries:
        raise KeyError("no idea success definitions are configured")
    if key is None:
        return entries[0]
    for entry in entries:
        if entry["key"] == key:
            return entry
    raise KeyError(f"unknown idea definition: {key}")


def cohort(census: Census, definition: dict, as_of: float | None = None) -> list[dict]:
    """Every game that qualifies, with its observed outcome.

    `as_of` (hours since epoch) restricts the view to what was known then:
    only observations up to it, and only games whose horizon had passed by it.
    """
    as_of = census.last if as_of is None else as_of
    if as_of is None:
        return []
    young = float(definition["young_days"]) * 24
    horizon = float(definition["horizon_days"]) * 24
    target = float(definition["threshold_ccu"])
    members = []
    for universe, created in census.created.items():
        series = census.series.get(universe)
        if series is None or created + horizon > as_of:
            continue
        seen = series.t[series.t <= as_of]
        if len(seen) == 0 or seen[0] - created > young or seen[0] < created:
            continue
        inside = (series.t >= created) & (series.t <= created + horizon) & (series.t <= as_of)
        if not inside.any():
            continue
        peak = float(series.ccu[inside].max())
        genre_l1, genre_l2 = census.genre.get(universe, ("", ""))
        members.append({"universe_id": universe, "created": created, "genre_l1": genre_l1,
                        "genre_l2": genre_l2, "peak_ccu": peak,
                        "reached": peak >= target})
    return members


def _posterior(successes: int, games: int, prior_rate: float, interval: float) -> dict:
    a = PRIOR_STRENGTH * prior_rate + successes
    b = PRIOR_STRENGTH * (1 - prior_rate) + (games - successes)
    a, b = max(a, 1e-6), max(b, 1e-6)
    tail = (1 - interval) / 2
    return {"rate": round(a / (a + b), 4),
            "interval": [round(float(beta_distribution.ppf(tail, a, b)), 4),
                         round(float(beta_distribution.ppf(1 - tail, a, b)), 4)],
            "interval_level": interval}


def _rates(members: list[dict], key: str, interval: float) -> tuple[float, dict]:
    overall = sum(m["reached"] for m in members) / len(members)
    by: dict[str, list[dict]] = {}
    for member in members:
        by.setdefault(member[key] or "(none)", []).append(member)
    rates = {}
    for name, group in by.items():
        successes = sum(m["reached"] for m in group)
        rates[name] = {"games": len(group), "reached": successes,
                       "observed_share": round(successes / len(group), 4),
                       **_posterior(successes, len(group), overall, interval)}
    return overall, rates


def backtest(census: Census, definition: dict, settings: dict) -> dict:
    """Does a genre's past rate predict later games better than the all-genre rate?"""
    members = cohort(census, definition)
    out = {"version": IDEA_VERSION, "definition": definition["key"],
           "cohort": len(members)}
    if len(members) < 3:
        return out | {"state": "insufficient_data",
                      "reason": f"{len(members)} games in the cohort"}
    created = np.array(sorted(m["created"] for m in members))
    split = float(created[int(math.floor((1 - float(settings.get("test_fraction", 0.3)))
                                          * len(created)))])
    horizon = float(definition["horizon_days"]) * 24
    # Train on what was knowable at the split: games whose horizon had ended.
    train = cohort(census, definition, as_of=split)
    test = [m for m in members if m["created"] >= split]
    need = int(settings.get("min_test", 100))
    minimum = int(settings.get("idea_min_cohort", 30))
    out |= {"train": len(train), "test": len(test),
            "split_at": datetime.fromtimestamp(split * 3600, UTC).isoformat(),
            "horizon_hours": horizon}
    problems = []
    if len(train) < minimum:
        problems.append(f"{len(train)} training games, {minimum} required")
    if len(test) < min(need, minimum):
        problems.append(f"{len(test)} test games, {min(need, minimum)} required")
    if problems:
        return out | {"state": "insufficient_data", "reason": "; ".join(problems),
                      "note": "The cohort grows by itself as the census keeps running; "
                              "each game needs its whole horizon to pass before it counts."}
    interval = float(settings.get("idea_interval", 0.9))
    overall, by_genre = _rates(train, "genre_l1", interval)
    y = np.array([1.0 if m["reached"] else 0.0 for m in test])
    p_overall = np.full(len(test), overall)
    p_genre = np.array([by_genre.get(m["genre_l1"] or "(none)", {}).get("rate", overall)
                        for m in test])
    brier_overall = float(np.mean((p_overall - y) ** 2))
    brier_genre = float(np.mean((p_genre - y) ** 2))
    # Each test game is its own resampling block.
    share = _bootstrap_share_better(np.arange(len(test), dtype=float), y, p_genre, p_overall,
                                    int(settings.get("bootstrap_rounds", 500)))
    needed = float(settings.get("bootstrap_confidence", 0.95))
    helps = share >= needed
    return out | {
        "state": "measured",
        "brier_all_genre_rate": round(brier_overall, 5),
        "brier_genre_rate": round(brier_genre, 5),
        "bootstrap_share_genre_better": round(share, 3),
        "genre_helps": helps,
        "test_share_reached": round(float(y.mean()), 4),
        "reason": (f"genre rates predicted later games better than the all-genre rate in "
                   f"{share:.0%} of resamples" if helps else
                   f"genre rates were better in only {share:.0%} of resamples "
                   f"({needed:.0%} required): treat the genre figure as no more informative "
                   f"than the all-genre rate"),
    }


def for_idea(census: Census, genre_l1: str, genre_l2: str = "",
             definition_key: str | None = None, comparables: list[str] | None = None) -> dict:
    """The observed rate for games like this idea, with its interval and its track record."""
    definition = idea_definition(definition_key)
    settings = evaluation_settings()
    interval = float(settings.get("idea_interval", 0.9))
    minimum = int(settings.get("idea_min_cohort", 30))
    members = cohort(census, definition)
    out = {"version": IDEA_VERSION, "definition": definition["key"],
           "label": definition["label"], "genre_l1": genre_l1, "genre_l2": genre_l2,
           "cohort": len(members), "probability": None,
           "caveats": [
               "Conditional on the game being noticed (mostly: reaching a Roblox shelf). "
               "It is not the chance for any published game, which is far lower.",
               "Peaks are recorded low: the census misses nights and stops watching a "
               "game once it leaves the shelves.",
               "A base rate for games like this, not a forecast for one game. Execution, "
               "marketing and timing are not in the data.",
           ]}
    if len(members) < minimum:
        return out | {"state": "insufficient_data",
                      "reason": f"{len(members)} games in the cohort, {minimum} required"}
    overall, by_genre = _rates(members, "genre_l1", interval)
    genre = by_genre.get(genre_l1)
    out["all_genres"] = {"games": len(members), "rate": round(overall, 4)}
    if genre is None:
        out |= {"state": "no_genre_data",
                "reason": f"no game in the cohort has genre '{genre_l1}'; the all-genre "
                          f"rate is the only figure the data supports",
                "known_genres": sorted(by_genre)}
    else:
        out |= {"state": "measured", "genre": genre, "probability": genre["rate"],
                "reason": f"{genre['reached']} of {genre['games']} noticed young "
                          f"{genre_l1} games reached the target, shrunk towards the "
                          f"all-genre rate ({overall:.1%})"}
        if genre_l2:
            sub = [m for m in members if m["genre_l1"] == genre_l1 and m["genre_l2"] == genre_l2]
            if sub:
                hits = sum(m["reached"] for m in sub)
                out["subgenre"] = {"games": len(sub), "reached": hits,
                                   **_posterior(hits, len(sub), genre["rate"], interval)}
    if comparables:
        lookup = {m["universe_id"]: m for m in members}
        out["comparables"] = [
            {"universe_id": str(c), "in_cohort": str(c) in lookup,
             "peak_ccu": lookup[str(c)]["peak_ccu"] if str(c) in lookup else None,
             "reached": lookup[str(c)]["reached"] if str(c) in lookup else None}
            for c in comparables]
    out["track_record"] = backtest(census, definition, settings)
    return out
