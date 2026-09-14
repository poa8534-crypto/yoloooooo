from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_score
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal, init_db
from .evidence import evidence_conflicts
from .models import Candidate, Observation
from .schemas import CalibrationStatus

REQUIRED_CLUSTERS = 200
MIN_HELDOUT_RECOMMENDATIONS = 10
PRECISION_FLOOR = 0.95
FEATURE_NAMES = [
    "current_demand",
    "ccu_change_7d",
    "visit_change_7d",
    "creator_view_velocity",
    "active_competitor_count",
    "market_concentration",
    "conflict_flag",
]


@dataclass
class Example:
    candidate_id: str
    run_id: str
    created_at: Any
    base_features: dict[str, float]
    label: int
    input_ccu: float


def _daily_values(rows: list[Observation], metric: str) -> list[tuple[Any, float]]:
    by_day: dict[Any, list[float]] = {}
    for row in rows:
        if row.metric == metric and isinstance(row.value_json, (int, float, str)):
            try:
                value = float(row.value_json)
            except ValueError:
                continue
            by_day.setdefault(row.observed_at.date(), []).append(value)
    return sorted((day, float(sum(values))) for day, values in by_day.items())


def _median(values: list[float]) -> float:
    return float(np.median(np.asarray(values, dtype=float)))


def _window(values: list[tuple[Any, float]], first: bool) -> list[float]:
    selected = values[:7] if first else values[-7:]
    return [value for _, value in selected]


def _growth(new: float, old: float) -> float:
    return (new - old) / max(abs(old), 1.0)


def _velocity(values: list[tuple[Any, float]], first: bool) -> float:
    selected = values[:8] if first else values[-8:]
    if len(selected) < 8:
        return 0.0
    deltas = [max(0.0, selected[i][1] - selected[i - 1][1]) for i in range(1, len(selected))]
    return float(np.mean(deltas))


def build_example(db: Session, candidate: Candidate) -> Example | None:
    rows = list(db.scalars(
        select(Observation)
        .where(Observation.candidate_id == candidate.id)
        .order_by(Observation.observed_at)
    ))
    ccu = _daily_values(rows, "roblox_playing")
    visits = _daily_values(rows, "roblox_visits")
    views = _daily_values(rows, "youtube_views")
    if len(ccu) < 30 or len(visits) < 7 or len(views) < 30:
        return None
    if (ccu[-1][0] - ccu[0][0]).days < 29:
        return None
    ccu_open, ccu_end = _median(_window(ccu, True)), _median(_window(ccu, False))
    opening_ccu = _window(ccu, True)
    opening_visits = _window(visits, True)
    start_view_velocity = _velocity(views, True)
    end_view_velocity = _velocity(views, False)
    base = {
        # Only the opening observation week is allowed to predict the later
        # 30-day outcome. Feeding the outcome window back into these features
        # would create target leakage and a meaningless precision result.
        "current_demand": math.log1p(max(ccu_open, 0.0)),
        "ccu_change_7d": _growth(_median(opening_ccu[-3:]), _median(opening_ccu[:3])),
        "visit_change_7d": _growth(opening_visits[-1], opening_visits[0]),
        "creator_view_velocity": math.log1p(max(start_view_velocity, 0.0)),
        "conflict_flag": 1.0 if evidence_conflicts(db, candidate.id) else 0.0,
    }
    label = int(ccu_end >= 1.20 * max(ccu_open, 1.0) and end_view_velocity >= start_view_velocity)
    return Example(candidate.id, candidate.run_id, candidate.created_at, base, label, ccu_open)


def _live_base_features(db: Session, candidate: Candidate) -> tuple[dict[str, float], float] | None:
    rows = list(db.scalars(
        select(Observation)
        .where(Observation.candidate_id == candidate.id)
        .order_by(Observation.observed_at)
    ))
    ccu = _daily_values(rows, "roblox_playing")[-7:]
    visits = _daily_values(rows, "roblox_visits")[-7:]
    views = _daily_values(rows, "youtube_views")[-8:]
    if len(ccu) < 7 or len(visits) < 7 or len(views) < 8:
        return None
    ccu_values = [value for _, value in ccu]
    visit_values = [value for _, value in visits]
    current_ccu = _median(ccu_values)
    return ({
        "current_demand": math.log1p(max(current_ccu, 0.0)),
        "ccu_change_7d": _growth(_median(ccu_values[-3:]), _median(ccu_values[:3])),
        "visit_change_7d": _growth(visit_values[-1], visit_values[0]),
        "creator_view_velocity": math.log1p(max(_velocity(views, True), 0.0)),
        "conflict_flag": 1.0 if evidence_conflicts(db, candidate.id) else 0.0,
    }, current_ccu)


