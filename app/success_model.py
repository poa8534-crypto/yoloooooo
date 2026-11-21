"""Backtested probabilities for questions about existing games.

Replaces the in-sample band table in `calibration_outcomes` as the source of
any probability the dashboard shows. That table was measured honestly but asked
a circular question: its score is mostly today's player count and its outcome
was "has many players tomorrow", so a rule as dumb as "already above the
target" scored 89.7 % on it. Here every question is judged out of sample and
next to the baselines it has to beat.

How it works, for one success definition (see `config/success_definitions.json`):

1. **Examples.** Cutoffs are census capture times at least `cutoff_step_hours`
   apart. For every game observed at a cutoff, features are computed from rows
   at or before the cutoff only, and the label from rows inside the horizon
   after it. A game whose own observations cover less than
   `min_window_coverage` of the census captures in its window is skipped:
   the census only sees shelf games while they are shelved, and an unobserved
   stretch is not a stretch below the target.
2. **Chronological split.** Training examples are those whose whole horizon
   ended before the split time; test examples start at or after it. Nothing
   in the test period is visible to the fit.
3. **Baselines.** Every model is compared with (a) the training base rate and
   (b) a model that sees only today's player count. A probability is shown
   only when the full model beats both on held-out Brier score.

What it does not do: forecast a game that does not exist. That is
`app.idea_rates`, which reports observed base rates and is just as strict.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
from sqlalchemy import func, select

from .market_pulse import TRACKED_SORT
from .models import GameSnapshot, MarketSample

log = logging.getLogger("venture-agents")

MODEL_VERSION = "success-model-v1"
DEFINITIONS_PATH = Path(__file__).resolve().parent.parent / "config" / "success_definitions.json"

# Feature order is frozen per MODEL_VERSION. Every feature is computed from
# rows at or before the cutoff.
FEATURES = ("log_ccu", "mom24", "mom24_missing", "mom72", "mom72_missing",
            "approval", "approval_missing", "log_votes", "on_shelf",
            "log_age_days", "age_missing")
# A reference observation for a 24 h or 72 h change must be this close to
# exactly 24 h or 72 h earlier. Comparing the same time of day removes the
# daily cycle, which otherwise dominates any short-span rate.
REFERENCE_TOLERANCE_HOURS = 1.5

SKILL = "skill"
NO_SKILL = "no_skill"
INSUFFICIENT = "insufficient_data"


# --------------------------------------------------------------------------
# Definitions (data, not code)
# --------------------------------------------------------------------------

@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def definitions(path: Path | None = None) -> dict:
    return _load(str(path or DEFINITIONS_PATH))


def evaluation_settings(path: Path | None = None) -> dict:
    return dict(definitions(path).get("evaluation") or {})


def game_definition(key: str | None = None, path: Path | None = None) -> dict:
    entries = definitions(path).get("game") or []
    if not entries:
        raise KeyError("no game success definitions are configured")
    if key is None:
        return entries[0]
    for entry in entries:
        if entry["key"] == key:
            return entry
    raise KeyError(f"unknown success definition: {key}")


# --------------------------------------------------------------------------
# Loading the census once, as arrays
# --------------------------------------------------------------------------

def _hours(moment: datetime) -> float:
    moment = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return moment.timestamp() / 3600.0


@dataclass
class Series:
    t: np.ndarray        # hours since epoch, one per capture, ascending
    ccu: np.ndarray
    up: np.ndarray
    down: np.ndarray
    shelf: np.ndarray    # 1.0 when any non-tracked shelf carried it at that capture


@dataclass
class Census:
    captures: np.ndarray                       # every capture time, ascending
    series: dict[str, Series] = field(default_factory=dict)
    created: dict[str, float] = field(default_factory=dict)  # hours since epoch
    genre: dict[str, tuple[str, str]] = field(default_factory=dict)

    @property
    def last(self) -> float | None:
        return float(self.captures[-1]) if len(self.captures) else None


def load_census(db) -> Census:
    """Every census row, collapsed to one point per game per capture.

    Streamed one game at a time and packed into numpy arrays as it goes. The
    ledger holds well over a million rows; materialising them as Python
    objects cost the service gigabytes on a PC that was already short of
    commit memory.
    """
    result = db.execute(
        select(MarketSample.universe_id, MarketSample.captured_at,
               MarketSample.player_count, MarketSample.up_votes,
               MarketSample.down_votes, MarketSample.sort_id)
        .order_by(MarketSample.universe_id, MarketSample.captured_at)
        .execution_options(yield_per=20_000)
    )
    captures: set[float] = set()
    series: dict[str, Series] = {}
    current: str | None = None
    points: dict[float, list] = {}

    def flush() -> None:
        if current is None or not points:
            return
        times = sorted(points)
        values = np.array([points[moment] for moment in times], dtype=float)
        series[current] = Series(np.array(times), values[:, 0], values[:, 1],
                                 values[:, 2], values[:, 3])

    for universe, captured, ccu, up, down, sort_id in result:
        if universe != current:
            flush()
            current, points = universe, {}
        moment = _hours(captured)
        captures.add(moment)
        shelf = 0.0 if sort_id == TRACKED_SORT else 1.0
        point = points.get(moment)
        if point is None:
            points[moment] = [ccu, up, down, shelf]
        else:
            point[3] = max(point[3], shelf)
    flush()
    census = Census(captures=np.array(sorted(captures)), series=series)
    created = db.execute(
        select(GameSnapshot.universe_id, func.min(GameSnapshot.created))
        .where(GameSnapshot.created.is_not(None))
        .group_by(GameSnapshot.universe_id)
    ).all()
    for universe, moment in created:
        if moment is not None:
            census.created[universe] = _hours(moment)
    genres = db.execute(
        select(GameSnapshot.universe_id, GameSnapshot.genre_l1, GameSnapshot.genre_l2)
        .order_by(GameSnapshot.captured_at)
    ).all()
    for universe, genre_l1, genre_l2 in genres:
        census.genre[universe] = (genre_l1 or "", genre_l2 or "")
    return census


# --------------------------------------------------------------------------
# Features and labels
# --------------------------------------------------------------------------

def _at_or_before(series: Series, moment: float, within: float) -> int | None:
    index = int(np.searchsorted(series.t, moment, side="right")) - 1
    if index < 0 or moment - series.t[index] > within:
        return None
    return index


def _nearest(series: Series, moment: float, tolerance: float) -> int | None:
    index = int(np.searchsorted(series.t, moment))
    best = None
    for candidate in (index - 1, index):
        if 0 <= candidate < len(series.t):
            gap = abs(series.t[candidate] - moment)
            if gap <= tolerance and (best is None or gap < abs(series.t[best] - moment)):
                best = candidate
    return best


def features_at(series: Series, index: int, created: float | None) -> list[float]:
    """The frozen feature vector for one game at one observation."""
    now = float(series.t[index])
    ccu = float(series.ccu[index])
    log_ccu = math.log1p(ccu)
    values = {"log_ccu": log_ccu}
    for name, hours in (("mom24", 24.0), ("mom72", 72.0)):
        reference = _nearest(series, now - hours, REFERENCE_TOLERANCE_HOURS)
        if reference is None:
            values[name], values[f"{name}_missing"] = 0.0, 1.0
        else:
            values[name] = log_ccu - math.log1p(float(series.ccu[reference]))
            values[f"{name}_missing"] = 0.0
    votes = float(series.up[index] + series.down[index])
    if votes > 0:
        values["approval"], values["approval_missing"] = float(series.up[index]) / votes, 0.0
    else:
        values["approval"], values["approval_missing"] = 0.5, 1.0
    values["log_votes"] = math.log1p(votes)
    values["on_shelf"] = float(series.shelf[index])
    if created is not None and created <= now:
        values["log_age_days"], values["age_missing"] = math.log1p((now - created) / 24.0), 0.0
    else:
        values["log_age_days"], values["age_missing"] = 0.0, 1.0
    return [values[name] for name in FEATURES]


def applies(definition: dict, ccu_now: float) -> bool:
    """Whether the question can be asked of a game at this player count."""
    kind = definition.get("kind", "threshold")
    if kind == "threshold":
        return not (definition.get("require_below_at_cutoff")
                    and ccu_now >= float(definition["threshold_ccu"]))
    if kind == "ratio":
        return ccu_now >= float(definition.get("min_ccu_at_cutoff", 1))
    if kind == "persist":
        return ccu_now >= float(definition["threshold_ccu"])
    raise ValueError(f"unknown success definition kind: {kind}")


def _label(definition: dict, ccu_now: float, window: np.ndarray,
           window_t: np.ndarray, cutoff: float) -> bool | None:
    """True/False for an eligible example, None when the question does not apply."""
    if not applies(definition, ccu_now):
        return None
    kind = definition.get("kind", "threshold")
    horizon = float(definition["horizon_hours"])
    if kind == "threshold":
        return bool(window.max() >= float(definition["threshold_ccu"]))
    if kind == "ratio":
        return bool(window.max() >= float(definition["ratio"]) * ccu_now)
    if kind == "persist":
        target = float(definition["threshold_ccu"])
        # "Still there at the end": the observations in the last sixth of
        # the horizon, which for seven days is the final 28 hours.
        tail = window[window_t > cutoff + horizon * 5 / 6]
        if len(tail) == 0:
            return None
        return bool(tail.max() >= target)
    raise ValueError(f"unknown success definition kind: {kind}")


@dataclass
class Examples:
    cutoffs: np.ndarray
    X: np.ndarray
    y: np.ndarray
    universes: list[str]
    skipped_low_coverage: int = 0


def build_examples(census: Census, definition: dict, settings: dict) -> Examples:
    horizon = float(definition["horizon_hours"])
    step = float(settings.get("cutoff_step_hours", 24))
    fresh = float(settings.get("fresh_observation_hours", 1.0))
    min_cover = float(settings.get("min_window_coverage", 0.5))
    captures = census.captures
    if len(captures) == 0:
        return Examples(np.array([]), np.zeros((0, len(FEATURES))), np.array([]), [])
    last = float(captures[-1])
    cutoffs: list[float] = []
    for moment in captures:
        if moment + horizon > last:
            break
        if not cutoffs or moment - cutoffs[-1] >= step:
            cutoffs.append(float(moment))
    rows, labels, when, who = [], [], [], []
    skipped = 0
    for cutoff in cutoffs:
        lo = int(np.searchsorted(captures, cutoff, side="right"))
        hi = int(np.searchsorted(captures, cutoff + horizon, side="right"))
        census_in_window = hi - lo
        if census_in_window == 0:
            continue
        for universe, series in census.series.items():
            index = _at_or_before(series, cutoff, fresh)
            if index is None:
                continue
            start = index + 1
            end = int(np.searchsorted(series.t, cutoff + horizon, side="right"))
            if end <= start:
                skipped += 1
                continue
            if (end - start) / census_in_window < min_cover:
                skipped += 1
                continue
            label = _label(definition, float(series.ccu[index]), series.ccu[start:end],
                           series.t[start:end], cutoff)
            if label is None:
                continue
            rows.append(features_at(series, index, census.created.get(universe)))
            labels.append(1.0 if label else 0.0)
            when.append(cutoff)
            who.append(universe)
    X = np.array(rows, dtype=float) if rows else np.zeros((0, len(FEATURES)))
    return Examples(np.array(when), X, np.array(labels), who, skipped)


# --------------------------------------------------------------------------
# Models and metrics
# --------------------------------------------------------------------------

def _fit(X: np.ndarray, y: np.ndarray):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))
    model.fit(X, y)
    return model


def _metrics(y: np.ndarray, p: np.ndarray) -> dict:
    from sklearn.metrics import roc_auc_score

    p = np.clip(p, 1e-6, 1 - 1e-6)
    brier = float(np.mean((p - y) ** 2))
    log_loss = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    auc = float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else None
    return {"brier": round(brier, 5), "log_loss": round(log_loss, 5),
            "auc": round(auc, 4) if auc is not None else None}


def reliability(y: np.ndarray, p: np.ndarray, bins: int) -> list[dict]:
    """Predicted against observed, per probability bin. Empty bins have no rate."""
    table = []
    for number in range(bins):
        low, high = number / bins, (number + 1) / bins
        inside = (p >= low) & ((p < high) if number < bins - 1 else (p <= high))
        count = int(inside.sum())
        table.append({
            "from": round(low, 3), "to": round(high, 3), "games": count,
            "reached": int(y[inside].sum()) if count else 0,
            "mean_predicted": round(float(p[inside].mean()), 4) if count else None,
            "observed_rate": round(float(y[inside].mean()), 4) if count else None,
        })
    return table


def _bootstrap_share_better(days: np.ndarray, y: np.ndarray, p_model: np.ndarray,
                            p_baseline: np.ndarray, rounds: int) -> float:
    """Share of day-block resamples in which the model's Brier score is lower.

    Examples from the same cutoff share that day's market, so they are
    resampled together; resampling single examples would overstate certainty.
    A fixed seed keeps the verdict reproducible from the same ledger.
    """
    blocks = [np.flatnonzero(days == day) for day in np.unique(days)]
    if len(blocks) < 2 or rounds <= 0:
        return 0.0
    loss_model = (p_model - y) ** 2
    loss_base = (p_baseline - y) ** 2
    model_sums = np.array([loss_model[block].sum() for block in blocks])
    base_sums = np.array([loss_base[block].sum() for block in blocks])
    rng = np.random.default_rng(20261121)
    picks = rng.integers(0, len(blocks), size=(rounds, len(blocks)))
    better = model_sums[picks].sum(axis=1) < base_sums[picks].sum(axis=1)
    return float(better.mean())


def backtest(census: Census, definition: dict, settings: dict) -> dict:
    """Chronological held-out evaluation of one success definition."""
    examples = build_examples(census, definition, settings)
    horizon = float(definition["horizon_hours"])
    base = {"version": MODEL_VERSION, "definition": definition,
            "features": list(FEATURES), "examples": int(len(examples.y)),
            "skipped_low_coverage": examples.skipped_low_coverage,
            "as_of": (datetime.fromtimestamp(census.last * 3600, UTC).isoformat()
                      if census.last is not None else None)}
    unique = np.unique(examples.cutoffs)
    if len(unique) < 3:
        return base | {"state": INSUFFICIENT,
                       "reason": f"{len(unique)} cutoff day(s) with a resolved outcome; "
                                 "a chronological split needs at least 3"}
    split = float(unique[int(math.floor((1 - float(settings.get("test_fraction", 0.3)))
                                         * len(unique)))])
    train = examples.cutoffs + horizon <= split
    test = examples.cutoffs >= split
    y_train, y_test = examples.y[train], examples.y[test]
    counts = {"train": int(train.sum()), "test": int(test.sum()),
              "train_positives": int(y_train.sum()), "test_positives": int(y_test.sum()),
              "split_at": datetime.fromtimestamp(split * 3600, UTC).isoformat()}
    needs = []
    if counts["train"] < int(settings.get("min_train", 200)):
        needs.append(f"{counts['train']} training examples, {settings.get('min_train', 200)} required")
    if counts["test"] < int(settings.get("min_test", 100)):
        needs.append(f"{counts['test']} test examples, {settings.get('min_test', 100)} required")
    if counts["test_positives"] < int(settings.get("min_test_positives", 10)):
        needs.append(f"{counts['test_positives']} test successes, "
                     f"{settings.get('min_test_positives', 10)} required")
    if not 0 < y_train.sum() < len(y_train):
        needs.append("the training period has only one outcome class")
    if needs:
        return base | counts | {"state": INSUFFICIENT, "reason": "; ".join(needs)}

    bins = int(settings.get("reliability_bins", 10))
    rate = float(y_train.mean())
    full = _fit(examples.X[train], y_train)
    ccu_only = _fit(examples.X[train][:, :1], y_train)
    p_full = full.predict_proba(examples.X[test])[:, 1]
    p_ccu = ccu_only.predict_proba(examples.X[test][:, :1])[:, 1]
    p_base = np.full(len(y_test), rate)
    scored = {"model": _metrics(y_test, p_full),
              "baseline_base_rate": _metrics(y_test, p_base),
              "baseline_ccu_only": _metrics(y_test, p_ccu)}
    rounds = int(settings.get("bootstrap_rounds", 500))
    needed = float(settings.get("bootstrap_confidence", 0.95))
    days = examples.cutoffs[test]
    # Which candidate earns the right to speak. The richer model has to beat
    # the player-count-only one reliably; failing that, the player-count-only
    # model has to beat the base rate reliably; failing that, nothing is shown.
    full_vs_ccu = _bootstrap_share_better(days, y_test, p_full, p_ccu, rounds)
    full_vs_base = _bootstrap_share_better(days, y_test, p_full, p_base, rounds)
    ccu_vs_base = _bootstrap_share_better(days, y_test, p_ccu, p_base, rounds)
    if full_vs_ccu >= needed and full_vs_base >= needed:
        selected, p_selected = "full", p_full
    elif ccu_vs_base >= needed:
        selected, p_selected = "ccu_only", p_ccu
    else:
        selected, p_selected = None, None
    verdict = SKILL if selected else NO_SKILL
    selected_scores = scored["model" if selected == "full" else "baseline_ccu_only"]         if selected else None
    skill = (1 - selected_scores["brier"] / scored["baseline_base_rate"]["brier"]
             if selected and scored["baseline_base_rate"]["brier"] > 0 else None)
    if selected == "full":
        reason = (f"the full model beats both the base rate and the player-count-only model "
                  f"in at least {needed:.0%} of day-block resamples")
    elif selected == "ccu_only":
        reason = (f"only today's player count carries signal: the full model beat it in "
                  f"{full_vs_ccu:.0%} of day-block resamples ({needed:.0%} required), so "
                  f"the player-count-only model is used; it beats the base rate in "
                  f"{ccu_vs_base:.0%}")
    else:
        reason = (f"no model reliably beats the base rate on held-out data (best: "
                  f"{max(full_vs_base, ccu_vs_base):.0%} of resamples, {needed:.0%} "
                  f"required); no probability is shown")
    return base | counts | {
        "state": verdict,
        "selected_model": selected,
        "reason": reason,
        "bootstrap_share_better": {"full_vs_ccu_only": round(full_vs_ccu, 3),
                                   "full_vs_base_rate": round(full_vs_base, 3),
                                   "ccu_only_vs_base_rate": round(ccu_vs_base, 3)},
        "selected_scores": selected_scores,
        "brier_skill_vs_base_rate": round(skill, 4) if skill is not None else None,
        "training_base_rate": round(rate, 4),
        "test_base_rate": round(float(y_test.mean()), 4),
        "scores": scored,
        "reliability": (reliability(y_test, p_selected, bins) if selected
                        else reliability(y_test, p_full, bins)),
        "note": "Held-out, chronological. Rows inside the census only; games are seen "
                "while on a shelf or while researched, so this is conditional on being "
                "noticed. Nightly gaps in the census mean peaks are recorded low.",
    }


SELECTED_COLUMNS = {"full": slice(None), "ccu_only": slice(0, 1)}


def final_model(census: Census, definition: dict, settings: dict, selected: str):
    """Refit the selected model on every resolved example, for live predictions."""
    examples = build_examples(census, definition, settings)
    if not 0 < examples.y.sum() < len(examples.y):
        return None
    columns = SELECTED_COLUMNS[selected]
    return columns, _fit(examples.X[:, columns], examples.y)


def predict(census: Census, definition: dict, settings: dict, universe_id: str,
            evaluation: dict, model) -> dict:
    """A probability for one game now, only if the definition has shown skill."""
    out = {"universe_id": str(universe_id), "definition": definition["key"],
           "label": definition["label"], "probability": None,
           "evaluation_state": evaluation.get("state")}
    if definition.get("threshold_ccu") is not None:
        out["target_ccu"] = definition["threshold_ccu"]
    if evaluation.get("state") != SKILL:
        return out | {"reason": evaluation.get("reason", "not evaluated")}
    series = census.series.get(str(universe_id))
    if series is None or census.last is None:
        return out | {"reason": "this game has no census observations"}
    index = _at_or_before(series, census.last,
                          float(settings.get("fresh_observation_hours", 1.0)))
    if index is None:
        return out | {"reason": "this game was not observed in the latest census"}
    if not applies(definition, float(series.ccu[index])):
        return out | {"reason": "the question does not apply to this game right now "
                                "(for example it is already above the target)"}
    if model is None:
        return out | {"reason": "no model could be fitted on the resolved examples"}
    columns, fitted = model
    x = np.array([features_at(series, index, census.created.get(str(universe_id)))])
    probability = float(fitted.predict_proba(x[:, columns])[0, 1])
    bin_ = next((entry for entry in evaluation.get("reliability", [])
                 if entry["from"] <= probability <= entry["to"]), None)
    return out | {
        "probability": round(probability, 4),
        "band": bin_,
        "model": evaluation["selected_model"],
        "held_out_brier": evaluation["selected_scores"]["brier"],
        "held_out_auc": evaluation["selected_scores"]["auc"],
        "reason": (f"held-out: of {bin_['games']} test examples predicted "
                   f"{bin_['from']:.0%}-{bin_['to']:.0%}, {bin_['reached']} succeeded"
                   if bin_ and bin_["games"] else "held-out bin for this value was empty"),
    }


# --------------------------------------------------------------------------
# Cache: the census changes every half hour; nothing here needs to be fresher
# --------------------------------------------------------------------------

_lock = threading.Lock()
_cache: dict = {"census_key": None, "census": None, "backtests": {}, "models": {}}
_running: set[str] = set()


def _census_key(db) -> tuple:
    return (db.scalar(select(func.max(MarketSample.captured_at))),
            db.scalar(select(func.count(GameSnapshot.id))))


def refresh(session_factory, keys: list[str] | None = None) -> None:
    """Recompute the census arrays and the backtests. Runs in a worker thread."""
    with session_factory() as db:
        key = _census_key(db)
        census = load_census(db)
    settings = evaluation_settings()
    results, models = {}, {}
    for entry in definitions().get("game") or []:
        if keys and entry["key"] not in keys:
            continue
        try:
            results[entry["key"]] = backtest(census, entry, settings)
            if results[entry["key"]]["state"] == SKILL:
                models[entry["key"]] = final_model(census, entry, settings,
                                                   results[entry["key"]]["selected_model"])
        except Exception as exc:  # one bad definition must not hide the others
            log.warning("backtest %s failed: %s", entry["key"], exc)
            results[entry["key"]] = {"state": "error", "reason": f"{type(exc).__name__}: {exc}"}
    with _lock:
        _cache.update(census_key=key, census=census, backtests=results, models=models)


def _refresh_in_background(session_factory) -> None:
    with _lock:
        if "all" in _running:
            return
        _running.add("all")

    def work():
        try:
            refresh(session_factory)
        finally:
            with _lock:
                _running.discard("all")

    threading.Thread(target=work, name="success-backtest", daemon=True).start()


def cached(db, session_factory) -> tuple[Census | None, dict, dict, bool]:
    """(census, backtests, models, stale). Starts a refresh when the census has moved on."""
    with _lock:
        census, key = _cache["census"], _cache["census_key"]
        results, models = dict(_cache["backtests"]), dict(_cache["models"])
    stale = key != _census_key(db)
    if stale:
        _refresh_in_background(session_factory)
    return census, results, models, stale


def computing() -> bool:
    with _lock:
        return bool(_running)
