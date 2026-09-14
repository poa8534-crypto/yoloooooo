"""Hard rules applied before and around scoring.

These run first and can veto a score outright. A rule never invents a value:
when a required field is absent the rule says so and the decision degrades to
review, rather than guessing the missing input.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .features import FeatureVector
from .subjects import MatchCandidateView, MatchSubjectView

RULES_VERSION = "rules-v1"

# Subject-level codes
CODE_MULTIPLE_EXPLICIT_IDS = "multiple_conflicting_explicit_ids"
CODE_MISSING_REQUIRED_FIELD = "missing_required_field"
CODE_UNTRUSTED_INJECTION = "untrusted_injection_text"
CODE_GENERIC_TITLE = "generic_title"
CODE_DUPLICATE_CONTENT = "duplicate_content_counted_once"

# Pair-level codes
CODE_DIRECT_URL_EVIDENCE = "direct_roblox_url_evidence"
CODE_DIRECT_ID_EVIDENCE = "direct_roblox_id_evidence"
CODE_EXPLICIT_ID_CONTRADICTION = "explicit_id_contradiction"
CODE_GENERIC_DENSE_ONLY = "generic_title_dense_evidence_only"
CODE_COMPETING_NAMES = "competing_game_names_in_title"
CODE_TITLE_NAMES_OTHER_GAME = "title_names_a_different_experience"


@dataclass(frozen=True)
class SubjectGuard:
    """Deterministic checks that depend on the source alone."""

    blocked: bool = False
    codes: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    untrusted_codes: tuple[str, ...] = ()

    @property
    def has_missing_fields(self) -> bool:
        return bool(self.missing_fields)


@dataclass(frozen=True)
class PairGuard:
    """Deterministic checks for one (subject, candidate) pair."""

    hard_negative: bool = False
    exact_id_evidence: bool = False
    auto_blocked: bool = False
    # True when the subject text itself names this candidate. A pair with
    # strong lexical evidence that a rule blocks belongs in review, not in
    # the discard pile.
    strong_lexical_evidence: bool = False
    codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DuplicateLedger:
    """Collapses syndicated re-uploads so the same content counts once."""

    seen: dict[str, str] = field(default_factory=dict)

    def register(self, fingerprint: str, subject_id: str) -> str | None:
        """Return the first subject ID that carried this content, if any."""
        existing = self.seen.get(fingerprint)
        if existing is not None and existing != subject_id:
            return existing
        self.seen.setdefault(fingerprint, subject_id)
        return None


def guard_subject(subject: MatchSubjectView) -> SubjectGuard:
    """Rules that can stop a subject before any candidate is scored."""
    codes: list[str] = []
    identifiers = subject.identifiers

    # Two explicit identifiers that cannot both be right. The engine refuses to
    # pick one; a human decides.
    distinct_urls = {url for url in identifiers.roblox_game_urls if url}
    blocked = (
        len(identifiers.place_ids) > 1
        or len(identifiers.universe_ids) > 1
        or len(distinct_urls) > 1
    )
    if blocked:
        codes.append(CODE_MULTIPLE_EXPLICIT_IDS)

    missing = tuple(subject.missing_required_fields())
    codes.extend(f"{CODE_MISSING_REQUIRED_FIELD}:{name}" for name in missing)

    untrusted = subject.normalized.injection_codes
    if untrusted:
        # Recorded so the text is stored as untrusted. It carries no authority
        # over the verdict: nothing downstream reads these codes as a command.
        codes.append(CODE_UNTRUSTED_INJECTION)

    if subject.normalized.generic:
        codes.append(CODE_GENERIC_TITLE)

    return SubjectGuard(
        blocked=blocked,
        codes=tuple(codes),
        missing_fields=missing,
        untrusted_codes=tuple(untrusted),
    )


def guard_pair(
    subject: MatchSubjectView,
    candidate: MatchCandidateView,
    features: FeatureVector,
    *,
    subject_guard: SubjectGuard | None = None,
) -> PairGuard:
    """Rules for one pair, evaluated against the stored features."""
    subject_guard = subject_guard or guard_subject(subject)
    values = features.values
    codes: list[str] = []

    url_hit = values["direct_roblox_url_agreement"] >= 1.0
    id_hit = (
        values["direct_universe_id_agreement"] >= 1.0
        or values["direct_place_id_agreement"] >= 1.0
    )
    if url_hit:
        codes.append(CODE_DIRECT_URL_EVIDENCE)
    if id_hit:
        codes.append(CODE_DIRECT_ID_EVIDENCE)

    # An explicit Roblox ID that belongs to a different experience is a hard
    # contradiction, whatever the text similarity says.
    hard_negative = values["conflicting_explicit_id_penalty"] >= 1.0
    if hard_negative:
        codes.append(CODE_EXPLICIT_ID_CONTRADICTION)

    auto_blocked = False

    # A generic title ("best Roblox games") carried only by dense similarity
    # can never auto-associate.
    lexical_support = max(
        values["exact_normalized_name_match"],
        values["alias_match"],
        values["title_game_name_agreement"],
        values["description_roblox_link_agreement"],
    )
    if subject.normalized.generic and not (url_hit or id_hit) and lexical_support < 0.90:
        auto_blocked = True
        codes.append(CODE_GENERIC_DENSE_ONLY)

    # The subject names at least one other experience outright.
    names_this_candidate = values["title_game_name_agreement"] >= 0.90
    if values["competing_game_name_penalty"] >= 1.0:
        # A description link is not enough when the title names a different
        # game: creators routinely link their own experience under a video
        # about somebody else's. A person decides which one the video is about.
        auto_blocked = True
        codes.append(
            CODE_COMPETING_NAMES if names_this_candidate else CODE_TITLE_NAMES_OTHER_GAME
        )

    if subject_guard.has_missing_fields and not (url_hit or id_hit):
        auto_blocked = True

    return PairGuard(
        hard_negative=hard_negative,
        exact_id_evidence=bool(url_hit or id_hit),
        auto_blocked=auto_blocked,
        strong_lexical_evidence=bool(
            names_this_candidate
            or values["exact_normalized_name_match"] >= 1.0
            or values["alias_match"] >= 1.0
        ),
        codes=tuple(codes),
    )
