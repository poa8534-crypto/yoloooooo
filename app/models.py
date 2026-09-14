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
    String,
    Text,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

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


APPEND_ONLY = (
    SourceArtifact, Observation, Fact, Proposal, Inference,
    ScoreRecord, ConfidenceRecord, DecisionRecord, DecisionOverride,
)


def _refuse_mutation(_mapper, _connection, target) -> None:
    raise ValueError(f"{type(target).__name__} is append-only")


for _model in APPEND_ONLY:
    event.listen(_model, "before_update", _refuse_mutation)
    event.listen(_model, "before_delete", _refuse_mutation)

