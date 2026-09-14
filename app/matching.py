"""Local dense retrieval support for the association engine.

The corpus-wide TF-IDF matcher that used to live here has been replaced by
`app.association`, which records features, rules and versions for every
verdict. What remains is the optional embedding model: it can widen retrieval,
it can never approve a match on its own, and an outage degrades to lexical
retrieval with the outage recorded on the association record.
"""

from __future__ import annotations

import numpy as np

from .association.retrieval import EmbeddingProvider

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"


class LocalEmbeddingSearch:
    """Lazy, offline-at-inference semantic vectors.

    `scores` returns None whenever the model cannot run — a missing package, a
    missing download, a runtime failure. None is the supported outcome, and the
    caller falls back to lexical retrieval.
    """

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL):
        self.model_name = model_name
        self._model = None

    def scores(self, query: str, candidates: list[str]) -> list[float] | None:
        if not candidates:
            return None
        try:
            if self._model is None:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(model_name=self.model_name)
            vectors = list(self._model.embed([query, *candidates]))
            query_vector = np.asarray(vectors[0], dtype=float)
            candidate_vectors = np.asarray(vectors[1:], dtype=float)
            norms = np.linalg.norm(candidate_vectors, axis=1) * max(
                np.linalg.norm(query_vector), 1e-12
            )
            return (
                candidate_vectors @ query_vector / np.maximum(norms, 1e-12)
            ).clip(0.0, 1.0).tolist()
        except (ImportError, OSError, RuntimeError, ValueError):
            return None


def local_embedding_provider(
    model_name: str = DEFAULT_EMBEDDING_MODEL,
) -> EmbeddingProvider:
    """An `EmbeddingProvider` backed by the local fastembed model."""
    search = LocalEmbeddingSearch(model_name)
    return EmbeddingProvider(model_name=model_name, scorer=search.scores)
