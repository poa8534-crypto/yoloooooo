from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    event,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from .db import Base


def uid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class RunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class DecisionKind(str, enum.Enum):
    COLLECTION_ONLY = "collection_only"
    RESEARCH_MORE = "research_more"
    RECOMMEND = "recommend"
    BLOCKED_CONFLICT = "blocked_conflict"


class ResearchRun(Base):
    __tablename__ = "research_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    niche: Mapped[str] = mapped_column(String(240), index=True)
    status: Mapped[str] = mapped_column(String(32), default=RunStatus.QUEUED.value)
    message: Mapped[str] = mapped_column(Text, default="Queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    candidates: Mapped[list[Candidate]] = relationship(back_populates="run")


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), index=True)
    external_kind: Mapped[str] = mapped_column(String(32), default="roblox_universe")
    external_id: Mapped[str] = mapped_column(String(80), index=True)
    display_name_observation_id: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    run: Mapped[ResearchRun] = relationship(back_populates="candidates")


class ResearchCheckpoint(Base):
    __tablename__ = "research_checkpoints"
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), primary_key=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchReport(Base):
    __tablename__ = "research_reports"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditRecord(Base):
    __tablename__ = "audit_records"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), index=True)
    proposal_id: Mapped[str | None] = mapped_column(ForeignKey("proposals.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScoutAuditRun(Base):
    """Tracks the lifecycle of an in-flight Venture Scout audit.

    Like ResearchRun, this is a mutable state tracker that moves from
    running -> complete, blocked, or interrupted across service restarts.
    """
    __tablename__ = "scout_audit_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    message: Mapped[str] = mapped_column(Text, default="Venture Scout audit requested")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditActivityEvent(Base):
    """One immutable milestone or gate result emitted during a Scout audit.

    Append-only: this forms the durable forensic trail for live activity
    and replaying past audits. It carries descriptions of work, never model output.
    """
    __tablename__ = "audit_activity_events"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    sequence: Mapped[int] = mapped_column(Integer, index=True)
    stage: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    extra_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class TrackedVideo(Base):
    __tablename__ = "tracked_videos"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), index=True)
    video_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceArtifact(Base):
    __tablename__ = "source_artifacts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    url: Mapped[str] = mapped_column(Text)
    publisher_owner: Mapped[str] = mapped_column(String(255), index=True)
    retrieval_method: Mapped[str] = mapped_column(String(80))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    content_type: Mapped[str] = mapped_column(String(120))
    raw_path: Mapped[str] = mapped_column(Text)
    source_tier: Mapped[str] = mapped_column(String(32))
    # Byte length of the captured payload, recorded at capture time. Null on
    # rows written before this column existed; the ledger is append-only, so
    # those are never back-filled and the dashboard reports them as unmeasured.
    raw_size: Mapped[int | None] = mapped_column(Integer, default=None)
    is_discovery_only: Mapped[bool] = mapped_column(Boolean, default=False)
    observations: Mapped[list[Observation]] = relationship(back_populates="artifact")


class Observation(Base):
    __tablename__ = "observations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("source_artifacts.id"), index=True)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("candidates.id"), index=True)
    metric: Mapped[str] = mapped_column(String(100), index=True)
    value_json: Mapped[Any] = mapped_column(JSON)
    unit: Mapped[str | None] = mapped_column(String(40))
    extraction_method: Mapped[str] = mapped_column(String(80))
    pointer: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    # Set for every observation whose candidate link came from the association
    # engine. A YouTube or web metric without one cannot reach downstream
    # scoring: see app.association.service.approved_association_ids.
    association_id: Mapped[str | None] = mapped_column(String, index=True, default=None)
    artifact: Mapped[SourceArtifact] = relationship(back_populates="observations")


