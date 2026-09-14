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
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from .dataset import SPLIT_DEV, SPLIT_TEST, SPLIT_TRAIN, Dataset, build_dataset
from .decisions import CODE_EXACT_ID, AssociationOutcome, evaluate
from .features import FEATURE_NAMES
from .normalize import NORMALIZATION_VERSION
from .retrieval import EmbeddingProvider
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

    # Measure with fuzzy automatic association enabled, so the benchmark can
    # see what it *would* approve. Whether it stays enabled is decided below.
    probe = replace(base, fuzzy_auto_enabled=True, validated=False)
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

    frozen = replace(
        base,
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
