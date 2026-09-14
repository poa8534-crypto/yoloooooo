"""Deterministic, versioned association engine.

The engine decides whether a discovered source (a YouTube video, a captured web
page) belongs to a Roblox experience candidate. Every decision is a pure
function of stored features, is recorded append-only, and is versioned so that
a verdict can be recomputed and audited later.
"""

from __future__ import annotations

from .decisions import (
    AssociationOutcome,
    AssociationVerdict,
    CandidateEvaluation,
    evaluate,
)
from .features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    REQUIRED_FEATURES,
    compute_features,
)
from .normalize import (
    NORMALIZATION_VERSION,
    Identifiers,
    NormalizedText,
    content_fingerprint,
    extract_identifiers,
    normalize_record,
    normalize_url,
)
from .retrieval import EmbeddingProvider, RetrievalResult, retrieve
from .rules import guard_pair, guard_subject
from .service import (
    AssociationDecision,
    AssociationService,
    candidate_view_from_row,
    is_association_usable,
    is_downstream_admissible,
    resolved_candidate_id,
)
from .subjects import MatchCandidateView, MatchSubjectView
from .thresholds import (
    MATCHER_VERSION,
    THRESHOLD_VERSION,
    Thresholds,
    active_thresholds,
)

__all__ = [
    "FEATURE_NAMES",
    "FEATURE_SCHEMA_VERSION",
    "MATCHER_VERSION",
    "NORMALIZATION_VERSION",
    "REQUIRED_FEATURES",
    "THRESHOLD_VERSION",
    "AssociationDecision",
    "AssociationOutcome",
    "AssociationService",
    "AssociationVerdict",
    "CandidateEvaluation",
    "EmbeddingProvider",
    "Identifiers",
    "MatchCandidateView",
    "MatchSubjectView",
    "NormalizedText",
    "RetrievalResult",
    "Thresholds",
    "active_thresholds",
    "candidate_view_from_row",
    "compute_features",
    "content_fingerprint",
    "evaluate",
    "extract_identifiers",
    "guard_pair",
    "guard_subject",
    "is_association_usable",
    "is_downstream_admissible",
    "normalize_record",
    "normalize_url",
    "resolved_candidate_id",
    "retrieve",
]
