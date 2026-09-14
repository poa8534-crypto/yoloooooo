import math
from datetime import UTC, datetime, timedelta

from app.calibration import REQUIRED_CLUSTERS, build_example, calibration_status
from app.models import Candidate, Observation, ResearchRun, SourceArtifact


def test_scoring_is_locked_without_complete_windows(db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.calibration.load_artifact", lambda: None)
    status = calibration_status(db)
    assert status.scoring_active is False
    assert status.complete_clusters == 0
    assert status.required_clusters == REQUIRED_CLUSTERS
    assert status.phase == "collection"


def _candidate_window(db, *, grows: bool):
    run = ResearchRun(niche="test niche")
    db.add(run)
    db.flush()
    candidate = Candidate(run_id=run.id, external_id=f"game-{grows}")
    db.add(candidate)
    db.flush()
    artifact = SourceArtifact(
        url="https://games.roblox.com/v1/games",
        publisher_owner="roblox.com",
        retrieval_method="test",
        sha256="a" * 64,
        content_type="application/json",
        raw_path="unused",
        source_tier="primary",
        is_discovery_only=False,
    )
    db.add(artifact)
    db.flush()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for day in range(30):
        early_ccu = 10 + day
        ccu = early_ccu if day < 7 else (100 + day if grows else 4)
        for metric, value in (
            ("roblox_playing", ccu),
            ("roblox_visits", 1_000 + day * 20),
            ("youtube_views", 5_000 + day * (200 if grows or day < 8 else 20)),
        ):
            db.add(Observation(
                artifact_id=artifact.id,
                candidate_id=candidate.id,
                metric=metric,
                value_json=value,
                unit=None,
                extraction_method="json_pointer",
                pointer="/test",
                observed_at=start + timedelta(days=day),
            ))
    db.flush()
    return candidate


def test_outcome_period_is_not_leaked_into_training_features(db):
    growing = build_example(db, _candidate_window(db, grows=True))
    shrinking = build_example(db, _candidate_window(db, grows=False))
    assert growing is not None and shrinking is not None
    assert growing.label == 1
    assert shrinking.label == 0
    assert growing.base_features == shrinking.base_features
    assert growing.base_features["current_demand"] == math.log1p(13)


def test_a_research_run_never_spans_two_calibration_splits():
    """Candidates from one run share derived features, so they share a split."""
    from datetime import timedelta

    from app.calibration import Example, grouped_time_split

    base = datetime(2026, 1, 1, tzinfo=UTC)
    examples = [
        Example(
            candidate_id=f"c{run}-{seat}",
            run_id=f"run-{run}",
            created_at=base + timedelta(days=run),
            base_features={},
            label=seat % 2,
            input_ccu=float(seat),
        )
        for run in range(10)
        for seat in range(5)
    ]
    train, dev, test = grouped_time_split(examples)

    assert train and dev and test
    assert sorted(train + dev + test) == list(range(len(examples)))
    homes: dict[str, set[str]] = {}
    for name, indexes in (("train", train), ("dev", dev), ("test", test)):
        for index in indexes:
            homes.setdefault(examples[index].run_id, set()).add(name)
    leaking = {run: splits for run, splits in homes.items() if len(splits) > 1}
    assert not leaking, f"runs split across sets: {leaking}"
    # And the splits stay in discovery order.
    assert max(examples[i].created_at for i in train) <= min(examples[i].created_at for i in dev)
    assert max(examples[i].created_at for i in dev) <= min(examples[i].created_at for i in test)