class Fact(Base):
    __tablename__ = "facts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    template_id: Mapped[str] = mapped_column(String(80))
    slot_observation_ids: Mapped[dict[str, str]] = mapped_column(JSON)
    source_ids: Mapped[list[str]] = mapped_column(JSON)
    freshness: Mapped[str] = mapped_column(String(32))
    verification_state: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), index=True)
    agent: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    model_name: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Inference(Base):
    __tablename__ = "inferences"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), index=True)
    kind: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    supporting_fact_ids: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScoreRecord(Base):
    __tablename__ = "score_records"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), index=True)
    value: Mapped[float] = mapped_column(Float)
    features: Mapped[dict[str, float]] = mapped_column(JSON)
    model_version: Mapped[str] = mapped_column(String(100))
    threshold: Mapped[float] = mapped_column(Float)
    dataset_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConfidenceRecord(Base):
    __tablename__ = "confidence_records"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), index=True)
    value: Mapped[float] = mapped_column(Float)
    coverage: Mapped[float] = mapped_column(Float)
    authority: Mapped[float] = mapped_column(Float)
    freshness: Mapped[float] = mapped_column(Float)
    independence: Mapped[float] = mapped_column(Float)
    consistency: Mapped[float] = mapped_column(Float)
    failure_reasons: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DecisionRecord(Base):
    __tablename__ = "decision_records"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    rationale_codes: Mapped[list[str]] = mapped_column(JSON)
    score_id: Mapped[str | None] = mapped_column(ForeignKey("score_records.id"))
    confidence_id: Mapped[str | None] = mapped_column(ForeignKey("confidence_records.id"))
    threshold_version: Mapped[str] = mapped_column(String(80), default="collection-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DecisionOverride(Base):
    __tablename__ = "decision_overrides"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decision_records.id"), index=True)
    requested_kind: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SystemState(Base):
    __tablename__ = "system_state"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value_json: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssociationOutcomeKind(str, enum.Enum):
    AUTO_ASSOCIATE = "auto_associate"
    REVIEW_REQUIRED = "review_required"
    NO_MATCH = "no_match"
    BLOCKED_CONFLICT = "blocked_conflict"


class ReviewVerdict(str, enum.Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REASSIGNED = "reassigned"


class MatcherVersion(Base):
    """One frozen matcher configuration.

    A row is written the first time a given fingerprint decides anything, so a
    stored verdict can always be traced back to the exact policy that produced
    it, including whether that policy had cleared its benchmark.
    """

    __tablename__ = "matcher_versions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    matcher_version: Mapped[str] = mapped_column(String(80), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    feature_schema_version: Mapped[str] = mapped_column(String(80))
    normalization_version: Mapped[str] = mapped_column(String(80))
    threshold_version: Mapped[str] = mapped_column(String(80))
    weights_version: Mapped[str] = mapped_column(String(80), default="weights-v1")
    embedding_model: Mapped[str] = mapped_column(String(160), default="")
    embedding_model_hash: Mapped[str] = mapped_column(String(64), default="")
    dataset_hash: Mapped[str] = mapped_column(String(64), default="")
    feature_order: Mapped[list[str]] = mapped_column(JSON, default=list)
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validated: Mapped[bool] = mapped_column(Boolean, default=False)
    fuzzy_auto_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    heldout_precision: Mapped[float | None] = mapped_column(Float)
    heldout_decisions: Mapped[int | None] = mapped_column(Integer)
    benchmark_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MatchSubject(Base):
    """A source record that may belong to a Roblox experience.

    Identity is this row's internal ID. `external_id` is the platform's own ID
    when one was captured; a display name alone never identifies a subject.
    """

    __tablename__ = "match_subjects"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    subject_type: Mapped[str] = mapped_column(String(40), index=True)
    external_kind: Mapped[str] = mapped_column(String(40), default="")
    external_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    raw_title: Mapped[str] = mapped_column(Text, default="")
    raw_description: Mapped[str] = mapped_column(Text, default="")
    raw_url: Mapped[str] = mapped_column(Text, default="")
    creator_name: Mapped[str] = mapped_column(String(255), default="")
    creator_external_id: Mapped[str] = mapped_column(String(120), default="")
    niche: Mapped[str] = mapped_column(String(240), default="")
    source_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_artifacts.id"), index=True
    )
    source_artifact_sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    extraction_method: Mapped[str] = mapped_column(String(80), default="")
    source_tier: Mapped[str] = mapped_column(String(32), default="")
    pointer_prefix: Mapped[str] = mapped_column(Text, default="")
    content_fingerprint: Mapped[str] = mapped_column(String(64), default="", index=True)
    normalization_version: Mapped[str] = mapped_column(String(40), default="")
    normalized_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Captured text that looked like an instruction. Stored for auditing only;
    # nothing in the engine reads it as a command.
    untrusted_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    duplicate_of_subject_id: Mapped[str | None] = mapped_column(String, index=True)
    # Set when the source text changed after an earlier capture. The old
    # row stays exactly as it was so the verdict recorded against it still
    # points at the text that produced it.
    supersedes_subject_id: Mapped[str | None] = mapped_column(String, index=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MatchCandidate(Base):
    """A Roblox experience (or niche cluster) a subject may belong to."""

    __tablename__ = "match_candidates"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    candidate_type: Mapped[str] = mapped_column(String(40), default="roblox_experience", index=True)
    candidate_row_id: Mapped[str | None] = mapped_column(ForeignKey("candidates.id"), index=True)
    universe_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    place_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    raw_name: Mapped[str] = mapped_column(Text, default="")
    raw_description: Mapped[str] = mapped_column(Text, default="")
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    creator_name: Mapped[str] = mapped_column(String(255), default="")
    creator_external_id: Mapped[str] = mapped_column(String(120), default="")
    niche_keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Duplicate experiences collapse onto one canonical candidate.
    canonical_candidate_id: Mapped[str] = mapped_column(String, default="", index=True)
    normalization_version: Mapped[str] = mapped_column(String(40), default="")
    normalized_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssociationRecord(Base):
    """The engine's verdict for one subject. Append-only, never edited.

    A human review is a separate row that annotates this one. The original
    verdict, its inputs and the policy that produced it stay exactly as written.
    """

    __tablename__ = "association_records"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    subject_id: Mapped[str] = mapped_column(ForeignKey("match_subjects.id"), index=True)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("match_candidates.id"), index=True)
    runner_up_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("match_candidates.id"), index=True
    )
    outcome: Mapped[str] = mapped_column(String(32), index=True)
    rationale_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    features: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    feature_availability: Mapped[dict[str, bool]] = mapped_column(JSON, default=dict)
    feature_order: Mapped[list[str]] = mapped_column(JSON, default=list)
    candidate_scoreboard: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    top_score: Mapped[float] = mapped_column(Float, default=0.0)
    runner_up_score: Mapped[float] = mapped_column(Float, default=0.0)
    margin: Mapped[float] = mapped_column(Float, default=0.0)
    required_coverage: Mapped[float] = mapped_column(Float, default=0.0)
    matcher_version: Mapped[str] = mapped_column(String(80), index=True)
    matcher_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("matcher_versions.id"), index=True
    )
    feature_schema_version: Mapped[str] = mapped_column(String(80))
    normalization_version: Mapped[str] = mapped_column(String(80))
    threshold_version: Mapped[str] = mapped_column(String(80))
    embedding_model: Mapped[str] = mapped_column(String(160), default="")
    # SHA-256 of the model *identifier*, not of the weight bytes. It pins
    # which model was declared, not which weights ran.
    embedding_model_hash: Mapped[str] = mapped_column(String(64), default="")
    embedding_available: Mapped[bool] = mapped_column(Boolean, default=False)
    source_artifact_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_artifact_hashes: Mapped[list[str]] = mapped_column(JSON, default=list)
    shadow_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    validated_matcher: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AssociationReview(Base):
    """A human annotation of one association record. Append-only.

    A review never rewrites the engine verdict; it records what a person
    decided alongside it, and becomes labeled data for the benchmark.
    """

    __tablename__ = "association_reviews"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    association_id: Mapped[str] = mapped_column(ForeignKey("association_records.id"), index=True)
    # Monotonic per association. The wall clock cannot order these: on Windows
    # two back-to-back writes routinely carry an identical timestamp, and a
    # rejection must never lose a tiebreak to an earlier approval.
    sequence: Mapped[int] = mapped_column(Integer, default=1, index=True)
    verdict: Mapped[str] = mapped_column(String(32), index=True)
    selected_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("match_candidates.id"), index=True
    )
    engine_outcome: Mapped[str] = mapped_column(String(32))
    engine_candidate_id: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(String(120), default="local-operator")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AssociationOverride(Base):
    """A human override of an engine verdict. Append-only.

    Separate from a review so that "I disagree with the engine" is
    distinguishable in the ledger from "I confirmed what the engine said".
    """

    __tablename__ = "association_overrides"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    association_id: Mapped[str] = mapped_column(ForeignKey("association_records.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=1, index=True)
    requested_outcome: Mapped[str] = mapped_column(String(32))
    requested_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("match_candidates.id"), index=True
    )
    engine_outcome: Mapped[str] = mapped_column(String(32))
    engine_candidate_id: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(String(120), default="local-operator")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class MarketSample(Base):
    """One sampling of Roblox's own front page.

    Deliberately separate from `facts`. A fact is a claim about one tracked
    entity, resolved through an approved association and answerable by ID. A
    market sample is a census: three hundred games Roblox itself ranked at one
    moment, none of which the run asked about. Writing those as facts would
    make "fact" mean two different things, and would attach hundreds of
    candidates per sample to a table that exists to hold games under study.

    Every row still points at the artifact it was read from, so a row is as
    traceable as a fact is; it just is not one.
    """

    __tablename__ = "market_samples"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("source_artifacts.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    sort_id: Mapped[str] = mapped_column(String(80), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    universe_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(Text)
    player_count: Mapped[int] = mapped_column(Integer)
    up_votes: Mapped[int] = mapped_column(Integer)
    down_votes: Mapped[int] = mapped_column(Integer)
    genre: Mapped[str] = mapped_column(String(80), index=True, default="")
    sponsored: Mapped[bool] = mapped_column(Boolean, default=False)


APPEND_ONLY = (
    ResearchReport, AuditRecord, AuditActivityEvent, MarketSample,
    SourceArtifact, Observation, Fact, Proposal, Inference,
    ScoreRecord, ConfidenceRecord, DecisionRecord, DecisionOverride,
    AssociationRecord, AssociationReview, AssociationOverride, MatcherVersion,
)


APPEND_ONLY_TABLES = frozenset(model.__tablename__ for model in APPEND_ONLY)


def _refuse_mutation(_mapper, _connection, target) -> None:
    raise ValueError(f"{type(target).__name__} is append-only")


for _model in APPEND_ONLY:
    event.listen(_model, "before_update", _refuse_mutation)
    event.listen(_model, "before_delete", _refuse_mutation)


@event.listens_for(Session, "do_orm_execute")
def _refuse_bulk_mutation(state) -> None:
    """Refuse bulk UPDATE/DELETE against an append-only table.

    The per-instance hooks above never see `session.execute(update(...))`.
    Database triggers are the real backstop (see `app.migrations`); this turns
    the resulting abort into a clear error at the point of the mistake.
    """
    if not (state.is_update or state.is_delete):
        return
    for mapper in state.all_mappers:
        if mapper.local_table is not None and mapper.local_table.name in APPEND_ONLY_TABLES:
            raise ValueError(f"{mapper.local_table.name} is append-only")

