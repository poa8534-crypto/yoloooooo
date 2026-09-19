from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Prose may carry a digit in exactly two shapes, and nothing else.
#
# A milestone label: "Day 1", "Step 2", "Phase 3".
_ORDINAL_LABEL = re.compile(
    r"\b(?:day|days|step|steps|phase|phases|milestone|milestones|part|parts|week|weeks)\s*#?\s*\d{1,2}\b",
    re.IGNORECASE,
)
# A genre token where the digit is part of the word, not a quantity.
_GENRE_TOKEN = re.compile(r"\b(?:[23]-?d|\dv\d|f2p|p2w)\b", re.IGNORECASE)

_LINK = re.compile(
    r"https?://|www\.|\b[\w-]+\.(?:com|org|net|io|gg|dev|xyz|co)\b",
    re.IGNORECASE,
)
_DIGIT = re.compile(r"\d")


def contains_unsupported_measurement(value: str) -> bool:
    """Is there a link, or a number that is not a milestone label or genre token?

    This used to be a blacklist of metric words, which could not win. It passed
    "The game has 80 daily sessions." because "sessions" was not on the list,
    and every fix invited the next uncovered noun.

    The rule is inverted now: prose may contain a digit only in a shape that
    cannot be a measurement. Everything else has to be written in words, or put
    in `design_assumptions`, where a quantity is typed and labelled as
    unverified rather than reading as a finding.
    """
    if _LINK.search(value):
        return True
    remaining = _GENRE_TOKEN.sub(" ", _ORDINAL_LABEL.sub(" ", value))
    return bool(_DIGIT.search(remaining))


class _Firewall:
    """Adapter so existing callers can keep using `.search(...)`."""

    @staticmethod
    def search(value: str):
        return contains_unsupported_measurement(value) or None


FORBIDDEN_PROPOSAL_TEXT = _Firewall


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResearchRunCreate(StrictModel):
    # Characters, not words, which is the whole reason the old limit bit: 240
    # characters is about forty words, and a brief describing a game runs to
    # hundreds. Twelve thousand holds a thousand words of any prose -- measured
    # rather than assumed, because a thousand words of ordinary English is
    # nearer 6,000 characters and a thousand of longer ones is nearer 8,000.
    niche: str = Field(min_length=3, max_length=12000)
    mode: Literal["quick", "deep"] = "quick"


class ScoutQueueRun(StrictModel):
    """The concepts the operator chose to audit.

    Capped because each one spends several minutes of a model that runs a
    single request at a time: a selection of two hundred is a mistake, not an
    instruction, and it would be discovered hours later.
    """

    proposal_ids: list[str] = Field(min_length=1, max_length=25)


class DesignAssumption(StrictModel):
    kind: Literal["session_length", "implementation_effort", "feature_count"]
    value: float = Field(gt=0, le=10000, allow_inf_nan=False)
    unit: Literal["minutes", "hours", "features"]
    basis: Literal["unverified_design_assumption"] = "unverified_design_assumption"


class ProposalPayload(StrictModel):
    # Prose limits are generous because a Venture Scout audit is meant to read
    # like an analyst brief, not a caption. The firewall that matters is the
    # validator below, which refuses URLs and metric-like claims at any length.
    concept_title: str = Field(min_length=3, max_length=100)
    core_loop: str = Field(min_length=10, max_length=2000)
    differentiator: str = Field(min_length=10, max_length=2000)
    build_steps: list[str] = Field(min_length=1, max_length=12)
    risks: list[str] = Field(min_length=1, max_length=12)
    questions: list[str] = Field(default_factory=list, max_length=12)
    supporting_fact_ids: list[str] = Field(default_factory=list)
    design_assumptions: list[DesignAssumption] = Field(default_factory=list, max_length=8)
    essential_features: list[str] = Field(default_factory=list, max_length=12)
    excluded_features: list[str] = Field(default_factory=list, max_length=12)
    dependencies: list[str] = Field(default_factory=list, max_length=12)
    validation_tasks: list[str] = Field(default_factory=list, max_length=12)
    counterevidence: list[str] = Field(default_factory=list, max_length=12)
    # Authored only by a Venture Scout audit; a Hunter concept leaves them empty.
    executive_summary: str = Field(default="", max_length=2500)
    opportunity_gap: str = Field(default="", max_length=2000)
    competitive_notes: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("concept_title", "core_loop", "differentiator", "executive_summary", "opportunity_gap")
    @classmethod
    def no_untrusted_metrics(cls, value: str) -> str:
        if FORBIDDEN_PROPOSAL_TEXT.search(value):
            raise ValueError("proposal text may not contain URLs or metric-like claims")
        return value.strip()

    @field_validator("build_steps", "risks", "questions", "essential_features", "excluded_features", "dependencies", "validation_tasks", "counterevidence", "competitive_notes")
    @classmethod
    def no_untrusted_metrics_in_lists(cls, values: list[str]) -> list[str]:
        if any(FORBIDDEN_PROPOSAL_TEXT.search(v) for v in values):
            raise ValueError("proposal list may not contain URLs or metric-like claims")
        return [v.strip() for v in values]

    @field_validator("supporting_fact_ids")
    @classmethod
    def valid_ids(cls, values):
        from uuid import UUID
        for value in values:
            UUID(value)
        return values


