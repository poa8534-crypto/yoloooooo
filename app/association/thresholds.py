"""Frozen weights, thresholds and version identifiers.

A verdict is only reproducible if the numbers behind it are pinned. The
built-in constants below are the shipped defaults; a validated benchmark run
(`app.association.benchmark`) writes an artifact that can raise or lower them,
and the artifact records the dataset hash it was measured on.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..config import get_settings
from .features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from .normalize import NORMALIZATION_VERSION

MATCHER_VERSION = "assoc-v1"
THRESHOLD_VERSION = "thresholds-v1"
WEIGHTS_VERSION = "weights-v1"

# Precision floor the held-out benchmark must clear before fuzzy (non-ID)
# evidence is ever allowed to associate automatically.
FUZZY_PRECISION_FLOOR = 0.99
MIN_FUZZY_HELDOUT_DECISIONS = 20

DEFAULT_WEIGHTS: dict[str, float] = {
    "direct_universe_id_agreement": 3.00,
    "direct_place_id_agreement": 2.50,
    "direct_roblox_url_agreement": 2.50,
    "exact_normalized_name_match": 1.60,
    "alias_match": 1.20,
    "word_similarity": 1.00,
    "char_ngram_similarity": 0.60,
    "token_jaccard": 0.50,
    # Dense similarity contributes, but never enough on its own to clear the
    # high threshold. See `decisions.py` for the hard gate that enforces this.
    "embedding_cosine": 0.70,
    "niche_keyword_coverage": 0.30,
    "creator_channel_agreement": 0.80,
    "description_roblox_link_agreement": 2.00,
    "title_game_name_agreement": 1.20,
    "generic_title_penalty": -1.50,
    "conflicting_explicit_id_penalty": -4.00,
    "competing_game_name_penalty": -1.20,
    "source_type_youtube": 0.00,
    "source_type_web": 0.00,
    "source_type_roblox": 0.00,
    "extraction_api": 0.00,
    "extraction_page": 0.00,
    "embedding_available": 0.00,
}

DEFAULT_BIAS = -2.20


@dataclass(frozen=True)
class Thresholds:
    """One frozen, versioned decision policy."""

    matcher_version: str = MATCHER_VERSION
    threshold_version: str = THRESHOLD_VERSION
    weights_version: str = WEIGHTS_VERSION
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    normalization_version: str = NORMALIZATION_VERSION
    high: float = 0.90
    low: float = 0.55
    margin_min: float = 0.10
    min_required_coverage: float = 0.75
    min_independent_features: int = 2
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    bias: float = DEFAULT_BIAS
    # False until a held-out benchmark clears FUZZY_PRECISION_FLOOR. While it
    # is False, only exact verified-ID evidence can associate automatically.
    fuzzy_auto_enabled: bool = False
    validated: bool = False
    dataset_hash: str = ""
    embedding_model: str = ""
    embedding_model_hash: str = ""
    heldout_precision: float | None = None
    heldout_decisions: int | None = None
    benchmark_reason: str = "Shipped defaults; no benchmark has been recorded."

    def weight_vector(self) -> list[float]:
        return [float(self.weights.get(name, 0.0)) for name in FEATURE_NAMES]

    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def as_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["feature_order"] = list(FEATURE_NAMES)
        data["fingerprint"] = self.fingerprint()
        return data


def artifact_path() -> Path:
    return get_settings().model_dir / "association_current.json"


def load_artifact() -> dict[str, Any] | None:
    path = artifact_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def thresholds_from_artifact(artifact: dict[str, Any]) -> Thresholds:
    """Rebuild a policy from a frozen benchmark artifact.

    A mismatch in feature order, feature schema or normalization version means
    the artifact describes a different engine, so it is refused rather than
    silently reinterpreted.
    """
    if list(artifact.get("feature_order", [])) != list(FEATURE_NAMES):
        raise ValueError("benchmark artifact was frozen against a different feature order")
    if artifact.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
        raise ValueError("benchmark artifact was frozen against a different feature schema")
    if artifact.get("normalization_version") != NORMALIZATION_VERSION:
        raise ValueError("benchmark artifact was frozen against a different normalization version")
    known = {f.name for f in Thresholds.__dataclass_fields__.values()}
    return Thresholds(**{key: value for key, value in artifact.items() if key in known})


def active_thresholds() -> Thresholds:
    """The policy in force right now.

    Falls back to the shipped defaults (fuzzy automatic association disabled)
    whenever no valid artifact is present.
    """
    artifact = load_artifact()
    if not artifact:
        return Thresholds()
    try:
        return thresholds_from_artifact(artifact)
    except (TypeError, ValueError):
        return Thresholds()


def write_artifact(thresholds: Thresholds) -> Path:
    path = artifact_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(thresholds.as_json(), indent=2, sort_keys=True), encoding="utf-8"
    )
    temp.replace(path)
    return path
