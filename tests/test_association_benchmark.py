"""Dataset splitting and the threshold-validation gate."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.association import MatchCandidateView, MatchSubjectView
from app.association.benchmark import (
    PENALTY_FEATURES,
    fit_weights,
    run_benchmark,
    run_split,
)
from app.association.dataset import (
    SPLIT_DEV,
    SPLIT_TEST,
    SPLIT_TRAIN,
    Dataset,
    LabeledExample,
    build_dataset,
    load_seed,
    split_by_cluster_and_time,
)
from app.association.decisions import AssociationOutcome
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
    """Precision, not coverage — and on a sample size worth naming.

    "No false positives" is only meaningful next to the number of positives it
    is drawn from, so that count is asserted too. It is small.
    """
    probe = replace(Thresholds(), fuzzy_auto_enabled=True)
    decisions = 0
    for split in (SPLIT_TRAIN, SPLIT_DEV, SPLIT_TEST):
        result = run_split(dataset, split, probe)
        assert result.false_positives == [], f"{split} produced false positives"
        assert result.exact_id_precision in (None, 1.0)
        decisions += result.auto_total
    # Guards the claim against silently shrinking to zero decisions, which
    # would make "no false positives" vacuously true.
    assert decisions >= 15, f"only {decisions} automatic decisions across all splits"


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


def _fuzzy_auto_dataset() -> Dataset:
    """A dataset whose held-out split the engine really can decide fuzzily.

    Each cluster holds two competing candidates and a video whose title names
    one of them outright, on that studio's own channel: two independent
    features, a real runner-up to measure a margin against, and no ID.
    """
    examples: list[LabeledExample] = []
    for index in range(9):
        left = MatchCandidateView(
            candidate_id=f"c{index}-a", universe_id=f"90{index}1", place_ids=(f"70{index}1",),
            raw_name=f"Lantern Harbour {index}", creator_name=f"Studio {index}",
        )
        right = MatchCandidateView(
            candidate_id=f"c{index}-b", universe_id=f"90{index}2", place_ids=(f"70{index}2",),
            raw_name=f"Copper Foundry {index}", creator_name=f"Other Studio {index}",
        )
        subject = MatchSubjectView(
            subject_id=f"s{index}", subject_type="youtube_video", external_id=f"v{index}",
            raw_title=f"Lantern Harbour {index}",
            raw_description="A full session with commentary from the team.",
            creator_name=f"Studio {index}",
            source_artifact_sha256="a" * 64, extraction_method="youtube_videos_api",
            source_tier="primary",
            discovered_at=datetime(2026, 1 + index, 1, tzinfo=UTC),
        )
        examples.append(LabeledExample(
            example_id=f"ex{index}", cluster=f"cluster-{index}", niche="harbour building",
            kind="exact_title", subject=subject, pool=(left, right),
            label_candidate_id=left.candidate_id,
            discovered_at=subject.discovered_at,
        ))
    return Dataset(
        examples=tuple(examples), splits=split_by_cluster_and_time(examples)
    )


def test_the_engine_can_decide_fuzzily_when_the_evidence_is_there():
    built = _fuzzy_auto_dataset()
    probe = replace(Thresholds(), fuzzy_auto_enabled=True)
    result = run_split(built, SPLIT_TEST, probe)
    assert result.auto_fuzzy > 0, "the fuzzy path must be reachable at all"
    assert result.false_positives == []


def test_the_gate_enables_fuzzy_matching_when_the_evidence_supports_it(monkeypatch):
    # Hold the engine fixed and lower only the sample-size requirement, to prove
    # the gate flips when a held-out measurement actually clears the floor.
    monkeypatch.setattr("app.association.benchmark.MIN_FUZZY_HELDOUT_DECISIONS", 1)
    frozen, report = run_benchmark(dataset=_fuzzy_auto_dataset())
    assert report["passed"] is True
    assert frozen.fuzzy_auto_enabled is True
    assert frozen.validated is True
    assert frozen.heldout_precision == 1.0


def test_the_seed_dataset_yields_no_held_out_fuzzy_decisions(dataset):
    """Documents where the engine actually stands on the shipped data.

    Requiring a real runner-up plus two independent features means the seed
    set produces no held-out fuzzy decisions at all, so the gate cannot open
    on it however the precision floor is set.
    """
    _frozen, report = run_benchmark(dataset=dataset)
    assert report["splits"][SPLIT_TEST]["auto_fuzzy"] == 0
    assert report["passed"] is False


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


def test_learned_weights_are_fitted_only_on_the_training_split(dataset):
    learned = fit_weights(dataset, replace(Thresholds(), fuzzy_auto_enabled=True))
    assert learned is not None
    assert learned.weights_version.startswith("weights-learned-")
    assert set(learned.weights) == set(Thresholds().weights)


def test_fitted_penalty_weights_are_already_negative_on_this_data(dataset):
    """Documents the happy case. It does NOT exercise the clamp — see below."""
    learned = fit_weights(dataset, replace(Thresholds(), fuzzy_auto_enabled=True))
    for name in PENALTY_FEATURES:
        assert learned.weights[name] <= 0.0


def test_the_clamp_forces_a_positive_fitted_penalty_back_to_zero(dataset, monkeypatch):
    """Drive the fit to a hostile result so the clamp is what is under test.

    The real dataset happens to fit every penalty negative, so asserting the
    sign proves nothing about the guard. This substitutes a model that returns
    a positive coefficient for every feature.
    """
    class AllPositive:
        def __init__(self, *_args, **_kwargs):
            self.coef_ = None
            self.intercept_ = None

        def fit(self, x, _y):
            import numpy as _np

            self.coef_ = _np.ones((1, x.shape[1]))
            self.intercept_ = _np.zeros(1)
            return self

    monkeypatch.setattr("app.association.benchmark.LogisticRegression", AllPositive)
    learned = fit_weights(dataset, replace(Thresholds(), fuzzy_auto_enabled=True))
    assert learned is not None
    for name in PENALTY_FEATURES:
        assert learned.weights[name] == 0.0, f"{name} was left as a reward"
    # Everything that is not a penalty keeps the fitted value, so the clamp is
    # narrow rather than flattening the whole vector.
    assert learned.weights["direct_universe_id_agreement"] == 1.0


def test_learned_weights_are_refused_if_they_cost_exact_id_resolution(dataset):
    """Verified-ID resolution is not tradeable for fuzzy coverage."""
    _frozen, report = run_benchmark(dataset=dataset)
    default_exact = run_split(
        dataset, SPLIT_TEST, replace(Thresholds(), fuzzy_auto_enabled=True)
    ).auto_exact_id
    assert report["splits"][SPLIT_TEST]["auto_exact_id"] == default_exact
    # On this dataset the fit really does cost exact-ID decisions, so the
    # rejection must have fired and the defaults must have been measured.
    assert report["learned_weights_rejected"], "expected the fit to be rejected here"
    assert report["measured_weights_source"] == "default"


def test_a_failed_gate_never_adopts_the_unvalidated_policy(dataset):
    frozen, report = run_benchmark(dataset=dataset)
    assert report["passed"] is False
    assert report["frozen_weights_source"] == "default"
    assert frozen.weights == Thresholds().weights
    assert frozen.bias == Thresholds().bias


def test_dense_similarity_alone_cannot_approve_whatever_the_weights_say():
    """The independent-feature gate is a rule, not a weight."""
    from app.association import MatchCandidateView, MatchSubjectView, evaluate
    from app.association.retrieval import EmbeddingProvider

    greedy = replace(
        Thresholds(),
        fuzzy_auto_enabled=True,
        validated=True,
        weights={**Thresholds().weights, "embedding_cosine": 50.0},
    )
    pool = [
        MatchCandidateView(candidate_id="a", universe_id="1", place_ids=("11",), raw_name="Alpha"),
        MatchCandidateView(candidate_id="b", universe_id="2", place_ids=("22",), raw_name="Beta"),
    ]
    subject = MatchSubjectView(
        subject_id="s", subject_type="youtube_video", external_id="v",
        raw_title="something entirely unrelated", raw_description="a description",
        source_artifact_sha256="a" * 64, extraction_method="youtube_videos_api",
    )
    dense = EmbeddingProvider(model_name="stub", scorer=lambda _q, docs: [0.99] * len(docs))
    verdict = evaluate(subject, pool, thresholds=greedy, embedder=dense, shadow_mode=False)
    assert verdict.outcome is not AssociationOutcome.AUTO_ASSOCIATE


def test_the_benchmark_reports_recall_not_only_precision(dataset):
    """Abstaining from everything yields perfect precision and no value.

    The spec is explicit that rejecting everything is not sufficient, so the
    report has to carry recall alongside precision, and recall has to be real.
    """
    probe = replace(Thresholds(), fuzzy_auto_enabled=True)
    _frozen, report = run_benchmark(dataset=dataset, base=Thresholds())

    for split in (SPLIT_TRAIN, SPLIT_DEV, SPLIT_TEST):
        rows = report["splits"][split]
        assert "automatic_recall" in rows, f"{split} reports no recall"
        assert "labeled_positives" in rows
        assert rows["labeled_positives"] > 0

    measured = run_split(dataset, SPLIT_TRAIN, probe)
    assert measured.labeled_positives > 0
    # Recall is low by design, but a matcher that associates nothing at all is
    # a failure the report must be able to show.
    assert measured.automatic_recall is not None
    assert measured.automatic_recall > 0.0, "the engine associated nothing on train"
    assert measured.automatic_recall <= 1.0
