"""Deterministic pairwise features.

Every value here is computed from stored text and stored identifiers. No model
writes, reads back, or adjusts any of these numbers: an LLM has no path into
this module. The feature order is frozen and versioned, because a stored
`AssociationRecord` has to be replayable against the exact same schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .normalize import (
    char_ngrams,
    content_tokens,
    extract_identifiers,
    normalized_name,
    word_tokens,
)
from .subjects import (
    SUBJECT_ROBLOX_EXPERIENCE,
    SUBJECT_WEB_PAGE,
    SUBJECT_YOUTUBE_VIDEO,
    MatchCandidateView,
    MatchSubjectView,
)

FEATURE_SCHEMA_VERSION = "features-v1"

# Frozen order. Append new features to the end and bump the schema version;
# never reorder, because stored records are replayed against this list.
FEATURE_NAMES: tuple[str, ...] = (
    "direct_universe_id_agreement",
    "direct_place_id_agreement",
    "direct_roblox_url_agreement",
    "exact_normalized_name_match",
    "alias_match",
    "word_similarity",
    "char_ngram_similarity",
    "token_jaccard",
    "embedding_cosine",
    "niche_keyword_coverage",
    "creator_channel_agreement",
    "description_roblox_link_agreement",
    "title_game_name_agreement",
    "generic_title_penalty",
    "conflicting_explicit_id_penalty",
    "competing_game_name_penalty",
    "source_type_youtube",
    "source_type_web",
    "source_type_roblox",
    "extraction_api",
    "extraction_page",
    "embedding_available",
)

# Features whose inputs must actually be present before an automatic
# association is allowed. A feature with no input reads 0.0 and is reported as
# uncovered rather than guessed.
REQUIRED_FEATURES: tuple[str, ...] = (
    "word_similarity",
    "char_ngram_similarity",
    "title_game_name_agreement",
    "creator_channel_agreement",
    "description_roblox_link_agreement",
)

# Independent fuzzy signals. Embedding similarity is deliberately absent: dense
# vectors may retrieve a candidate but may never carry an approval on their own.
INDEPENDENT_FEATURES: tuple[str, ...] = (
    "exact_normalized_name_match",
    "alias_match",
    "creator_channel_agreement",
    "description_roblox_link_agreement",
    "title_game_name_agreement",
)

API_EXTRACTION_METHODS = frozenset({
    "roblox_games_api", "youtube_videos_api", "youtube_search_api", "json_pointer",
})
PAGE_EXTRACTION_METHODS = frozenset({"page_capture", "exact_passage", "html_capture"})


@dataclass(frozen=True)
class FeatureContext:
    """Pair-independent inputs that the retrieval stage supplies."""

    niche: str = ""
    embedding_score: float | None = None
    embedding_available: bool = False
    competing_name_hits: int = 0
    retrieval_methods: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeatureVector:
    values: dict[str, float]
    availability: dict[str, bool] = field(default_factory=dict)

    def as_list(self) -> list[float]:
        return [self.values[name] for name in FEATURE_NAMES]

    @property
    def required_coverage(self) -> float:
        if not REQUIRED_FEATURES:
            return 1.0
        covered = sum(1 for name in REQUIRED_FEATURES if self.availability.get(name, False))
        return covered / len(REQUIRED_FEATURES)

    def uncovered_required(self) -> list[str]:
        return [name for name in REQUIRED_FEATURES if not self.availability.get(name, False)]

    def independent_hits(self) -> list[str]:
        return [
            name for name in INDEPENDENT_FEATURES
            if self.availability.get(name, False) and self.values.get(name, 0.0) >= 0.90
        ]


def _dice(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return 2.0 * len(left & right) / (len(left) + len(right))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _contains_sequence(haystack: list[str], needle: list[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[i:i + len(needle)] == needle
        for i in range(len(haystack) - len(needle) + 1)
    )


def title_name_agreement(subject: MatchSubjectView, candidate: MatchCandidateView) -> float:
    """How completely the candidate's name appears inside the subject title."""
    subject_tokens = word_tokens(subject.raw_title)
    name_tokens = word_tokens(candidate.raw_name)
    if not subject_tokens or not name_tokens:
        return 0.0
    if _contains_sequence(subject_tokens, name_tokens):
        return 1.0
    for alias in candidate.aliases:
        if _contains_sequence(subject_tokens, word_tokens(alias)):
            return 1.0
    content_name = content_tokens(candidate.raw_name)
    if content_name and _contains_sequence(subject_tokens, content_name):
        return 0.95
    if not content_name:
        return 0.0
    present = sum(1 for token in content_name if token in set(subject_tokens))
    return round(0.80 * present / len(content_name), 6)


def creator_agreement(subject: MatchSubjectView, candidate: MatchCandidateView) -> float:
    subject_key, candidate_key = subject.creator_key, candidate.creator_key
    if not subject_key or not candidate_key:
        return 0.0
    if subject_key == candidate_key:
        return 1.0
    # A YouTube channel ID and a Roblox creator ID are different namespaces;
    # comparing them was dead code that would only ever fire on a collision.
    overlap = _dice(set(subject_key.split()), set(candidate_key.split()))
    return 1.0 if overlap >= 0.90 else round(0.5 * overlap, 6)


def niche_coverage(candidate: MatchCandidateView, niche: str) -> float:
    keywords = [token for token in content_tokens(niche)] or list(candidate.niche_keywords)
    if not keywords:
        return 0.0
    haystack = set(word_tokens(f"{candidate.raw_name} {candidate.raw_description}"))
    return round(sum(1 for word in keywords if word in haystack) / len(keywords), 6)


