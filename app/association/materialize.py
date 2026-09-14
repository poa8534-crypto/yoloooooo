"""Turning an approved association into downstream evidence.

This is the only place that writes a YouTube or web observation against a
Roblox candidate, and it refuses to write one unless the association is usable:
either an `auto_associate` verdict resting on exact verified-ID evidence (or a
validated matcher), or a human confirmation. That is what makes the acceptance
property hold — every downstream metric resolves through an approved, versioned
association record back to its hashed source artifact.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..evidence import EvidenceError, add_json_observation, create_fact
from ..models import (
    AssociationRecord,
    MatchCandidate,
    MatchSubject,
    Observation,
    SourceArtifact,
    TrackedVideo,
)
from .service import is_association_usable, resolved_candidate_id
from .subjects import SUBJECT_YOUTUBE_VIDEO

# (pointer suffix, metric, fact template, unit)
YOUTUBE_METRIC_SPECS: tuple[tuple[str, str, str, str | None], ...] = (
    ("snippet/title", "youtube_title", "youtube_title", None),
    ("statistics/viewCount", "youtube_views", "youtube_views", "views"),
)


def already_materialized(db: Session, association_id: str) -> bool:
    return db.scalar(
        select(Observation.id).where(Observation.association_id == association_id).limit(1)
    ) is not None


def apply_association(db: Session, record: AssociationRecord) -> list[str]:
    """Create the observations and facts an approved association unlocks.

    Returns the IDs of the facts created. Does nothing (and returns an empty
    list) when the association is not usable or has already been applied.
    """
    if not is_association_usable(db, record):
        return []
    if already_materialized(db, record.id):
        return []

    match_candidate_id = resolved_candidate_id(db, record)
    if not match_candidate_id:
        return []
    match_candidate = db.get(MatchCandidate, match_candidate_id)
    subject = db.get(MatchSubject, record.subject_id)
    if match_candidate is None or subject is None:
        return []
    # The evidence ledger's own candidate row. Without it there is nothing to
    # attach an observation to, and the engine will not invent one.
    candidate_row_id = match_candidate.candidate_row_id
    if not candidate_row_id:
        return []
    artifact = (
        db.get(SourceArtifact, subject.source_artifact_id)
        if subject.source_artifact_id else None
    )
    if artifact is None or not subject.pointer_prefix:
        return []

    fact_ids: list[str] = []
    if subject.subject_type == SUBJECT_YOUTUBE_VIDEO:
        if subject.external_id and db.scalar(
            select(TrackedVideo).where(TrackedVideo.video_id == subject.external_id)
        ) is None:
            db.add(TrackedVideo(candidate_id=candidate_row_id, video_id=subject.external_id))
        specs = YOUTUBE_METRIC_SPECS
    else:
        specs = ()

    for suffix, metric, template, unit in specs:
        pointer = f"{subject.pointer_prefix.rstrip('/')}/{suffix}"
        try:
            observation = add_json_observation(
                db,
                artifact=artifact,
                candidate_id=candidate_row_id,
                metric=metric,
                pointer=pointer,
                unit=unit,
                association_id=record.id,
            )
        except (EvidenceError, KeyError, IndexError, ValueError):
            # A pointer that no longer resolves is a missing field, not a
            # value to guess at.
            continue
        fact_ids.append(create_fact(db, template_id=template, slots={"value": observation}).id)
    db.flush()
    return fact_ids