class SearchPlan(StrictModel):
    """Search queries, not evidence.

    Nothing here becomes a fact: these are the words typed into a search box,
    and every result still passes the whole capture and association pipeline.
    The validator keeps links and operators out, because the caller adds the
    site restriction itself and a model-authored URL is never followed.
    """

    queries: list[str] = Field(min_length=2, max_length=10)

    @field_validator("queries")
    @classmethod
    def plain_search_terms(cls, values: list[str]) -> list[str]:
        for value in values:
            if not 2 <= len(value.strip()) <= 80:
                raise ValueError("a search query must be short and non-empty")
            if re.search(r"https?://|www\.|site:", value, re.IGNORECASE):
                raise ValueError("a search query may not contain a link or a site operator")
        return [" ".join(value.split()) for value in values]


class AuditCritique(StrictModel):
    """One deliberation pass: what is wrong with the draft the model just wrote.

    Kept separate from `ProposalPayload` so the critique cannot be mistaken for
    a proposal, and so a weak critique fails on its own terms rather than
    silently producing a weak revision.
    """

    weaknesses: list[str] = Field(min_length=1, max_length=8)
    missing_dependencies: list[str] = Field(default_factory=list, max_length=8)
    scope_risks: list[str] = Field(default_factory=list, max_length=8)
    unsupported_claims: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("weaknesses", "missing_dependencies", "scope_risks", "unsupported_claims")
    @classmethod
    def no_untrusted_metrics_in_lists(cls, values: list[str]) -> list[str]:
        if any(FORBIDDEN_PROPOSAL_TEXT.search(v) for v in values):
            raise ValueError("critique may not contain URLs or metric-like claims")
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
    proposal_id: str | None = None
    decision: str
    decision_id: str | None
    score: float | None
    confidence: float | None
    # Re-resolved against the ledger when the view is built, not when the
    # proposal was written.
    cited_fact_ids: list[str] = Field(default_factory=list)
    withdrawn_fact_ids: list[str] = Field(default_factory=list)
    has_audit: bool = False


class RunView(StrictModel):
    id: str
    niche: str
    status: str
    message: str
    created_at: datetime
    completed_at: datetime | None
    candidates: list[CandidateView]
    passing_results: list[CandidateView]
    progress: dict = Field(default_factory=dict)


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


class AgentRunView(StrictModel):
    """One completed agent run, for the history page.

    Both agents write to the ledger but neither was listed anywhere, so a
    finished concept or audit could only be found by remembering which
    candidate it belonged to.
    """

    id: str
    kind: Literal["meta_hunter", "venture_scout"]
    created_at: datetime
    run_id: str | None = None
    niche: str = ""
    candidate_id: str
    candidate_name: str = ""
    title: str = ""
    summary: str = ""
    outcome: str
    model_name: str = ""
    operation: Literal["analyze_game", "audit_idea"] | None = None
    cited_fact_ids: list[str] = Field(default_factory=list)
    # The claim each citation points at, so history reads as evidence rather
    # than as a column of identical-looking row ids.
    cited_facts: list[FactView] = Field(default_factory=list)
    withdrawn_fact_ids: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    payload: ProposalPayload | None = None


class AuditGate(StrictModel):
    label: str
    passed: bool
    detail: str
    state: Literal["pass", "fail", "missing", "not_applicable"] = "missing"


class AuditReadiness(StrictModel):
    """Computed pre-audit state for one candidate."""

    candidate_id: str
    ready: bool
    # Evaluated per candidate against the ledger.
    gates: list[AuditGate] = Field(default_factory=list)
    # Always-on properties of the pipeline, not per-candidate measurements.
    invariants: list[AuditGate] = Field(default_factory=list)


class AuditView(StrictModel):
    audit_id: str | None = None
    proposal_id: str | None = None
    gates: list[AuditGate] = Field(default_factory=list)
    candidate_id: str
    evidence_state: str
    proposal: ProposalPayload | None
    risks: list[str]
    decision: str
    note: str
    cited_fact_ids: list[str] = Field(default_factory=list)
    withdrawn_fact_ids: list[str] = Field(default_factory=list)
    # Non-empty when the audit criticised its own draft and the revision that
    # should have answered the critique never landed.
    unresolved_concerns: list[str] = Field(default_factory=list)
    revision_applied: bool = True
    operation: Literal["analyze_game", "audit_idea"] | None = None
    # What the design was aimed at: the game itself for an analysis, the run's
    # niche for a critique of a proposal written in it.
    niche: str = ""
    # What the audit found against its own draft, kept whether or not the
    # revision landed, so a reviewer can see what was raised.
    critique: AuditCritique | None = None
