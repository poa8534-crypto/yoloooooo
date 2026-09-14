"""Scoring, margins and the four possible outcomes.

The engine returns exactly one of `auto_associate`, `review_required`,
`no_match` or `blocked_conflict`. Abstaining is a successful outcome: a match
that is close, incomplete or ambiguous goes to a human instead of being
guessed.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field

from .features import FeatureContext, FeatureVector, compute_features
from .retrieval import EmbeddingProvider, RetrievalResult, retrieve
from .rules import PairGuard, SubjectGuard, guard_pair, guard_subject
from .subjects import MatchCandidateView, MatchSubjectView
from .thresholds import Thresholds, active_thresholds


class AssociationOutcome(str, enum.Enum):
    AUTO_ASSOCIATE = "auto_associate"
    REVIEW_REQUIRED = "review_required"
    NO_MATCH = "no_match"
    BLOCKED_CONFLICT = "blocked_conflict"


# Rationale codes
CODE_NO_CANDIDATES = "no_candidates_retrieved"
CODE_BELOW_LOW = "below_low_threshold"
CODE_BELOW_HIGH = "below_high_threshold"
CODE_THIN_MARGIN = "insufficient_top_two_margin"
CODE_LOW_COVERAGE = "insufficient_required_feature_coverage"
CODE_NEEDS_INDEPENDENT = "insufficient_independent_features"
CODE_FUZZY_NOT_VALIDATED = "fuzzy_auto_association_not_validated"
CODE_SHADOW_MODE = "shadow_mode_fuzzy_to_review"
CODE_EXACT_ID = "exact_verified_id_evidence"
CODE_SCORE_PASS = "score_above_high_threshold"
CODE_MARGIN_PASS = "clear_top_two_margin"
CODE_ALL_CANDIDATES_CONTRADICTED = "every_candidate_contradicted_by_explicit_id"
CODE_RULE_PENALISED_REVIEW = "rule_penalised_but_named_by_source"
CODE_RULE_BLOCKED_AUTO = "hard_rule_blocked_automatic_association"


def logistic(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, value))))


def score_features(features: FeatureVector, thresholds: Thresholds) -> float:
    """Frozen linear model, squashed into [0, 1]. No model call, no randomness."""
    total = sum(
        weight * features.values.get(name, 0.0)
        for name, weight in thresholds.weights.items()
    )
    return round(logistic(total + thresholds.bias), 9)


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate: MatchCandidateView
    features: FeatureVector
    score: float
    guard: PairGuard
    retrieval_methods: tuple[str, ...] = ()

    @property
    def candidate_id(self) -> str:
        return self.candidate.candidate_id


@dataclass(frozen=True)
class AssociationVerdict:
    """Everything a stored `AssociationRecord` needs, plus the reasoning."""

    subject: MatchSubjectView
    outcome: AssociationOutcome
    winner: CandidateEvaluation | None
    runner_up: CandidateEvaluation | None
    top_score: float
    runner_up_score: float
    margin: float
    rationale: tuple[str, ...]
    subject_guard: SubjectGuard
    thresholds: Thresholds
    evaluations: tuple[CandidateEvaluation, ...] = ()
    embedding_available: bool = False
    embedding_model: str = ""
    retrieval_notes: tuple[str, ...] = ()
    duplicate_of_subject_id: str = ""
    shadow_mode: bool = True

    @property
    def required_coverage(self) -> float:
        return self.winner.features.required_coverage if self.winner else 0.0

    def candidate_scoreboard(self) -> list[dict]:
        return [
            {
                "candidate_id": item.candidate_id,
                "universe_id": item.candidate.universe_id,
                "display_name": item.candidate.raw_name,
                "score": item.score,
                "exact_id_evidence": item.guard.exact_id_evidence,
                "hard_negative": item.guard.hard_negative,
                "retrieval_methods": list(item.retrieval_methods),
            }
            for item in self.evaluations
        ]


@dataclass
class _Working:
    codes: list[str] = field(default_factory=list)

    def add(self, *codes: str) -> None:
        for code in codes:
            if code not in self.codes:
                self.codes.append(code)


def _verdict(
    subject: MatchSubjectView,
    outcome: AssociationOutcome,
    codes: list[str],
    subject_guard: SubjectGuard,
    thresholds: Thresholds,
    *,
    shadow_mode: bool,
    evaluations: tuple[CandidateEvaluation, ...] = (),
    winner: CandidateEvaluation | None = None,
    runner_up: CandidateEvaluation | None = None,
    embedding_available: bool = False,
    embedding_model: str = "",
    retrieval_notes: tuple[str, ...] = (),
    duplicate_of: str = "",
) -> AssociationVerdict:
    top = winner.score if winner else 0.0
    second = runner_up.score if runner_up else 0.0
    return AssociationVerdict(
        subject=subject,
        outcome=outcome,
        winner=winner,
        runner_up=runner_up,
        top_score=top,
        runner_up_score=second,
        margin=round(top - second, 9),
        rationale=tuple(codes),
        subject_guard=subject_guard,
        thresholds=thresholds,
        evaluations=evaluations,
        embedding_available=embedding_available,
        embedding_model=embedding_model,
        retrieval_notes=retrieval_notes,
        duplicate_of_subject_id=duplicate_of,
        shadow_mode=shadow_mode,
    )


def evaluate(
    subject: MatchSubjectView,
    pool: list[MatchCandidateView],
    *,
    thresholds: Thresholds | None = None,
    embedder: EmbeddingProvider | None = None,
    niche: str = "",
    shadow_mode: bool = True,
    duplicate_of: str = "",
    retrieval: RetrievalResult | None = None,
) -> AssociationVerdict:
    """Score a subject against a candidate pool and return one verdict."""
    thresholds = thresholds or active_thresholds()
    subject_guard = guard_subject(subject)
    working = _Working(list(subject_guard.codes))

    if duplicate_of:
        # Syndicated re-upload: the content already counted once.
        from .rules import CODE_DUPLICATE_CONTENT

        working.add(CODE_DUPLICATE_CONTENT)
        return _verdict(
            subject, AssociationOutcome.NO_MATCH, working.codes, subject_guard,
            thresholds, shadow_mode=shadow_mode, duplicate_of=duplicate_of,
        )

    if subject_guard.blocked:
        # Two conflicting explicit IDs: automatic association is impossible.
        return _verdict(
            subject, AssociationOutcome.BLOCKED_CONFLICT, working.codes, subject_guard,
            thresholds, shadow_mode=shadow_mode,
        )

    found = retrieval if retrieval is not None else retrieve(subject, pool, embedder=embedder)
    working.codes.extend(note for note in found.notes if note not in working.codes)

    if not found.candidates:
        working.add(CODE_NO_CANDIDATES)
        return _verdict(
            subject, AssociationOutcome.NO_MATCH, working.codes, subject_guard, thresholds,
            shadow_mode=shadow_mode, embedding_available=found.embedding_available,
            embedding_model=found.embedding_model, retrieval_notes=tuple(found.notes),
        )

    evaluations: list[CandidateEvaluation] = []
    for candidate in found.candidates:
        context = FeatureContext(
            niche=niche,
            embedding_score=found.embedding_scores.get(candidate.candidate_id),
            embedding_available=found.embedding_available
            and candidate.candidate_id in found.embedding_scores,
            competing_name_hits=found.competing_name_hits.get(candidate.candidate_id, 0),
            retrieval_methods=found.methods_for(candidate.candidate_id),
        )
        features = compute_features(subject, candidate, context)
        guard = guard_pair(subject, candidate, features, subject_guard=subject_guard)
        evaluations.append(CandidateEvaluation(
            candidate=candidate,
            features=features,
            score=score_features(features, thresholds),
            guard=guard,
            retrieval_methods=found.methods_for(candidate.candidate_id),
        ))

    # Deterministic ordering: score first, then candidate ID, so that ties can
    # never resolve differently between two runs.
    evaluations.sort(key=lambda item: (-item.score, item.candidate_id))
    ranked = tuple(evaluations)
    admissible = [item for item in evaluations if not item.guard.hard_negative]

    shared = {
        "subject_guard": subject_guard,
        "thresholds": thresholds,
        "shadow_mode": shadow_mode,
        "evaluations": ranked,
        "embedding_available": found.embedding_available,
        "embedding_model": found.embedding_model,
        "retrieval_notes": tuple(found.notes),
    }

    if not admissible:
        working.add(CODE_ALL_CANDIDATES_CONTRADICTED)
        return _verdict(
            subject, AssociationOutcome.NO_MATCH, working.codes, **shared,
        )

    winner = admissible[0]
    runner_up = admissible[1] if len(admissible) > 1 else None
    margin = round(winner.score - (runner_up.score if runner_up else 0.0), 9)
    working.add(*winner.guard.codes)

    if winner.score < thresholds.low:
        # A rule penalty is there to stop an automatic association, not to
        # throw away a candidate the source plainly names. Those pairs go to a
        # person instead of falling out of the queue.
        if winner.guard.auto_blocked and winner.guard.strong_lexical_evidence:
            working.add(CODE_BELOW_LOW, CODE_RULE_PENALISED_REVIEW, *winner.guard.codes)
            return _verdict(
                subject, AssociationOutcome.REVIEW_REQUIRED, working.codes,
                winner=winner, runner_up=runner_up, **shared,
            )
        working.add(CODE_BELOW_LOW)
        return _verdict(
            subject, AssociationOutcome.NO_MATCH, working.codes,
            winner=winner, runner_up=runner_up, **shared,
        )

    reasons_against: list[str] = []
    if winner.score < thresholds.high:
        reasons_against.append(CODE_BELOW_HIGH)
    else:
        working.add(CODE_SCORE_PASS)
    if margin < thresholds.margin_min:
        reasons_against.append(CODE_THIN_MARGIN)
    else:
        working.add(CODE_MARGIN_PASS)
    if winner.features.required_coverage < thresholds.min_required_coverage:
        reasons_against.append(CODE_LOW_COVERAGE)
    if winner.guard.auto_blocked:
        # A hard rule vetoed automatic association. The specific rule codes are
        # already on the record; this one says the veto was decisive.
        reasons_against.append(CODE_RULE_BLOCKED_AUTO)

    exact_id = winner.guard.exact_id_evidence
    if exact_id:
        working.add(CODE_EXACT_ID)
    else:
        independent = winner.features.independent_hits()
        if len(independent) < thresholds.min_independent_features:
            reasons_against.append(CODE_NEEDS_INDEPENDENT)
        if not thresholds.fuzzy_auto_enabled:
            reasons_against.append(CODE_FUZZY_NOT_VALIDATED)
        if shadow_mode:
            # Shadow mode: only exact verified-ID matches may be accepted
            # automatically. Everything fuzzy is recorded and reviewed.
            reasons_against.append(CODE_SHADOW_MODE)

    if reasons_against:
        working.add(*reasons_against)
        return _verdict(
            subject, AssociationOutcome.REVIEW_REQUIRED, working.codes,
            winner=winner, runner_up=runner_up, **shared,
        )

    return _verdict(
        subject, AssociationOutcome.AUTO_ASSOCIATE, working.codes,
        winner=winner, runner_up=runner_up, **shared,
    )
