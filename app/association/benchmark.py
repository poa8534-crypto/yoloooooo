"""Threshold validation.

Runs the engine over the labeled dataset with fuzzy automatic association
temporarily enabled, measures held-out precision, and freezes a policy artifact
either way. Fuzzy automatic association is switched on only when held-out
precision clears the floor on enough decisions; otherwise the artifact records
the failure and the engine stays in review-only mode.

Precision, not coverage, is the objective. A run that automates nothing and
sends everything to review is a passing run in every sense that matters here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime

import numpy as np
from sklearn.linear_model import LogisticRegression
from sqlalchemy.orm import Session

from .dataset import SPLIT_DEV, SPLIT_TEST, SPLIT_TRAIN, Dataset, build_dataset
from .decisions import CODE_EXACT_ID, AssociationOutcome, evaluate
from .features import FEATURE_NAMES, FeatureContext, compute_features
from .normalize import NORMALIZATION_VERSION
from .retrieval import EmbeddingProvider, retrieve
from .thresholds import (
    FUZZY_PRECISION_FLOOR,
    MIN_FUZZY_HELDOUT_DECISIONS,
    Thresholds,
    write_artifact,
)


@dataclass
class SplitResult:
    split: str
    examples: int = 0
    auto_total: int = 0
    auto_fuzzy: int = 0
    auto_fuzzy_correct: int = 0
    auto_exact_id: int = 0
    auto_exact_id_correct: int = 0
    review_required: int = 0
    no_match: int = 0
    blocked_conflict: int = 0
    false_positives: list[str] = field(default_factory=list)
    hard_negatives: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)

    @property
    def fuzzy_precision(self) -> float | None:
        if not self.auto_fuzzy:
            return None
        return self.auto_fuzzy_correct / self.auto_fuzzy

    @property
    def exact_id_precision(self) -> float | None:
        if not self.auto_exact_id:
            return None
        return self.auto_exact_id_correct / self.auto_exact_id

    def as_json(self) -> dict:
        data = asdict(self)
        data["fuzzy_precision"] = self.fuzzy_precision
        data["exact_id_precision"] = self.exact_id_precision
        return data


def run_split(
    dataset: Dataset,
    split: str,
    thresholds: Thresholds,
    *,
    embedder: EmbeddingProvider | None = None,
) -> SplitResult:
    """Score one split. Shadow mode is off here so fuzzy decisions are visible."""
    result = SplitResult(split=split)
    for example in dataset.of(split):
        verdict = evaluate(
            example.subject,
            list(example.pool),
            thresholds=thresholds,
            embedder=embedder,
            niche=example.niche,
            shadow_mode=False,
        )
        result.examples += 1
        chosen = verdict.winner.candidate_id if verdict.winner else None
        correct = chosen == example.label_candidate_id

        if verdict.outcome is AssociationOutcome.AUTO_ASSOCIATE:
            result.auto_total += 1
            exact = CODE_EXACT_ID in verdict.rationale
            if exact:
                result.auto_exact_id += 1
                result.auto_exact_id_correct += int(correct)
            else:
                result.auto_fuzzy += 1
                result.auto_fuzzy_correct += int(correct)
            if not correct:
                result.false_positives.append(example.example_id)
        elif verdict.outcome is AssociationOutcome.REVIEW_REQUIRED:
            result.review_required += 1
            result.ambiguous.append(example.example_id)
        elif verdict.outcome is AssociationOutcome.BLOCKED_CONFLICT:
            result.blocked_conflict += 1
            result.hard_negatives.append(example.example_id)
        else:
            result.no_match += 1
            if example.label_candidate_id is None:
                result.hard_negatives.append(example.example_id)
    return result


PENALTY_FEATURES = (
    "generic_title_penalty",
    "conflicting_explicit_id_penalty",
    "competing_game_name_penalty",
)


def feature_rows(
    dataset: Dataset,
    split: str,
    *,
    embedder: EmbeddingProvider | None = None,
) -> tuple[list[list[float]], list[int]]:
    """Every (subject, candidate) pair in one split, with its label.

    A pair is positive when the candidate is the one the label names. Pairs are
    drawn from retrieval, so the model is fitted on the same candidate set the
    engine actually has to choose between.
    """
    rows: list[list[float]] = []
    labels: list[int] = []
    for example in dataset.of(split):
        found = retrieve(example.subject, list(example.pool), embedder=embedder)
        for candidate in found.candidates:
            context = FeatureContext(
                niche=example.niche,
                embedding_score=found.embedding_scores.get(candidate.candidate_id),
                embedding_available=found.embedding_available
                and candidate.candidate_id in found.embedding_scores,
                competing_name_hits=found.competing_name_hits.get(candidate.candidate_id, 0),
                retrieval_methods=found.methods_for(candidate.candidate_id),
            )
            rows.append(compute_features(example.subject, candidate, context).as_list())
            labels.append(int(candidate.candidate_id == example.label_candidate_id))
    return rows, labels


def fit_weights(
    dataset: Dataset,
    base: Thresholds,
    *,
    embedder: EmbeddingProvider | None = None,
    split: str = SPLIT_TRAIN,
) -> Thresholds | None:
    """Fit weights on the training split only.

    Returns None when the split cannot support a fit (too few rows, or only one
    class present). Penalty features are clamped at or below zero afterwards: a
    fitted coefficient that turned "this title names a different game" into a
    *bonus* would be a plausible artefact of a small sample and an unacceptable
    engine.
    """
    rows, labels = feature_rows(dataset, split, embedder=embedder)
    if len(rows) < 20 or len(set(labels)) < 2:
        return None
    x = np.asarray(rows, dtype=float)
    y = np.asarray(labels, dtype=int)
    model = LogisticRegression(
        C=1.0, max_iter=5000, class_weight="balanced", random_state=17
    )
    model.fit(x, y)
    weights = {
        name: float(coefficient)
        for name, coefficient in zip(FEATURE_NAMES, model.coef_[0], strict=True)
    }
    for name in PENALTY_FEATURES:
        weights[name] = min(0.0, weights[name])
    digest = hashlib.sha256(
        json.dumps(weights, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    return replace(
        base,
        weights=weights,
        bias=float(model.intercept_[0]),
        weights_version=f"weights-learned-{digest}",
    )


@dataclass(frozen=True)
class DevQuality:
    fuzzy_precision: float
    fuzzy_decisions: int
    exact_id_decisions: int

    @property
    def fuzzy_rank(self) -> tuple[float, int]:
        """Precision first, then how many decisions it was willing to make."""
        return (self.fuzzy_precision, self.fuzzy_decisions)


def _dev_quality(dataset: Dataset, thresholds: Thresholds, embedder) -> DevQuality:
    result = run_split(dataset, SPLIT_DEV, thresholds, embedder=embedder)
    return DevQuality(
        fuzzy_precision=result.fuzzy_precision or 0.0,
        fuzzy_decisions=result.auto_fuzzy,
        exact_id_decisions=result.auto_exact_id,
    )


def run_benchmark(
    db: Session | None = None,
    *,
    base: Thresholds | None = None,
    embedder: EmbeddingProvider | None = None,
    dataset: Dataset | None = None,
) -> tuple[Thresholds, dict]:
    """Measure, then freeze. Returns the frozen policy and the full report."""
    dataset = dataset or build_dataset(db)
    dataset_hash = dataset.hash()
    base = base or Thresholds()

    # Candidate policies: the hand-set defaults, and weights fitted on the
    # training split. Whichever looks better on dev goes to the held-out test;
    # the test split is never used to choose.
    default_policy = replace(base, fuzzy_auto_enabled=True, validated=False)
    learned_policy = fit_weights(dataset, default_policy, embedder=embedder)
    weights_source = "default"
    probe = default_policy
    rejection = ""
    if learned_policy is not None:
        default_dev = _dev_quality(dataset, default_policy, embedder)
        learned_dev = _dev_quality(dataset, learned_policy, embedder)
        if learned_dev.exact_id_decisions < default_dev.exact_id_decisions:
            # A fit that spreads weight off the direct-ID features can stop
            # verified-ID links clearing the high threshold. Exact identity
            # resolution is the one thing that works in shadow mode; it is not
            # tradeable for fuzzy coverage.
            rejection = (
                f"learned weights rejected: exact-ID decisions on dev fell from "
                f"{default_dev.exact_id_decisions} to {learned_dev.exact_id_decisions}"
            )
        elif learned_dev.fuzzy_rank > default_dev.fuzzy_rank:
            probe = learned_policy
            weights_source = "learned"

    results = {
        split: run_split(dataset, split, probe, embedder=embedder)
        for split in (SPLIT_TRAIN, SPLIT_DEV, SPLIT_TEST)
    }
    test = results[SPLIT_TEST]
    precision = test.fuzzy_precision
    decisions = test.auto_fuzzy
    passed = (
        precision is not None
        and precision >= FUZZY_PRECISION_FLOOR
        and decisions >= MIN_FUZZY_HELDOUT_DECISIONS
    )
    if passed:
        reason = (
            f"Held-out fuzzy precision {precision:.4f} on {decisions} decisions "
            f"clears the {FUZZY_PRECISION_FLOOR:.2f} floor; fuzzy automatic "
            "association is enabled."
        )
    else:
        measured = "no fuzzy decisions" if precision is None else f"precision={precision:.4f}"
        reason = (
            f"Benchmark did not clear the gate ({measured} on {decisions} held-out "
            f"fuzzy decisions; {FUZZY_PRECISION_FLOOR:.2f} precision on at least "
            f"{MIN_FUZZY_HELDOUT_DECISIONS} required). Fuzzy matching stays in "
            "review-only mode; exact verified-ID matches are unaffected."
        )

    # A policy that did not clear the gate is never adopted. The measured
    # numbers are still recorded, but what gets frozen is the shipped
    # default with fuzzy automatic association off.
    adopted = probe if passed else base
    frozen = replace(
        adopted,
        fuzzy_auto_enabled=passed,
        validated=passed,
        dataset_hash=dataset_hash,
        embedding_model=embedder.model_name if embedder else "",
        heldout_precision=precision,
        heldout_decisions=decisions,
        benchmark_reason=reason,
    )
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_hash": dataset_hash,
        "dataset_counts": dataset.counts(),
        "normalization_version": NORMALIZATION_VERSION,
        "feature_order": list(FEATURE_NAMES),
        "matcher_version": frozen.matcher_version,
        "threshold_version": frozen.threshold_version,
        "weights": dict(frozen.weights),
        "measured_weights_source": weights_source,
        "frozen_weights_source": weights_source if passed else "default",
        "learned_weights_rejected": rejection,
        "weights_version": frozen.weights_version,
        "bias": frozen.bias,
        "high": frozen.high,
        "low": frozen.low,
        "margin_min": frozen.margin_min,
        "min_required_coverage": frozen.min_required_coverage,
        "embedding_model": frozen.embedding_model,
        "fuzzy_precision_floor": FUZZY_PRECISION_FLOOR,
        "min_fuzzy_heldout_decisions": MIN_FUZZY_HELDOUT_DECISIONS,
        "passed": passed,
        "reason": reason,
        "splits": {split: value.as_json() for split, value in results.items()},
    }
    return frozen, report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate association thresholds and freeze the policy artifact"
    )
    parser.add_argument(
        "--write", action="store_true",
        help="write the frozen policy to the model directory",
    )
    parser.add_argument(
        "--report", default="", help="optional path to write the full JSON report",
    )
    args = parser.parse_args()

    from ..db import SessionLocal, init_db

    init_db()
    with SessionLocal() as db:
        thresholds, report = run_benchmark(db)
    if args.write:
        report["artifact_path"] = str(write_artifact(thresholds))
    if args.report:
        from pathlib import Path

        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
