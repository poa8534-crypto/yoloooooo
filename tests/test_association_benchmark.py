"""Dataset splitting and the threshold-validation gate."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.association.benchmark import run_benchmark, run_split
from app.association.dataset import (
    SPLIT_DEV,
    SPLIT_TEST,
    SPLIT_TRAIN,
    build_dataset,
    load_seed,
    split_by_cluster_and_time,
)
from app.association.thresholds import (
    FUZZY_PRECISION_FLOOR,
    MIN_FUZZY_HELDOUT_DECISIONS,
    Thresholds,
    thresholds_from_artifact,
)


@pytest.fixture(scope="module")
def dataset():
    return build_dataset()


def test_the_seed_dataset_covers_every_adversarial_shape():
    kinds = {example.kind for example in load_seed()}
    for required in (
        "clear_positive_id", "clear_positive_name", "clear_negative", "similar_name",
        "sequel", "clone", "generic", "multi_game", "missing_description", "injection",
        "renamed", "duplicate", "conflict", "wrong_link", "name_collision",
    ):
        assert required in kinds, f"seed dataset is missing {required} examples"


def test_no_cluster_spans_two_splits(dataset):
    by_example = {example.example_id: example for example in dataset.examples}
    homes: dict[str, set[str]] = {}
    for split, ids in dataset.splits.items():
        for example_id in ids:
            homes.setdefault(by_example[example_id].cluster, set()).add(split)
    leaking = {cluster: splits for cluster, splits in homes.items() if len(splits) > 1}
    assert not leaking, f"clusters leaked across splits: {leaking}"


def test_splits_are_ordered_by_discovery_time(dataset):
    def latest(split):
        return max(example.discovered_at for example in dataset.of(split))

    def earliest(split):
        return min(example.discovered_at for example in dataset.of(split))

    assert latest(SPLIT_TRAIN) <= earliest(SPLIT_DEV)
    assert latest(SPLIT_DEV) <= earliest(SPLIT_TEST)


def test_every_example_lands_in_exactly_one_split(dataset):
    assigned = [example_id for ids in dataset.splits.values() for example_id in ids]
    assert len(assigned) == len(set(assigned)) == len(dataset.examples)


def test_the_dataset_hash_is_stable_and_content_addressed(dataset):
    assert dataset.hash() == build_dataset().hash()
    trimmed = replace(dataset, examples=dataset.examples[:-1])
    assert trimmed.hash() != dataset.hash()


def test_splitting_is_deterministic():
    examples = load_seed()
    assert split_by_cluster_and_time(examples) == split_by_cluster_and_time(
        list(reversed(examples))
    )


def test_the_engine_makes_no_false_positives_on_the_seed_dataset(dataset):
    """The measurement that matters: precision, not coverage."""
    probe = replace(Thresholds(), fuzzy_auto_enabled=True)
    for split in (SPLIT_TRAIN, SPLIT_DEV, SPLIT_TEST):
        result = run_split(dataset, split, probe)
        assert result.false_positives == [], f"{split} produced false positives"
        assert result.exact_id_precision in (None, 1.0)


def test_the_gate_keeps_fuzzy_matching_in_review_only_mode_by_default(dataset):
    frozen, report = run_benchmark(dataset=dataset)
    assert report["passed"] is False
    assert frozen.fuzzy_auto_enabled is False
    assert frozen.validated is False
    # It fails on sample size, not on precision: the seed set is too small to
    # support a 99% claim, which is exactly what the gate is there to catch.
    assert report["splits"][SPLIT_TEST]["auto_fuzzy"] < MIN_FUZZY_HELDOUT_DECISIONS
    assert frozen.dataset_hash == dataset.hash()
    assert str(FUZZY_PRECISION_FLOOR) in report["reason"] or "0.99" in report["reason"]


def test_the_gate_enables_fuzzy_matching_when_the_evidence_supports_it(monkeypatch, dataset):
    # Hold the engine fixed and lower only the sample-size requirement, to prove
    # the gate flips when a held-out measurement actually clears the floor.
    monkeypatch.setattr("app.association.benchmark.MIN_FUZZY_HELDOUT_DECISIONS", 1)
    frozen, report = run_benchmark(dataset=dataset)
    assert report["passed"] is True
    assert frozen.fuzzy_auto_enabled is True
    assert frozen.validated is True
    assert frozen.heldout_precision == 1.0


def test_a_frozen_artifact_records_everything_needed_to_reproduce_it(dataset):
    frozen, _report = run_benchmark(dataset=dataset)
    artifact = frozen.as_json()
    for key in (
        "feature_order", "weights", "bias", "high", "low", "margin_min",
        "dataset_hash", "normalization_version", "feature_schema_version",
        "threshold_version", "matcher_version", "embedding_model_hash",
        "heldout_precision", "benchmark_reason", "fingerprint",
    ):
        assert key in artifact, f"frozen artifact is missing {key}"
    assert thresholds_from_artifact(artifact).fingerprint() == frozen.fingerprint()


def test_an_artifact_frozen_against_another_feature_order_is_refused(dataset):
    frozen, _report = run_benchmark(dataset=dataset)
    artifact = frozen.as_json()
    artifact["feature_order"] = list(reversed(artifact["feature_order"]))
    with pytest.raises(ValueError, match="different feature order"):
        thresholds_from_artifact(artifact)


def test_an_artifact_frozen_against_another_normalization_is_refused(dataset):
    frozen, _report = run_benchmark(dataset=dataset)
    artifact = frozen.as_json()
    artifact["normalization_version"] = "norm-v0"
    with pytest.raises(ValueError, match="different normalization version"):
        thresholds_from_artifact(artifact)
