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


class MatchingCandidateView(StrictModel):
    candidate_id: str
    universe_id: str
    display_name: str
    score: float
    exact_id_evidence: bool = False
    hard_negative: bool = False
    retrieval_methods: list[str] = Field(default_factory=list)


class MatchingArtifactView(StrictModel):
    id: str
    url: str
    publisher_owner: str
    sha256: str
    source_tier: str
    retrieval_method: str
    captured_at: datetime


class MatchingReviewView(StrictModel):
    """Everything a reviewer needs to judge one proposed association."""

    association_id: str
    created_at: datetime
    outcome: str
    rationale_codes: list[str]
    subject_id: str
    subject_type: str
    subject_external_id: str
    subject_title: str
    subject_description: str
    subject_url: str
    subject_creator: str
    niche: str
    # Text in the captured source that looked like an instruction. Shown as a
    # warning; it has no effect on the engine's verdict.
    untrusted_codes: list[str] = Field(default_factory=list)
    duplicate_of_subject_id: str | None = None
    candidate: MatchingCandidateView | None = None
    runner_up: MatchingCandidateView | None = None
    alternatives: list[MatchingCandidateView] = Field(default_factory=list)
    features: dict[str, float] = Field(default_factory=dict)
    feature_availability: dict[str, bool] = Field(default_factory=dict)
    feature_order: list[str] = Field(default_factory=list)
    top_score: float
    runner_up_score: float
    margin: float
    required_coverage: float
    conflict_warnings: list[str] = Field(default_factory=list)
    matcher_version: str
    feature_schema_version: str
    normalization_version: str
    threshold_version: str
    embedding_model: str = ""
    embedding_model_hash: str = ""
    embedding_available: bool = False
    shadow_mode: bool = True
    validated_matcher: bool = False
    usable_downstream: bool = False
    artifacts: list[MatchingArtifactView] = Field(default_factory=list)
    review_verdict: str | None = None
    review_reason: str | None = None
    review_selected_candidate_id: str | None = None
    reviewed_at: datetime | None = None


class MatchingReviewCreate(StrictModel):
    verdict: Literal["approved", "rejected", "reassigned"]
    reason: str = Field(min_length=10, max_length=1000)
    selected_candidate_id: str | None = None
    reviewer: str = Field(default="local-operator", max_length=120)


class MatchingReviewResult(StrictModel):
    review_id: str
    association_id: str
    verdict: str
    engine_outcome: str
    engine_candidate_id: str | None
    selected_candidate_id: str | None
    reason: str
    override_id: str | None
    facts_created: list[str]
    created_at: datetime


class MatchingStatus(StrictModel):
    matcher_version: str
    feature_schema_version: str
    normalization_version: str
    threshold_version: str
    weights_version: str
    shadow_mode: bool
    fuzzy_auto_enabled: bool
    validated: bool
    high_threshold: float
    low_threshold: float
    margin_threshold: float
    min_required_coverage: float
    heldout_precision: float | None = None
    heldout_decisions: int | None = None
    dataset_hash: str = ""
    embedding_model: str = ""
    benchmark_reason: str
    # Non-empty when a stored policy artifact was refused and the engine
    # fell back to shipped defaults.
    artifact_error: str = ""
    pending_reviews: int
    total_associations: int
    outcome_counts: dict[str, int] = Field(default_factory=dict)


class AuditGate(StrictModel):
    label: str
    passed: bool
    detail: str


class AuditReadiness(StrictModel):
    """Computed pre-audit state for one candidate."""

    candidate_id: str
    ready: bool
    # Evaluated per candidate against the ledger.
    gates: list[AuditGate] = Field(default_factory=list)
    # Always-on properties of the pipeline, not per-candidate measurements.
    invariants: list[AuditGate] = Field(default_factory=list)


class AuditView(StrictModel):
    candidate_id: str
    evidence_state: str
    proposal: ProposalPayload | None
    risks: list[str]
    decision: str
    note: str
