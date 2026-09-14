from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FORBIDDEN_PROPOSAL_TEXT = re.compile(
    r"(?:https?://|www\.|\b\d+(?:[.,]\d+)?\s*(?:%|[kmb]\b|players?\b|visits?\b|views?\b|hours?\b|days?\b))",
    re.IGNORECASE,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResearchRunCreate(StrictModel):
    niche: str = Field(min_length=3, max_length=240)


class ProposalPayload(StrictModel):
    concept_title: str = Field(min_length=3, max_length=100)
    core_loop: str = Field(min_length=10, max_length=500)
    differentiator: str = Field(min_length=10, max_length=500)
    build_steps: list[str] = Field(min_length=1, max_length=8)
    risks: list[str] = Field(min_length=1, max_length=8)
    questions: list[str] = Field(default_factory=list, max_length=8)
    supporting_fact_ids: list[str] = Field(default_factory=list)

    @field_validator("concept_title", "core_loop", "differentiator")
    @classmethod
    def no_untrusted_metrics(cls, value: str) -> str:
        if FORBIDDEN_PROPOSAL_TEXT.search(value):
            raise ValueError("proposal text may not contain URLs or metric-like claims")
        return value.strip()

    @field_validator("build_steps", "risks", "questions")
    @classmethod
    def no_untrusted_metrics_in_lists(cls, values: list[str]) -> list[str]:
        if any(FORBIDDEN_PROPOSAL_TEXT.search(v) for v in values):
            raise ValueError("proposal list may not contain URLs or metric-like claims")
        return [v.strip() for v in values]


class OverrideCreate(StrictModel):
    requested_kind: Literal["research_more", "recommend", "blocked_conflict"]
    reason: str = Field(min_length=10, max_length=1000)


class FactView(StrictModel):
    id: str
    text: str
    source_ids: list[str]
    freshness: str
    verification_state: str


class CandidateView(StrictModel):
    id: str
    external_id: str
    display_name: str
    facts: list[FactView]
    proposal: ProposalPayload | None
    decision: str
    decision_id: str | None
    score: float | None
    confidence: float | None


class RunView(StrictModel):
    id: str
    niche: str
    status: str
    message: str
    created_at: datetime
    completed_at: datetime | None
    candidates: list[CandidateView]
    passing_results: list[CandidateView]


class CalibrationStatus(StrictModel):
    phase: str
    complete_clusters: int
    required_clusters: int
    scoring_active: bool
    model_version: str | None = None
    heldout_precision: float | None = None
    heldout_recommendations: int | None = None
    reason: str


class AuditView(StrictModel):
    candidate_id: str
    evidence_state: str
    proposal: ProposalPayload | None
    risks: list[str]
    decision: str
    note: str