def current_features(db: Session, candidate: Candidate) -> dict[str, float] | None:
    current = _live_base_features(db, candidate)
    if current is None:
        return None
    base, current_ccu = current
    peers = list(db.scalars(select(Candidate).where(Candidate.run_id == candidate.run_id)))
    peer_values = [
        item for peer in peers
        if (item := _live_base_features(db, peer)) is not None
    ]
    active = sum(1 for _, ccu in peer_values if ccu > 0)
    total = sum(ccu for _, ccu in peer_values)
    base["active_competitor_count"] = float(active)
    base["market_concentration"] = current_ccu / max(total, 1.0)
    return base


def collect_examples(db: Session) -> list[Example]:
    examples = [
        example for candidate in db.scalars(select(Candidate).order_by(Candidate.created_at))
        if (example := build_example(db, candidate)) is not None
    ]
    by_run: dict[str, list[Example]] = {}
    for example in examples:
        by_run.setdefault(example.run_id, []).append(example)
    for group in by_run.values():
        active = sum(1 for item in group if item.input_ccu > 0)
        total = sum(item.input_ccu for item in group)
        for item in group:
            item.base_features["active_competitor_count"] = float(active)
            item.base_features["market_concentration"] = item.input_ccu / max(total, 1.0)
    return examples


def grouped_time_split(
    examples: list[Example],
    *,
    train_fraction: float = 0.60,
    dev_fraction: float = 0.20,
) -> tuple[list[int], list[int], list[int]]:
    """Split by research run, in discovery order.

    Candidates found in the same run are competitors in the same niche, and
    two of their features — `active_competitor_count` and
    `market_concentration` — are computed *from each other*. Cutting that group
    across a split would let held-out rows carry information derived from
    training rows, which is exactly the leakage the precision gate is supposed
    to measure against.
    """
    by_run: dict[str, list[int]] = {}
    for index, example in enumerate(examples):
        by_run.setdefault(example.run_id, []).append(index)
    ordered = sorted(
        by_run.items(),
        key=lambda item: (min(examples[i].created_at for i in item[1]), item[0]),
    )
    total = len(examples)
    train_limit = total * train_fraction
    dev_limit = total * (train_fraction + dev_fraction)
    train: list[int] = []
    dev: list[int] = []
    test: list[int] = []
    seen = 0
    for _run_id, indexes in ordered:
        target = train if seen < train_limit else (dev if seen < dev_limit else test)
        target.extend(sorted(indexes))
        seen += len(indexes)
    return train, dev, test


def current_artifact_path() -> Path:
    return get_settings().model_dir / "current.json"


def load_artifact() -> dict[str, Any] | None:
    path = current_artifact_path()
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def calibration_status(db: Session) -> CalibrationStatus:
    count = len(collect_examples(db))
    artifact = load_artifact()
    if artifact:
        active = bool(artifact.get("active", False))
        return CalibrationStatus(
            phase="active" if active else "benchmark_failed",
            complete_clusters=count,
            required_clusters=REQUIRED_CLUSTERS,
            scoring_active=active,
            model_version=artifact.get("version"),
            heldout_precision=artifact.get("heldout_precision"),
            heldout_recommendations=artifact.get("heldout_recommendations"),
            reason=artifact.get("reason", ""),
        )
    return CalibrationStatus(
        phase="collection",
        complete_clusters=count,
        required_clusters=REQUIRED_CLUSTERS,
        scoring_active=False,
        reason=f"Collecting complete 30-day windows ({count}/{REQUIRED_CLUSTERS}).",
    )


