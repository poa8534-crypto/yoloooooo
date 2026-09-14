"""Candidate retrieval.

Retrieval is deliberately generous: it is a recall stage, and the scoring and
rule stages downstream are what refuse a bad pair. Dense embeddings may bring a
candidate into the pool, but `decisions.py` never lets a dense score approve a
match on its own, and an embedding outage degrades to lexical retrieval with the
outage recorded on the association record.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from .normalize import char_ngrams, content_tokens, word_tokens
from .subjects import MatchCandidateView, MatchSubjectView

RETRIEVAL_VERSION = "retrieval-v1"

METHOD_DIRECT_ID = "direct_id"
METHOD_DIRECT_URL = "direct_url"
METHOD_EXACT_NAME = "exact_name"
METHOD_BM25 = "bm25_word"
METHOD_CHAR_NGRAM = "char_ngram"
METHOD_DENSE = "dense_embedding"

NOTE_DENSE_UNAVAILABLE = "dense_retrieval_unavailable"


class EmbeddingProvider:
    """Protocol-ish wrapper around whatever supplies dense similarity.

    `scores` returns one cosine similarity per candidate, or None when the
    model could not run. None is a supported outcome, not an error.
    """

    def __init__(self, model_name: str = "", scorer=None):
        self.model_name = model_name
        self._scorer = scorer

    def scores(self, query: str, documents: list[str]) -> list[float] | None:
        if self._scorer is None:
            return None
        try:
            result = self._scorer(query, documents)
        except Exception:  # noqa: BLE001 - an outage must degrade, never raise
            return None
        if result is None or len(result) != len(documents):
            return None
        return [float(value) for value in result]


@dataclass
class RetrievalResult:
    candidates: list[MatchCandidateView] = field(default_factory=list)
    embedding_scores: dict[str, float] = field(default_factory=dict)
    embedding_available: bool = False
    embedding_model: str = ""
    methods: dict[str, list[str]] = field(default_factory=dict)
    competing_name_hits: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def methods_for(self, candidate_id: str) -> tuple[str, ...]:
        return tuple(self.methods.get(candidate_id, ()))


def _bm25_scores(
    query_tokens: list[str],
    documents: dict[str, list[str]],
    k1: float = 1.5,
    b: float = 0.75,
) -> dict[str, float]:
    """Plain BM25 over the candidate pool. No external fit state."""
    if not query_tokens or not documents:
        return {}
    lengths = {key: len(tokens) for key, tokens in documents.items()}
    avg_length = sum(lengths.values()) / max(len(lengths), 1) or 1.0
    document_frequency: Counter[str] = Counter()
    for tokens in documents.values():
        document_frequency.update(set(tokens))
    total = len(documents)
    scores: dict[str, float] = {}
    for key, tokens in documents.items():
        counts = Counter(tokens)
        score = 0.0
        for term in set(query_tokens):
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            n_q = document_frequency[term]
            idf = math.log(1.0 + (total - n_q + 0.5) / (n_q + 0.5))
            denominator = frequency + k1 * (1 - b + b * lengths[key] / avg_length)
            score += idf * frequency * (k1 + 1) / denominator
        if score > 0:
            scores[key] = round(score, 6)
    return scores


def _is_infix(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    return any(
        haystack[i:i + len(needle)] == needle
        for i in range(len(haystack) - len(needle) + 1)
    )


def _top_keys(scores: dict[str, float], limit: int) -> list[str]:
    return [
        key for key, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def retrieve(
    subject: MatchSubjectView,
    pool: list[MatchCandidateView],
    *,
    embedder: EmbeddingProvider | None = None,
    top_k: int = 10,
) -> RetrievalResult:
    """Union of direct, exact, lexical and dense retrieval over the pool."""
    result = RetrievalResult()
    if not pool:
        return result

    by_id = {candidate.candidate_id: candidate for candidate in pool}
    identifiers = subject.identifiers
    subject_name_key = subject.normalized.name_key
    selected: dict[str, list[str]] = {}

    def mark(candidate_id: str, method: str) -> None:
        methods = selected.setdefault(candidate_id, [])
        if method not in methods:
            methods.append(method)

    # 1. Direct Roblox ID and URL matches.
    subject_urls = set(identifiers.roblox_game_urls)
    for candidate in pool:
        if any(candidate.owns_roblox_id(value) for value in identifiers.roblox_ids):
            mark(candidate.candidate_id, METHOD_DIRECT_ID)
        if candidate.game_urls & subject_urls:
            mark(candidate.candidate_id, METHOD_DIRECT_URL)

    # 2. Exact normalized-name matches, including aliases.
    for candidate in pool:
        if subject_name_key and (
            subject_name_key == candidate.name_key or subject_name_key in candidate.alias_keys
        ):
            mark(candidate.candidate_id, METHOD_EXACT_NAME)

    # 3. Word BM25.
    query_tokens = content_tokens(subject.raw_title) or word_tokens(subject.raw_title)
    documents = {
        candidate.candidate_id: (
            content_tokens(f"{candidate.raw_name} {' '.join(candidate.aliases)}")
            or word_tokens(candidate.raw_name)
        )
        for candidate in pool
    }
    for candidate_id in _top_keys(_bm25_scores(query_tokens, documents), top_k):
        mark(candidate_id, METHOD_BM25)

    # 4. Character n-grams.
    subject_grams = char_ngrams(subject.raw_title)
    gram_scores: dict[str, float] = {}
    for candidate in pool:
        grams = char_ngrams(candidate.raw_name)
        if subject_grams and grams:
            overlap = len(subject_grams & grams)
            if overlap:
                gram_scores[candidate.candidate_id] = round(
                    2.0 * overlap / (len(subject_grams) + len(grams)), 6
                )
    for candidate_id in _top_keys(gram_scores, top_k):
        mark(candidate_id, METHOD_CHAR_NGRAM)

    # 5. Dense embeddings; an outage is recorded and retrieval continues.
    if embedder is not None:
        ordered = [candidate.candidate_id for candidate in pool]
        dense = embedder.scores(
            subject.raw_title, [by_id[key].raw_name for key in ordered]
        )
        if dense is None:
            result.notes.append(NOTE_DENSE_UNAVAILABLE)
        else:
            result.embedding_available = True
            result.embedding_model = embedder.model_name
            result.embedding_scores = {
                key: round(float(value), 6) for key, value in zip(ordered, dense, strict=True)
            }
            for candidate_id in _top_keys(result.embedding_scores, top_k):
                mark(candidate_id, METHOD_DENSE)
    else:
        result.notes.append(NOTE_DENSE_UNAVAILABLE)

    # Competing game names: how many *other* candidates the subject title also
    # names outright. A video that names two experiences cannot silently pick one.
    subject_tokens = word_tokens(subject.raw_title)
    hits: list[tuple[str, tuple[str, ...]]] = []
    for candidate in pool:
        name_tokens = tuple(word_tokens(candidate.raw_name))
        if not name_tokens or len(name_tokens) > len(subject_tokens):
            continue
        window = len(name_tokens)
        if any(
            tuple(subject_tokens[i:i + window]) == name_tokens
            for i in range(len(subject_tokens) - window + 1)
        ):
            hits.append((candidate.canonical_candidate_id, name_tokens))

    # "Grow a Garden 2" contains "Grow a Garden": a shorter name swallowed by a
    # longer one that also matched is the same mention, not a competing game.
    named = {
        canonical for canonical, tokens in hits
        if not any(
            other is not tokens and len(other) > len(tokens) and _is_infix(other, tokens)
            for _, other in hits
        )
    }

    result.candidates = [by_id[key] for key in sorted(selected)]
    result.methods = {key: list(value) for key, value in selected.items()}
    result.competing_name_hits = {
        candidate.candidate_id: len(named - {candidate.canonical_candidate_id})
        for candidate in result.candidates
    }
    return result
