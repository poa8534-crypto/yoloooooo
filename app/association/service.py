"""The association service: the only supported way to create an association.

Callers hand it subjects and a candidate pool; it retrieves, scores, applies
the hard rules, writes one append-only `AssociationRecord`, and answers the one
question downstream code is allowed to ask: *may this association be used?*

Shadow mode (Phase 9) is on by default. While it is on, only exact verified-ID
evidence is accepted automatically, every fuzzy proposal is recorded and routed
to review, and nothing fuzzy can reach scoring or recommendations.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    AssociationOverride,
    AssociationRecord,
    AssociationReview,
    MatchCandidate,
    MatcherVersion,
    MatchSubject,
    ReviewVerdict,
)
from .decisions import (
    CODE_EXACT_ID,
    AssociationOutcome,
    AssociationVerdict,
    evaluate,
)
from .features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from .normalize import NORMALIZATION_VERSION, content_fingerprint
from .retrieval import EmbeddingProvider
from .subjects import (
    CANDIDATE_ROBLOX_EXPERIENCE,
    MatchCandidateView,
    MatchSubjectView,
)
from .thresholds import Thresholds, active_thresholds


def embedding_model_hash(model_name: str) -> str:
    """Stable hash of the embedding model identifier.

    This pins *which* model was declared, not the weight bytes; fastembed
    resolves weights by name from its own pinned revision.
    """
    return hashlib.sha256((model_name or "").encode("utf-8")).hexdigest() if model_name else ""


@dataclass(frozen=True)
class AssociationDecision:
    """What the caller gets back: the stored record plus the live verdict."""

    record: AssociationRecord
    verdict: AssociationVerdict
    subject_row: MatchSubject
    candidate_row: MatchCandidate | None

    @property
    def outcome(self) -> str:
        return self.record.outcome

    @property
    def usable_downstream(self) -> bool:
        return is_downstream_admissible(self.record)


def is_downstream_admissible(record: AssociationRecord) -> bool:
    """Whether an engine verdict alone may feed downstream metrics.

    Two ways in, and only two:

    * the verdict is `auto_associate` and rests on exact verified-ID evidence
      (deterministic identity resolution, valid even in shadow mode), or
    * the verdict is `auto_associate` from a matcher version whose fuzzy
      benchmark has been validated.

    Everything else needs a human confirmation, which lives in
    `AssociationReview` and is checked by `is_association_usable`.
    """
    if record.outcome != AssociationOutcome.AUTO_ASSOCIATE.value:
        return False
    if CODE_EXACT_ID in (record.rationale_codes or []):
        return True
    return bool(record.validated_matcher)


def human_confirmation(db: Session, association_id: str) -> AssociationReview | None:
    """The latest human review for a record, if there is one.

    Ordered by the append sequence, never by the clock: two reviews recorded in
    the same tick must still resolve in the order a person made them.
    """
    return db.scalar(
        select(AssociationReview)
        .where(AssociationReview.association_id == association_id)
        .order_by(AssociationReview.sequence.desc())
        .limit(1)
    )


def is_association_usable(db: Session, record: AssociationRecord) -> bool:
    """Whether this association may resolve a downstream metric."""
    review = human_confirmation(db, record.id)
    if review is not None:
        return review.verdict in {ReviewVerdict.APPROVED.value, ReviewVerdict.REASSIGNED.value}
    return is_downstream_admissible(record)


def resolved_candidate_id(db: Session, record: AssociationRecord) -> str | None:
    """The candidate an association resolves to, or None if it resolves to none.

    A human reassignment wins over the engine's pick; a rejection resolves to
    nothing; an unreviewed record resolves only if it is admissible on its own.
    """
    review = human_confirmation(db, record.id)
    if review is not None:
        if review.verdict == ReviewVerdict.REJECTED.value:
            return None
        return review.selected_candidate_id or record.candidate_id
    return record.candidate_id if is_downstream_admissible(record) else None


class AssociationService:
    """Persistence and policy around the deterministic engine."""

    def __init__(
        self,
        *,
        embedder: EmbeddingProvider | None = None,
        thresholds: Thresholds | None = None,
        shadow_mode: bool = True,
    ):
        self.embedder = embedder
        self._thresholds = thresholds
        self.shadow_mode = shadow_mode

    @property
    def thresholds(self) -> Thresholds:
        return self._thresholds or active_thresholds()

    # ------------------------------------------------------------------
    # Entity persistence
    # ------------------------------------------------------------------
    def register_matcher_version(self, db: Session, thresholds: Thresholds) -> MatcherVersion:
        fingerprint = thresholds.fingerprint()
        existing = db.scalar(
            select(MatcherVersion).where(MatcherVersion.fingerprint == fingerprint)
        )
        if existing is not None:
            return existing
        row = MatcherVersion(
            matcher_version=thresholds.matcher_version,
            fingerprint=fingerprint,
            feature_schema_version=thresholds.feature_schema_version,
            normalization_version=thresholds.normalization_version,
            threshold_version=thresholds.threshold_version,
            weights_version=thresholds.weights_version,
            embedding_model=thresholds.embedding_model,
            embedding_model_hash=thresholds.embedding_model_hash,
            dataset_hash=thresholds.dataset_hash,
            feature_order=list(FEATURE_NAMES),
            policy_json=thresholds.as_json(),
            validated=thresholds.validated,
            fuzzy_auto_enabled=thresholds.fuzzy_auto_enabled,
            heldout_precision=thresholds.heldout_precision,
            heldout_decisions=thresholds.heldout_decisions,
            benchmark_reason=thresholds.benchmark_reason,
        )
        db.add(row)
        db.flush()
        return row

    def upsert_subject(self, db: Session, view: MatchSubjectView) -> MatchSubject:
        """Store a subject, versioning the row whenever the source text changes.

        Reusing a row after a video was retitled would leave the stored record
        describing text that is not what the engine scored. Instead the changed
        text gets its own row, deterministically keyed by its fingerprint, and
        the previous row is left untouched for the verdict already recorded
        against it.
        """
        fingerprint = content_fingerprint(view.raw_title, view.raw_description)
        row: MatchSubject | None = db.get(MatchSubject, view.subject_id)
        if row is None and view.external_id:
            row = db.scalar(
                select(MatchSubject).where(
                    MatchSubject.subject_type == view.subject_type,
                    MatchSubject.external_id == view.external_id,
                ).order_by(MatchSubject.created_at.desc()).limit(1)
            )
        subject_id = view.subject_id
        supersedes: str | None = None
        if row is not None:
            if row.content_fingerprint == fingerprint:
                return row
            # The captured text moved on. Key the new row by its content so the
            # same text always lands on the same row.
            subject_id = f"{view.subject_id}@{fingerprint[:12]}"
            existing = db.get(MatchSubject, subject_id)
            if existing is not None:
                return existing
            supersedes = row.id
        duplicate_of = db.scalar(
            select(MatchSubject.id).where(
                MatchSubject.content_fingerprint == fingerprint,
                MatchSubject.id != subject_id,
            ).order_by(MatchSubject.created_at).limit(1)
        )
        row = MatchSubject(
            id=subject_id,
            supersedes_subject_id=supersedes,
            subject_type=view.subject_type,
            external_kind=view.subject_type,
            external_id=view.external_id,
            raw_title=view.raw_title,
            raw_description=view.raw_description,
            raw_url=view.raw_url,
            creator_name=view.creator_name,
            creator_external_id=view.creator_external_id,
            niche=view.niche,
            source_artifact_id=view.source_artifact_id or None,
            source_artifact_sha256=view.source_artifact_sha256,
            extraction_method=view.extraction_method,
            source_tier=view.source_tier,
            pointer_prefix=view.pointer_prefix,
            content_fingerprint=fingerprint,
            normalization_version=NORMALIZATION_VERSION,
            normalized_json=view.normalized.as_json(),
            untrusted_codes=list(view.normalized.injection_codes),
            duplicate_of_subject_id=duplicate_of,
            discovered_at=view.discovered_at,
        )
        db.add(row)
        db.flush()
        return row

    def upsert_candidate(
        self, db: Session, view: MatchCandidateView, *, candidate_row_id: str | None = None
    ) -> MatchCandidate:
        row: MatchCandidate | None = db.get(MatchCandidate, view.candidate_id)
        if row is not None:
            return row
        row = MatchCandidate(
            id=view.candidate_id,
            candidate_type=view.candidate_type,
            candidate_row_id=candidate_row_id,
            universe_id=view.universe_id,
            place_ids=list(view.place_ids),
            raw_name=view.raw_name,
            raw_description=view.raw_description,
            aliases=list(view.aliases),
            creator_name=view.creator_name,
            creator_external_id=view.creator_external_id,
            niche_keywords=list(view.niche_keywords),
            canonical_candidate_id=view.canonical_candidate_id,
            normalization_version=NORMALIZATION_VERSION,
            normalized_json=view.normalized.as_json(),
        )
        db.add(row)
        db.flush()
        return row

    # ------------------------------------------------------------------
    # The decision itself
    # ------------------------------------------------------------------
    def associate(
        self,
        db: Session,
        subject: MatchSubjectView,
        pool: list[MatchCandidateView],
        *,
        niche: str = "",
        candidate_row_ids: dict[str, str] | None = None,
    ) -> AssociationDecision:
        """Decide one subject and write the append-only record."""
        thresholds = self.thresholds
        matcher_row = self.register_matcher_version(db, thresholds)
        subject_row = self.upsert_subject(db, subject)
        candidate_row_ids = candidate_row_ids or {}
        for candidate in pool:
            self.upsert_candidate(
                db, candidate, candidate_row_id=candidate_row_ids.get(candidate.candidate_id)
            )

        verdict = evaluate(
            subject,
            pool,
            thresholds=thresholds,
            embedder=self.embedder,
            niche=niche or subject.niche,
            shadow_mode=self.shadow_mode,
            duplicate_of=subject_row.duplicate_of_subject_id or "",
        )

        winner = verdict.winner
        features = winner.features.values if winner else {}
        availability = winner.features.availability if winner else {}
        record = AssociationRecord(
            subject_id=subject_row.id,
            candidate_id=winner.candidate_id if winner else None,
            runner_up_candidate_id=verdict.runner_up.candidate_id if verdict.runner_up else None,
            outcome=verdict.outcome.value,
            rationale_codes=list(verdict.rationale),
            features=dict(features),
            feature_availability=dict(availability),
            feature_order=list(FEATURE_NAMES),
            candidate_scoreboard=verdict.candidate_scoreboard(),
            top_score=verdict.top_score,
            runner_up_score=verdict.runner_up_score,
            margin=verdict.margin,
            required_coverage=verdict.required_coverage,
            matcher_version=thresholds.matcher_version,
            matcher_version_id=matcher_row.id,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            normalization_version=NORMALIZATION_VERSION,
            threshold_version=thresholds.threshold_version,
            embedding_model=verdict.embedding_model or thresholds.embedding_model,
            embedding_model_hash=embedding_model_hash(
                verdict.embedding_model or thresholds.embedding_model
            ),
            embedding_available=verdict.embedding_available,
            source_artifact_ids=[subject.source_artifact_id] if subject.source_artifact_id else [],
            source_artifact_hashes=(
                [subject.source_artifact_sha256] if subject.source_artifact_sha256 else []
            ),
            shadow_mode=self.shadow_mode,
            validated_matcher=bool(thresholds.validated and thresholds.fuzzy_auto_enabled),
            created_at=datetime.now(UTC),
        )
        db.add(record)
        db.flush()
        candidate_row = db.get(MatchCandidate, record.candidate_id) if record.candidate_id else None
        return AssociationDecision(
            record=record, verdict=verdict, subject_row=subject_row, candidate_row=candidate_row
        )

    # ------------------------------------------------------------------
    # Human review, append-only
    # ------------------------------------------------------------------
    def record_review(
        self,
        db: Session,
        association_id: str,
        *,
        verdict: str,
        reason: str,
        selected_candidate_id: str | None = None,
        reviewer: str = "local-operator",
    ) -> tuple[AssociationReview, AssociationOverride | None]:
        """Annotate a record. The engine's original verdict is never touched."""
        record = db.get(AssociationRecord, association_id)
        if record is None:
            raise KeyError(association_id)
        if verdict not in {item.value for item in ReviewVerdict}:
            raise ValueError(f"unknown review verdict: {verdict}")
        cleaned = (reason or "").strip()
        if len(cleaned) < 10:
            raise ValueError("a review needs a reason of at least 10 characters")
        if verdict == ReviewVerdict.REASSIGNED.value and not selected_candidate_id:
            raise ValueError("a reassignment must name the candidate it selects")
        if selected_candidate_id and db.get(MatchCandidate, selected_candidate_id) is None:
            raise KeyError(selected_candidate_id)

        chosen = selected_candidate_id
        if verdict == ReviewVerdict.APPROVED.value:
            chosen = selected_candidate_id or record.candidate_id
            if not chosen:
                raise ValueError("there is no candidate to approve on this record")
        elif verdict == ReviewVerdict.REJECTED.value:
            chosen = None

        next_sequence = 1 + (db.scalar(
            select(func.max(AssociationReview.sequence))
            .where(AssociationReview.association_id == association_id)
        ) or 0)
        review = AssociationReview(
            association_id=association_id,
            sequence=next_sequence,
            verdict=verdict,
            selected_candidate_id=chosen,
            engine_outcome=record.outcome,
            engine_candidate_id=record.candidate_id,
            reason=cleaned,
            reviewer=reviewer,
        )
        db.add(review)

        override: AssociationOverride | None = None
        disagrees = (
            (verdict == ReviewVerdict.REJECTED.value
             and record.outcome == AssociationOutcome.AUTO_ASSOCIATE.value)
            or (verdict == ReviewVerdict.APPROVED.value
                and record.outcome != AssociationOutcome.AUTO_ASSOCIATE.value)
            or verdict == ReviewVerdict.REASSIGNED.value
        )
        if disagrees:
            override = AssociationOverride(
                association_id=association_id,
                sequence=next_sequence,
                requested_outcome=(
                    AssociationOutcome.NO_MATCH.value
                    if verdict == ReviewVerdict.REJECTED.value
                    else AssociationOutcome.AUTO_ASSOCIATE.value
                ),
                requested_candidate_id=chosen,
                engine_outcome=record.outcome,
                engine_candidate_id=record.candidate_id,
                reason=cleaned,
                reviewer=reviewer,
            )
            db.add(override)
        db.flush()
        return review, override

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def pending_reviews(self, db: Session, limit: int = 100) -> list[AssociationRecord]:
        """Records that still need a human: reviewable and not yet reviewed."""
        reviewed = select(AssociationReview.association_id)
        return list(db.scalars(
            select(AssociationRecord)
            .where(
                AssociationRecord.outcome.in_([
                    AssociationOutcome.REVIEW_REQUIRED.value,
                    AssociationOutcome.BLOCKED_CONFLICT.value,
                ]),
                AssociationRecord.id.not_in(reviewed),
            )
            .order_by(AssociationRecord.created_at.desc())
            .limit(limit)
        ))

    def usable_associations(self, db: Session) -> list[AssociationRecord]:
        return [
            record for record in db.scalars(select(AssociationRecord))
            if is_association_usable(db, record)
        ]


