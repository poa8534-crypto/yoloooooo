"""In-memory views of the things being matched.

Identity is always an internal ID plus, where one exists, a verified external
ID. A display name on its own never establishes identity: two experiences may
legitimately share a name, and a name can be changed after capture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .normalize import (
    Identifiers,
    NormalizedText,
    canonical_game_url,
    content_fingerprint,
    normalize_record,
    normalized_name,
)

SUBJECT_YOUTUBE_VIDEO = "youtube_video"
SUBJECT_WEB_PAGE = "web_page"
SUBJECT_ROBLOX_EXPERIENCE = "roblox_experience"

CANDIDATE_ROBLOX_EXPERIENCE = "roblox_experience"
CANDIDATE_NICHE_CLUSTER = "niche_cluster"

SUBJECT_TYPES = frozenset({SUBJECT_YOUTUBE_VIDEO, SUBJECT_WEB_PAGE, SUBJECT_ROBLOX_EXPERIENCE})
CANDIDATE_TYPES = frozenset({CANDIDATE_ROBLOX_EXPERIENCE, CANDIDATE_NICHE_CLUSTER})


@dataclass(frozen=True)
class MatchSubjectView:
    """One source record that may belong to a Roblox experience."""

    subject_id: str
    subject_type: str
    external_id: str = ""
    raw_title: str = ""
    raw_description: str = ""
    raw_url: str = ""
    creator_name: str = ""
    creator_external_id: str = ""
    niche: str = ""
    source_artifact_id: str = ""
    source_artifact_sha256: str = ""
    extraction_method: str = ""
    source_tier: str = ""
    pointer_prefix: str = ""
    discovered_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    normalized: NormalizedText = field(init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.subject_type not in SUBJECT_TYPES:
            raise ValueError(f"unknown subject type: {self.subject_type}")
        object.__setattr__(
            self, "normalized",
            normalize_record(self.raw_title, self.raw_description, self.raw_url),
        )

    @property
    def identifiers(self) -> Identifiers:
        return self.normalized.identifiers

    @property
    def creator_key(self) -> str:
        """Normalized creator handle or channel name, "" when unknown."""
        if self.creator_name:
            return normalized_name(self.creator_name)
        handles = sorted(self.identifiers.creator_handles)
        return handles[0] if handles else ""

    @property
    def fingerprint(self) -> str:
        """Collapses syndicated re-uploads of the same content to one key."""
        return content_fingerprint(self.raw_title, self.raw_description)

    def missing_required_fields(self) -> list[str]:
        """Fields a decision needs that this subject does not carry.

        A missing field is reported, never imputed.
        """
        missing = []
        if not self.raw_title.strip():
            missing.append("raw_title")
        if not self.source_artifact_sha256:
            missing.append("source_artifact_sha256")
        if self.subject_type == SUBJECT_YOUTUBE_VIDEO and not self.raw_description.strip():
            missing.append("raw_description")
        return missing


@dataclass(frozen=True)
class MatchCandidateView:
    """One Roblox experience (or niche cluster) a subject may belong to."""

    candidate_id: str
    candidate_type: str = CANDIDATE_ROBLOX_EXPERIENCE
    universe_id: str = ""
    place_ids: tuple[str, ...] = ()
    raw_name: str = ""
    raw_description: str = ""
    aliases: tuple[str, ...] = ()
    creator_name: str = ""
    creator_external_id: str = ""
    canonical_candidate_id: str = ""
    niche_keywords: tuple[str, ...] = ()
    normalized: NormalizedText = field(init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.candidate_type not in CANDIDATE_TYPES:
            raise ValueError(f"unknown candidate type: {self.candidate_type}")
        object.__setattr__(
            self, "normalized", normalize_record(self.raw_name, self.raw_description),
        )
        object.__setattr__(
            self, "canonical_candidate_id", self.canonical_candidate_id or self.candidate_id,
        )

    @property
    def name_key(self) -> str:
        return self.normalized.name_key

    @property
    def alias_keys(self) -> frozenset[str]:
        return frozenset(
            key for key in (normalized_name(alias) for alias in self.aliases) if key
        )

    @property
    def creator_key(self) -> str:
        return normalized_name(self.creator_name) if self.creator_name else ""

    @property
    def game_urls(self) -> frozenset[str]:
        """Canonical Roblox game URLs this candidate is reachable at."""
        return frozenset(canonical_game_url(place_id) for place_id in self.place_ids)

    def owns_roblox_id(self, identifier: str) -> bool:
        return identifier == self.universe_id or identifier in self.place_ids