def _raw_probability(features: dict[str, float], artifact: dict[str, Any]) -> float:
    x = np.asarray([features[name] for name in artifact["feature_names"]], dtype=float)
    scaled = (x - np.asarray(artifact["mean"])) / np.asarray(artifact["scale"])
    logit = float(np.dot(scaled, np.asarray(artifact["coefficients"])) + artifact["intercept"])
    return 1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, logit))))


def score_features(features: dict[str, float], artifact: dict[str, Any]) -> float:
    raw = _raw_probability(features, artifact)
    return float(np.interp(raw, artifact["calibration_x"], artifact["calibration_y"]))


def train(db: Session) -> dict[str, Any]:
    examples = collect_examples(db)
    if len(examples) < REQUIRED_CLUSTERS:
        raise ValueError(f"need {REQUIRED_CLUSTERS} complete clusters; found {len(examples)}")
    examples.sort(key=lambda item: (item.created_at, item.candidate_id))
    canonical = [
        {"id": e.candidate_id, "features": e.base_features, "label": e.label}
        for e in examples
    ]
    dataset_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    X = np.asarray([[e.base_features[name] for name in FEATURE_NAMES] for e in examples])
    y = np.asarray([e.label for e in examples])
    if len(set(y.tolist())) < 2:
        raise ValueError("training data must contain both positive and negative outcomes")
    train_idx, dev_idx, test_idx = grouped_time_split(examples)
    if not (train_idx and dev_idx and test_idx):
        raise ValueError(
            "not enough distinct research runs to build leak-free train/dev/test splits"
        )
    y_dev, y_test = y[dev_idx], y[test_idx]
    scaler = StandardScaler().fit(X[train_idx])
    model = LogisticRegression(C=1.0, max_iter=2000, class_weight="balanced", random_state=17)
    model.fit(scaler.transform(X[train_idx]), y[train_idx])
    dev_raw = model.predict_proba(scaler.transform(X[dev_idx]))[:, 1]
    calibrator = IsotonicRegression(out_of_bounds="clip").fit(dev_raw, y_dev)
    dev_prob = calibrator.predict(dev_raw)
    thresholds: list[tuple[float, int]] = []
    for threshold in sorted(set(dev_prob.tolist()), reverse=True):
        pred = dev_prob >= threshold
        count = int(pred.sum())
        if count >= 5 and precision_score(y_dev, pred, zero_division=0) >= PRECISION_FLOOR:
            thresholds.append((float(threshold), count))
    threshold = min((item[0] for item in thresholds), default=1.01)
    test_raw = model.predict_proba(scaler.transform(X[test_idx]))[:, 1]
    test_prob = calibrator.predict(test_raw)
    test_pred = test_prob >= threshold
    heldout_recommendations = int(test_pred.sum())
    heldout_precision = float(precision_score(y_test, test_pred, zero_division=0))
    active = heldout_precision >= PRECISION_FLOOR and heldout_recommendations >= MIN_HELDOUT_RECOMMENDATIONS
    reason = (
        "Precision gate passed; automatic recommendations are active."
        if active else
        f"Benchmark gate failed: precision={heldout_precision:.3f}, recommendations={heldout_recommendations}."
    )
    artifact = {
        "active": active,
        "version": f"market-growth-{dataset_hash[:12]}",
        "dataset_hash": dataset_hash,
        "feature_names": FEATURE_NAMES,
        "mean": scaler.mean_.tolist(),
        "scale": np.where(scaler.scale_ == 0, 1.0, scaler.scale_).tolist(),
        "coefficients": model.coef_[0].tolist(),
        "intercept": float(model.intercept_[0]),
        "calibration_x": calibrator.X_thresholds_.tolist(),
        "calibration_y": calibrator.y_thresholds_.tolist(),
        "threshold": threshold,
        "heldout_precision": heldout_precision,
        "heldout_recommendations": heldout_recommendations,
        "train_count": len(train_idx),
        "dev_count": len(dev_idx),
        "test_count": len(test_idx),
        "split_strategy": "grouped_by_run_ordered_by_discovery_time",
        "reason": reason,
    }
    path = current_artifact_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and gate the market-growth evidence model")
    parser.parse_args()
    init_db()
    with SessionLocal() as db:
        artifact = train(db)
    print(json.dumps(artifact, indent=2))


if __name__ == "__main__":
    main()