def usable_association_for_subject(
    db: Session, subject_type: str, external_id: str
) -> tuple[AssociationRecord, str] | None:
    """The approved association for one external source, and the candidate row.

    Used by anything that revisits a source later — the daily snapshot, for
    instance — so that a repeat measurement is attributed through the same
    approved association, and a source whose association was rejected simply
    stops being measured.
    """
    if not external_id:
        return None
    records = db.scalars(
        select(AssociationRecord)
        .join(MatchSubject, MatchSubject.id == AssociationRecord.subject_id)
        .where(
            MatchSubject.subject_type == subject_type,
            MatchSubject.external_id == external_id,
        )
        .order_by(AssociationRecord.created_at.desc(), AssociationRecord.id.desc())
    )
    for record in records:
        if not is_association_usable(db, record):
            continue
        match_candidate_id = resolved_candidate_id(db, record)
        if not match_candidate_id:
            continue
        match_candidate = db.get(MatchCandidate, match_candidate_id)
        if match_candidate is None or not match_candidate.candidate_row_id:
            continue
        return record, match_candidate.candidate_row_id
    return None


def candidate_view_from_row(row: MatchCandidate) -> MatchCandidateView:
    return MatchCandidateView(
        candidate_id=row.id,
        candidate_type=row.candidate_type or CANDIDATE_ROBLOX_EXPERIENCE,
        universe_id=row.universe_id or "",
        place_ids=tuple(row.place_ids or ()),
        raw_name=row.raw_name or "",
        raw_description=row.raw_description or "",
        aliases=tuple(row.aliases or ()),
        creator_name=row.creator_name or "",
        creator_external_id=row.creator_external_id or "",
        canonical_candidate_id=row.canonical_candidate_id or row.id,
        niche_keywords=tuple(row.niche_keywords or ()),
    )
