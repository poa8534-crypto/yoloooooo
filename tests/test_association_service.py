"""Append-only persistence, human review, and the downstream gate."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError

from app.association import (
    AssociationService,
    MatchCandidateView,
    MatchSubjectView,
    Thresholds,
    is_association_usable,
    is_downstream_admissible,
    resolved_candidate_id,
)
from app.association.features import FEATURE_NAMES
from app.association.service import human_confirmation
from app.models import (
    AssociationOverride,
    AssociationRecord,
    AssociationReview,
    MatchCandidate,
    MatcherVersion,
    MatchSubject,
)

GARDEN = MatchCandidateView(
    candidate_id="c-garden", universe_id="1001", place_ids=("1101",),
    raw_name="Grow a Garden", creator_name="Lantern Studio",
)
TYCOON = MatchCandidateView(
    candidate_id="c-tycoon", universe_id="1003", place_ids=("1103",),
    raw_name="Garden Life Tycoon", creator_name="Mossbank",
)
POOL = [GARDEN, TYCOON]


def subject(title: str, description: str = "a captured description", subject_id="s-1", **kw):
    return MatchSubjectView(
        subject_id=subject_id,
        subject_type="youtube_video",
        external_id=kw.pop("external_id", "vid-1"),
        raw_title=title,
        raw_description=description,
        creator_name=kw.pop("creator_name", "SomeChannel"),
        source_artifact_sha256="a" * 64,
        extraction_method="youtube_videos_api",
        source_tier="primary",
        pointer_prefix="/items/0",
        **kw,
    )


@pytest.fixture
def service():
    return AssociationService(shadow_mode=True)


def test_every_proposal_is_recorded_with_its_full_input(db, service):
    decision = service.associate(
        db, subject("Grow a Garden full guide"), POOL, niche="cozy gardening"
    )
    record = decision.record
    assert record.outcome == "review_required"
    assert list(record.feature_order) == list(FEATURE_NAMES)
    assert set(record.features) == set(FEATURE_NAMES)
    assert record.feature_availability
    assert record.candidate_scoreboard
    assert record.source_artifact_hashes == ["a" * 64]
    assert record.matcher_version and record.feature_schema_version
    assert record.normalization_version and record.threshold_version
    assert record.margin == pytest.approx(record.top_score - record.runner_up_score)
    assert record.shadow_mode is True


def test_the_matcher_version_is_registered_once_and_linked(db, service):
    service.associate(db, subject("Grow a Garden", subject_id="s-1", external_id="v1"), POOL)
    service.associate(db, subject("Garden Life Tycoon", subject_id="s-2", external_id="v2"), POOL)
    versions = list(db.scalars(select(MatcherVersion)))
    assert len(versions) == 1
    records = list(db.scalars(select(AssociationRecord)))
    assert {record.matcher_version_id for record in records} == {versions[0].id}


def test_subjects_and_candidates_persist_with_internal_ids(db, service):
    service.associate(db, subject("Grow a Garden"), POOL)
    stored = db.get(MatchSubject, "s-1")
    assert stored.external_id == "vid-1"
    assert stored.normalized_json["name_key"] == "grow a garden"
    assert {row.id for row in db.scalars(select(MatchCandidate))} == {"c-garden", "c-tycoon"}


def test_an_engine_record_can_never_be_edited(db, service):
    decision = service.associate(db, subject("Grow a Garden"), POOL)
    db.commit()
    decision.record.outcome = "auto_associate"
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()


def test_a_review_annotates_and_never_overwrites_the_verdict(db, service):
    decision = service.associate(db, subject("Grow a Garden full guide"), POOL)
    original_outcome = decision.record.outcome
    original_candidate = decision.record.candidate_id
    review, override = service.record_review(
        db, decision.record.id, verdict="approved",
        reason="Checked the video; it is unambiguously this experience.",
    )
    db.commit()
    stored = db.get(AssociationRecord, decision.record.id)
    assert stored.outcome == original_outcome
    assert stored.candidate_id == original_candidate
    assert review.engine_outcome == original_outcome
    # Approving something the engine sent to review is a disagreement, so it is
    # also recorded as an override.
    assert override is not None
    assert override.engine_outcome == original_outcome


def test_reviews_accumulate_rather_than_replace(db, service):
    decision = service.associate(db, subject("Grow a Garden full guide"), POOL)
    service.record_review(
        db, decision.record.id, verdict="approved", reason="First pass looked correct."
    )
    service.record_review(
        db, decision.record.id, verdict="rejected",
        reason="Second look: the video is about a different experience.",
    )
    db.commit()
    reviews = list(db.scalars(select(AssociationReview)))
    assert len(reviews) == 2
    # The most recent review governs, but both remain in the ledger.
    assert resolved_candidate_id(db, decision.record) is None


def test_review_order_survives_an_identical_timestamp(db, service):
    """Two reviews in the same clock tick must still resolve in append order.

    On Windows `datetime.now()` returns the same value for back-to-back calls,
    so the wall clock cannot break the tie; the append sequence does.
    """
    decision = service.associate(db, subject("Grow a Garden full guide"), POOL)
    tied = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)
    # Inserted directly with a deliberately identical timestamp: the rows are
    # append-only, so the tie has to be created at insert time, which is also
    # exactly how the real clock produces it on this machine.
    approved = AssociationReview(
        association_id=decision.record.id, sequence=1, verdict="approved",
        selected_candidate_id="c-garden", engine_outcome=decision.record.outcome,
        engine_candidate_id=decision.record.candidate_id,
        reason="First pass looked correct.", created_at=tied,
    )
    rejected = AssociationReview(
        association_id=decision.record.id, sequence=2, verdict="rejected",
        selected_candidate_id=None, engine_outcome=decision.record.outcome,
        engine_candidate_id=decision.record.candidate_id,
        reason="Second look: this is a different experience entirely.", created_at=tied,
    )
    db.add_all([approved, rejected])
    db.commit()
    assert approved.created_at == rejected.created_at
    assert human_confirmation(db, decision.record.id).id == rejected.id
    assert resolved_candidate_id(db, decision.record) is None
    assert is_association_usable(db, decision.record) is False


def test_the_ledger_refuses_bulk_and_raw_rewrites(db, service):
    """Append-only has to hold against every writer, not just the ORM.

    Attribute writes raise in the ORM; bulk statements and raw SQL are stopped
    by database triggers.
    """
    decision = service.associate(db, subject("Grow a Garden full guide"), POOL)
    db.commit()
    record_id = decision.record.id

    with pytest.raises(ValueError, match="append-only"):
        db.execute(
            update(AssociationRecord)
            .where(AssociationRecord.id == record_id)
            .values(outcome="auto_associate")
        )
    db.rollback()

    with pytest.raises(ValueError, match="append-only"):
        db.execute(delete(AssociationRecord).where(AssociationRecord.id == record_id))
    db.rollback()

    with pytest.raises(IntegrityError, match="append-only"):
        db.execute(text(
            "UPDATE association_records SET outcome='auto_associate' WHERE id=:id"
        ), {"id": record_id})
        db.commit()
    db.rollback()

    with pytest.raises(IntegrityError, match="append-only"):
        db.execute(text("DELETE FROM association_records WHERE id=:id"), {"id": record_id})
        db.commit()
    db.rollback()

    assert db.get(AssociationRecord, record_id).outcome == "review_required"


def test_a_review_is_itself_append_only(db, service):
    decision = service.associate(db, subject("Grow a Garden full guide"), POOL)
    review, _ = service.record_review(
        db, decision.record.id, verdict="approved", reason="Confirmed by hand."
    )
    db.commit()
    review.reason = "changed my mind"
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()


def test_an_override_requires_a_reason(db, service):
    decision = service.associate(db, subject("Grow a Garden full guide"), POOL)
    with pytest.raises(ValueError, match="at least 10 characters"):
        service.record_review(db, decision.record.id, verdict="approved", reason="ok")


def test_selecting_another_candidate_records_an_override(db, service):
    decision = service.associate(db, subject("Grow a Garden full guide"), POOL)
    review, override = service.record_review(
        db, decision.record.id, verdict="reassigned",
        selected_candidate_id="c-tycoon",
        reason="The footage is actually from the tycoon experience.",
    )
    db.commit()
    assert review.selected_candidate_id == "c-tycoon"
    assert override.requested_candidate_id == "c-tycoon"
    assert resolved_candidate_id(db, decision.record) == "c-tycoon"
    assert len(list(db.scalars(select(AssociationOverride)))) == 1


def test_a_reassignment_must_name_a_candidate(db, service):
    decision = service.associate(db, subject("Grow a Garden full guide"), POOL)
    with pytest.raises(ValueError, match="must name the candidate"):
        service.record_review(
            db, decision.record.id, verdict="reassigned",
            reason="This belongs somewhere else entirely.",
        )


# --- The downstream gate -----------------------------------------------------

def test_a_fuzzy_review_verdict_is_not_usable_downstream(db, service):
    decision = service.associate(db, subject("Grow a Garden full guide"), POOL)
    assert decision.record.outcome == "review_required"
    assert is_downstream_admissible(decision.record) is False
    assert is_association_usable(db, decision.record) is False
    assert resolved_candidate_id(db, decision.record) is None


def test_an_exact_id_match_is_usable_even_though_the_matcher_is_unvalidated(db, service):
    decision = service.associate(
        db, subject("Huge update", "https://www.roblox.com/games/1101/Grow-a-Garden"), POOL
    )
    assert decision.record.outcome == "auto_associate"
    assert decision.record.validated_matcher is False
    assert is_downstream_admissible(decision.record) is True
    assert resolved_candidate_id(db, decision.record) == "c-garden"


def test_an_unvalidated_fuzzy_auto_verdict_is_refused_downstream(db):
    # A matcher that would auto-associate on text alone but has not passed its
    # benchmark: the verdict is recorded, and downstream still refuses it.
    unvalidated = replace(Thresholds(), fuzzy_auto_enabled=True, validated=False)
    service = AssociationService(thresholds=unvalidated, shadow_mode=False)
    decision = service.associate(db, subject("Grow a Garden"), POOL)
    assert decision.record.outcome == "auto_associate"
    assert "exact_verified_id_evidence" not in decision.record.rationale_codes
    assert is_downstream_admissible(decision.record) is False


def test_a_validated_matcher_makes_a_fuzzy_auto_verdict_usable(db):
    validated = replace(Thresholds(), fuzzy_auto_enabled=True, validated=True)
    service = AssociationService(thresholds=validated, shadow_mode=False)
    decision = service.associate(db, subject("Grow a Garden"), POOL)
    assert decision.record.outcome == "auto_associate"
    assert is_downstream_admissible(decision.record) is True


def test_human_approval_makes_a_reviewed_association_usable(db, service):
    decision = service.associate(db, subject("Grow a Garden full guide"), POOL)
    service.record_review(
        db, decision.record.id, verdict="approved",
        reason="Watched it; this is the right experience.",
    )
    db.commit()
    assert is_association_usable(db, decision.record) is True
    assert resolved_candidate_id(db, decision.record) == "c-garden"


def test_human_rejection_overrides_an_automatic_association(db, service):
    decision = service.associate(
        db, subject("Huge update", "https://www.roblox.com/games/1101/Grow-a-Garden"), POOL
    )
    assert is_association_usable(db, decision.record) is True
    service.record_review(
        db, decision.record.id, verdict="rejected",
        reason="The link is a sponsor link, not the subject of the video.",
    )
    db.commit()
    assert is_association_usable(db, decision.record) is False
    assert resolved_candidate_id(db, decision.record) is None


def test_pending_reviews_lists_only_unreviewed_ambiguous_records(db, service):
    ambiguous = service.associate(
        db, subject("Grow a Garden full guide", subject_id="s-1", external_id="v1"), POOL
    )
    service.associate(
        db,
        subject(
            "Huge update", "https://www.roblox.com/games/1101/x",
            subject_id="s-2", external_id="v2",
        ),
        POOL,
    )
    db.commit()
    assert [record.id for record in service.pending_reviews(db)] == [ambiguous.record.id]
    service.record_review(
        db, ambiguous.record.id, verdict="approved", reason="Confirmed by hand."
    )
    db.commit()
    assert service.pending_reviews(db) == []


def test_a_duplicate_subject_is_recorded_as_a_duplicate(db, service):
    first = subject("Rainbow Ladder Obby full run", subject_id="s-1", external_id="v1")
    service.associate(db, first, POOL)
    second = subject("RAINBOW LADDER OBBY full run!", subject_id="s-2", external_id="v2")
    decision = service.associate(db, second, POOL)
    db.commit()
    assert db.get(MatchSubject, "s-2").duplicate_of_subject_id == "s-1"
    assert decision.record.outcome == "no_match"
    assert "duplicate_content_counted_once" in decision.record.rationale_codes


def test_retitled_source_gets_its_own_row_and_leaves_the_old_verdict_intact(db, service):
    """A record must always describe the text the engine actually scored."""
    first = service.associate(db, subject("Grow a Garden full guide"), POOL)
    db.commit()
    original_subject_id = first.record.subject_id

    # Same video, retitled between runs.
    second = service.associate(
        db, subject("Garden Life Tycoon staff update walkthrough"), POOL
    )
    db.commit()

    assert second.record.subject_id != original_subject_id
    new_row = db.get(MatchSubject, second.record.subject_id)
    old_row = db.get(MatchSubject, original_subject_id)
    assert new_row.supersedes_subject_id == original_subject_id
    assert new_row.external_id == old_row.external_id == "vid-1"
    # The earlier record still points at the text it was decided on.
    assert old_row.raw_title == "Grow a Garden full guide"
    assert new_row.raw_title == "Garden Life Tycoon staff update walkthrough"
    assert db.get(AssociationRecord, first.record.id).subject_id == original_subject_id


def test_unchanged_source_text_reuses_the_same_row(db, service):
    first = service.associate(db, subject("Grow a Garden full guide"), POOL)
    second = service.associate(db, subject("Grow a Garden full guide"), POOL)
    db.commit()
    assert first.record.subject_id == second.record.subject_id
    assert len(list(db.scalars(select(MatchSubject)))) == 1