def compute_features(
    subject: MatchSubjectView,
    candidate: MatchCandidateView,
    context: FeatureContext | None = None,
) -> FeatureVector:
    """Compute every stored feature for one (subject, candidate) pair."""
    context = context or FeatureContext()
    identifiers = subject.identifiers
    subject_name_key = subject.normalized.name_key
    candidate_name_key = candidate.name_key

    universe_hit = float(bool(candidate.universe_id) and candidate.universe_id in identifiers.universe_ids)
    place_hit = float(bool(set(candidate.place_ids) & identifiers.place_ids))
    url_hit = float(bool(candidate.game_urls & set(identifiers.roblox_game_urls)))

    explicit_ids = identifiers.roblox_ids
    owned = {value for value in explicit_ids if candidate.owns_roblox_id(value)}
    conflicting = float(bool(explicit_ids) and not owned)

    subject_content = set(content_tokens(subject.raw_title))
    candidate_content = set(content_tokens(candidate.raw_name))
    subject_words = set(word_tokens(subject.raw_title))
    candidate_words = set(word_tokens(candidate.raw_name))
    word_left = subject_content or subject_words
    word_right = candidate_content or candidate_words

    description_link = 0.0
    if f"{subject.raw_description}{subject.raw_url}".strip():
        # Identifiers found in the description or the source URL only, so a
        # Roblox link in the title alone does not count twice.
        linked = extract_identifiers(subject.raw_description, subject.raw_url)
        description_link = float(
            bool(candidate.game_urls & set(linked.roblox_game_urls))
            or any(candidate.owns_roblox_id(value) for value in linked.roblox_ids)
        )

    alias_keys = candidate.alias_keys
    # Whole-token containment, not substring: an alias of "pet" must not
    # match "carpet".
    subject_token_list = word_tokens(subject.raw_title)
    alias_hit = float(bool(alias_keys) and (
        subject_name_key in alias_keys
        or any(
            _contains_sequence(subject_token_list, alias.split())
            for alias in alias_keys
        )
    ))

    embedding_score = context.embedding_score if context.embedding_available else None

    values: dict[str, float] = {
        "direct_universe_id_agreement": universe_hit,
        "direct_place_id_agreement": place_hit,
        "direct_roblox_url_agreement": url_hit,
        "exact_normalized_name_match": float(
            bool(candidate_name_key) and subject_name_key == candidate_name_key
        ),
        "alias_match": alias_hit,
        "word_similarity": round(_dice(word_left, word_right), 6),
        "char_ngram_similarity": round(
            _dice(char_ngrams(subject.raw_title), char_ngrams(candidate.raw_name)), 6
        ),
        "token_jaccard": round(_jaccard(word_left, word_right), 6),
        "embedding_cosine": round(float(embedding_score), 6) if embedding_score is not None else 0.0,
        "niche_keyword_coverage": niche_coverage(candidate, context.niche),
        "creator_channel_agreement": creator_agreement(subject, candidate),
        "description_roblox_link_agreement": description_link,
        "title_game_name_agreement": title_name_agreement(subject, candidate),
        "generic_title_penalty": float(subject.normalized.generic),
        "conflicting_explicit_id_penalty": conflicting,
        "competing_game_name_penalty": float(min(1.0, max(0, context.competing_name_hits))),
        "source_type_youtube": float(subject.subject_type == SUBJECT_YOUTUBE_VIDEO),
        "source_type_web": float(subject.subject_type == SUBJECT_WEB_PAGE),
        "source_type_roblox": float(subject.subject_type == SUBJECT_ROBLOX_EXPERIENCE),
        "extraction_api": float(subject.extraction_method in API_EXTRACTION_METHODS),
        "extraction_page": float(subject.extraction_method in PAGE_EXTRACTION_METHODS),
        "embedding_available": float(bool(context.embedding_available)),
    }

    availability = {
        "direct_universe_id_agreement": bool(candidate.universe_id),
        "direct_place_id_agreement": bool(candidate.place_ids),
        "direct_roblox_url_agreement": bool(candidate.place_ids),
        "exact_normalized_name_match": bool(subject_name_key and candidate_name_key),
        "alias_match": bool(candidate.alias_keys),
        "word_similarity": bool(word_left and word_right),
        "char_ngram_similarity": bool(subject.raw_title.strip() and candidate.raw_name.strip()),
        "token_jaccard": bool(word_left and word_right),
        "embedding_cosine": bool(context.embedding_available),
        "niche_keyword_coverage": bool(content_tokens(context.niche) or candidate.niche_keywords),
        "creator_channel_agreement": bool(subject.creator_key and candidate.creator_key),
        "description_roblox_link_agreement": bool(subject.raw_description.strip()),
        "title_game_name_agreement": bool(subject.raw_title.strip() and candidate.raw_name.strip()),
        "generic_title_penalty": True,
        "conflicting_explicit_id_penalty": True,
        "competing_game_name_penalty": True,
        "source_type_youtube": True,
        "source_type_web": True,
        "source_type_roblox": True,
        "extraction_api": bool(subject.extraction_method),
        "extraction_page": bool(subject.extraction_method),
        "embedding_available": True,
    }

    missing = set(FEATURE_NAMES) - set(values)
    if missing:
        raise AssertionError(f"feature schema {FEATURE_SCHEMA_VERSION} is missing {sorted(missing)}")
    return FeatureVector(values=values, availability=availability)


def canonical_name_key(raw: str) -> str:
    return normalized_name(raw)
