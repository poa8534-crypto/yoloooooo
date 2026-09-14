from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


@dataclass(frozen=True)
class MatchResult:
    outcome: str
    winner_index: int | None
    top_score: float
    margin: float
    rationale: list[str]


class AssociationMatcher:
    """Precision-first local matcher; dense vectors are optional and injected."""

    def __init__(self, high: float = 0.82, low: float = 0.45, margin_min: float = 0.08):
        self.high = high
        self.low = low
        self.margin_min = margin_min

    def match(
        self,
        query: str,
        candidates: list[str],
        dense_scores: list[float] | None = None,
    ) -> MatchResult:
        if not candidates:
            return MatchResult("no_match", None, 0.0, 0.0, ["no_candidates"])
        corpus = [query, *candidates]
        word = TfidfVectorizer(ngram_range=(1, 2), strip_accents="unicode").fit_transform(corpus)
        char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5)).fit_transform(corpus)
        word_scores = cosine_similarity(word[0:1], word[1:]).ravel()
        char_scores = cosine_similarity(char[0:1], char[1:]).ravel()
        scores = 0.6 * word_scores + 0.4 * char_scores
        if dense_scores is not None:
            if len(dense_scores) != len(candidates):
                raise ValueError("dense score count does not match candidates")
            scores = 0.7 * scores + 0.3 * np.asarray(dense_scores, dtype=float)
        order = np.argsort(-scores)
        top = int(order[0])
        s1 = float(scores[top])
        s2 = float(scores[int(order[1])]) if len(order) > 1 else 0.0
        margin = s1 - s2
        if s1 < self.low:
            return MatchResult("no_match", None, round(s1, 6), round(margin, 6), ["below_low_threshold"])
        if s1 >= self.high and margin >= self.margin_min:
            return MatchResult("auto_associate", top, round(s1, 6), round(margin, 6), ["high_score", "clear_margin"])
        reasons = ["review_required"]
        if margin < self.margin_min:
            reasons.append("ambiguous_margin")
        return MatchResult("review", top, round(s1, 6), round(margin, 6), reasons)


class LocalEmbeddingSearch:
    """Lazy, offline-at-inference semantic vectors; lexical matching remains the safe fallback."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        self.model_name = model_name
        self._model = None

    def scores(self, query: str, candidates: list[str]) -> list[float] | None:
        try:
            if self._model is None:
                from fastembed import TextEmbedding
                self._model = TextEmbedding(model_name=self.model_name)
            vectors = list(self._model.embed([query, *candidates]))
            query_vector = np.asarray(vectors[0], dtype=float)
            candidate_vectors = np.asarray(vectors[1:], dtype=float)
            norms = np.linalg.norm(candidate_vectors, axis=1) * max(np.linalg.norm(query_vector), 1e-12)
            return (candidate_vectors @ query_vector / np.maximum(norms, 1e-12)).clip(0.0, 1.0).tolist()
        except (ImportError, RuntimeError, ValueError):
            return None
