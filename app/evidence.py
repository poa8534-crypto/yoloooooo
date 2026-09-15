from __future__ import annotations

import gzip
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import tldextract
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Fact, Observation, SourceArtifact
from .security import sanitize_url

PRIMARY_OWNERS = {"roblox.com", "youtube.com", "googleapis.com"}
EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=())

FACT_TEMPLATES: dict[str, str] = {
    "roblox_name": "Roblox lists this experience as {value}.",
    "roblox_playing": "Roblox reported {value} concurrent players at capture time.",
    "roblox_visits": "Roblox reported {value} lifetime visits at capture time.",
    "roblox_favorites": "Roblox reported {value} favorites at capture time.",
    "roblox_updated": "Roblox reported the experience update time as {value}.",
    "roblox_description": "The Roblox experience description states: {value}",
    "youtube_title": "YouTube lists the tracked video as {value}.",
    "youtube_views": "YouTube reported {value} views for the tracked video at capture time.",
    "web_claim": "A captured source states: {value}",
}


class EvidenceError(ValueError):
    pass


def publisher_owner(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    ext = EXTRACTOR(host)
    return ".".join(part for part in (ext.domain, ext.suffix) if part) or host


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _store_bytes(raw: bytes, content_type: str) -> tuple[str, str]:
    """Store the captured bytes, compressed, addressed by their own hash.

    The market census writes 136 KB of near-identical JSON every half hour,
    which is 2.4 GB a year into a folder that happens to be synchronised to
    OneDrive. Captured payloads compress about six-fold, so they are stored
    that way.

    The digest is still taken over the *original* bytes: the hash in the
    ledger identifies what the source said, not how it happens to be stored.
    `mtime=0` keeps the compressed form byte-identical for identical input, so
    content addressing still holds.
    """
    digest = hashlib.sha256(raw).hexdigest()
    ext = ".json" if "json" in content_type else ".txt"
    path = get_settings().artifact_dir / f"{digest}{ext}.gz"
    if not path.exists():
        path.write_bytes(gzip.compress(raw, compresslevel=6, mtime=0))
    return digest, str(path)


def _read_stored(path: Path) -> bytes:
    """The captured bytes, whatever form they were stored in.

    Artifacts recorded before compression keep their own uncompressed paths,
    so both forms stay readable and no migration is needed.
    """
    stored = path.read_bytes()
    if path.suffix != ".gz":
        return stored
    try:
        return gzip.decompress(stored)
    except (OSError, EOFError) as exc:
        # A stored file that will not decompress no longer holds what was
        # captured. That is the same failure as a hash mismatch and has to be
        # reported as one rather than escaping as a gzip error.
        raise EvidenceError("artifact hash mismatch: stored bytes are unreadable") from exc


def record_artifact(
    db: Session,
    *,
    url: str,
    retrieval_method: str,
    content_type: str,
    payload: Any,
    source_tier: str,
    discovery_only: bool = False,
    owner: str | None = None,
    captured_at: datetime | None = None,
) -> SourceArtifact:
    raw = _canonical_json(payload) if "json" in content_type else str(payload).encode("utf-8")
    digest, raw_path = _store_bytes(raw, content_type)
    artifact = SourceArtifact(
        url=sanitize_url(url),
        publisher_owner=owner or publisher_owner(url),
        retrieval_method=retrieval_method,
        captured_at=captured_at or datetime.now(UTC),
        sha256=digest,
        content_type=content_type,
        raw_path=raw_path,
        source_tier=source_tier,
        raw_size=len(raw),
        is_discovery_only=discovery_only,
    )
    db.add(artifact)
    db.flush()
    return artifact


def _load_artifact(artifact: SourceArtifact) -> Any:
    raw = _read_stored(Path(artifact.raw_path))
    if hashlib.sha256(raw).hexdigest() != artifact.sha256:
        raise EvidenceError("artifact hash mismatch")
    if "json" in artifact.content_type:
        return json.loads(raw)
    return raw.decode("utf-8")


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("/") and pointer != "":
        raise EvidenceError("invalid JSON pointer")
    current = document
    for raw_part in pointer.split("/")[1:]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise EvidenceError(f"JSON pointer does not resolve: {pointer}")
    return current


def add_json_observation(
    db: Session,
    *,
    artifact: SourceArtifact,
    candidate_id: str | None,
    metric: str,
    pointer: str,
    unit: str | None = None,
    observed_at: datetime | None = None,
    association_id: str | None = None,
) -> Observation:
    if artifact.is_discovery_only:
        raise EvidenceError("discovery-only artifacts cannot create observations")
    document = _load_artifact(artifact)
    value = resolve_json_pointer(document, pointer)
    observation = Observation(
        artifact_id=artifact.id,
        candidate_id=candidate_id,
        metric=metric,
        value_json=value,
        unit=unit,
        extraction_method="json_pointer",
        pointer=pointer,
        observed_at=observed_at or artifact.captured_at,
        association_id=association_id,
    )
    db.add(observation)
    db.flush()
    return observation


def add_passage_observation(
    db: Session,
    *,
    artifact: SourceArtifact,
    candidate_id: str | None,
    metric: str,
    exact_passage: str,
    value: Any,
    unit: str | None = None,
    association_id: str | None = None,
) -> Observation:
    if artifact.is_discovery_only:
        raise EvidenceError("discovery-only artifacts cannot create observations")
    if not exact_passage.strip() or exact_passage not in str(_load_artifact(artifact)):
        raise EvidenceError("supporting passage is not present in captured content")
    observation = Observation(
        artifact_id=artifact.id,
        candidate_id=candidate_id,
        metric=metric,
        value_json=value,
        unit=unit,
        extraction_method="exact_passage",
        pointer=f"passage:{exact_passage}",
        observed_at=artifact.captured_at,
        association_id=association_id,
    )
    db.add(observation)
    db.flush()
    return observation


def create_fact(
    db: Session,
    *,
    template_id: str,
    slots: dict[str, Observation],
    source_ids: list[str] | None = None,
    freshness: str = "fresh",
    verification_state: str = "verified",
) -> Fact:
    if template_id not in FACT_TEMPLATES:
        raise EvidenceError("unknown fact template")
    if not slots:
        raise EvidenceError("a fact requires at least one observation")
    resolved_sources = source_ids or sorted({o.artifact_id for o in slots.values()})
    for observation in slots.values():
        artifact = db.get(SourceArtifact, observation.artifact_id)
        if artifact is None or artifact.is_discovery_only:
            raise EvidenceError("fact references inadmissible evidence")
        if observation.extraction_method == "json_pointer":
            actual = resolve_json_pointer(_load_artifact(artifact), observation.pointer)
            if actual != observation.value_json:
                raise EvidenceError("observation no longer matches its JSON pointer")
        elif observation.extraction_method == "exact_passage":
            passage = observation.pointer.removeprefix("passage:")
            if passage not in str(_load_artifact(artifact)) or str(observation.value_json) not in passage:
                raise EvidenceError("observation passage is missing")
    fact = Fact(
        template_id=template_id,
        slot_observation_ids={name: obs.id for name, obs in slots.items()},
        source_ids=resolved_sources,
        freshness=freshness,
        verification_state=verification_state,
    )
    db.add(fact)
    db.flush()
    return fact


def render_fact(db: Session, fact: Fact) -> str:
    template = FACT_TEMPLATES.get(fact.template_id)
    if template is None:
        raise EvidenceError("unknown fact template")
    values: dict[str, Any] = {}
    for slot, observation_id in fact.slot_observation_ids.items():
        observation = db.get(Observation, observation_id)
        if observation is None:
            raise EvidenceError("fact points to a missing observation")
        artifact = db.get(SourceArtifact, observation.artifact_id)
        if artifact is None:
            raise EvidenceError("observation points to a missing artifact")
        if observation.extraction_method == "json_pointer":
            actual = resolve_json_pointer(_load_artifact(artifact), observation.pointer)
            if actual != observation.value_json:
                raise EvidenceError("render blocked by provenance mismatch")
        else:
            passage = observation.pointer.removeprefix("passage:")
            if passage not in str(_load_artifact(artifact)) or str(observation.value_json) not in passage:
                raise EvidenceError("render blocked by missing passage")
        values[slot] = observation.value_json
    return template.format(**values)


def fact_freshness(db: Session, fact: Fact, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    ages: list[float] = []
    for observation_id in fact.slot_observation_ids.values():
        observation = db.get(Observation, observation_id)
        if observation is None:
            return "broken"
        observed_at = observation.observed_at
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        ages.append((now - observed_at).total_seconds() / 3600.0)
    return "fresh" if ages and max(ages) <= 48.0 else "stale"


def accept_web_claim(
    db: Session,
    *,
    candidate_id: str | None,
    claim_value: str,
    metric: str,
    evidence: list[tuple[SourceArtifact, str]],
    association_id: str | None = None,
) -> Fact:
    if not evidence:
        raise EvidenceError("web claim has no evidence")
    observations: list[Observation] = []
    owners: set[str] = set()
    content_hashes: set[str] = set()
    has_primary = False
    for artifact, passage in evidence:
        owners.add(artifact.publisher_owner)
        content_hashes.add(artifact.sha256)
        has_primary = has_primary or artifact.source_tier == "primary" or artifact.publisher_owner in PRIMARY_OWNERS
        observations.append(add_passage_observation(
            db,
            artifact=artifact,
            candidate_id=candidate_id,
            metric=metric,
            exact_passage=passage,
            value=claim_value,
            association_id=association_id,
        ))
    if not has_primary and (len(owners) < 2 or len(content_hashes) < 2):
        raise EvidenceError("secondary web claims require two independent sources")
    return create_fact(
        db,
        template_id="web_claim",
        slots={"value": observations[0]},
        source_ids=[o.artifact_id for o in observations],
        verification_state="primary" if has_primary else "corroborated",
    )


def _fact_index(db: Session) -> dict[str, list[Fact]]:
    """Map observation id to the facts built from it, once per session.

    `candidate_facts` used to read every `Fact` row and filter in Python, so
    one dashboard request covering 149 candidates loaded the whole fact table
    189 times: roughly 240,000 rows and five seconds for a single response,
    growing with the ledger. The ledger is append-only, so within a session
    the only way this index can go stale is a fact appended after it was
    built, which the row count detects.
    """
    total = db.scalar(select(func.count(Fact.id))) or 0
    cached = db.info.get("fact_index")
    if cached is not None and cached[0] == total:
        return cached[1]
    index: dict[str, list[Fact]] = {}
    for fact in db.scalars(select(Fact)):
        for observation_id in fact.slot_observation_ids.values():
            index.setdefault(observation_id, []).append(fact)
    db.info["fact_index"] = (total, index)
    return index


def candidate_facts(db: Session, candidate_id: str) -> list[Fact]:
    observation_ids = set(db.scalars(
        select(Observation.id).where(Observation.candidate_id == candidate_id)
    ))
    if not observation_ids:
        return []
    index = _fact_index(db)
    matched = {
        fact.id: fact
        for observation_id in observation_ids
        for fact in index.get(observation_id, ())
    }
    return sorted(matched.values(), key=_fact_order)


def _fact_order(fact: Fact) -> tuple[datetime, str]:
    """Order facts stably, whatever their timestamps came from.

    A row read back from SQLite is naive UTC, while a fact created earlier in
    the same session is still timezone-aware; comparing the two raises. The id
    breaks ties, which the clock alone does not: facts written in the same
    batch can share a timestamp to the microsecond.
    """
    created = fact.created_at
    return (created if created.tzinfo else created.replace(tzinfo=UTC), fact.id)


def latest_metric(db: Session, candidate_id: str, metric: str) -> Observation | None:
    return db.scalar(
        select(Observation)
        .where(Observation.candidate_id == candidate_id, Observation.metric == metric)
        .order_by(Observation.observed_at.desc())
        .limit(1)
    )


def evidence_conflicts(db: Session, candidate_id: str) -> list[str]:
    rows = list(db.scalars(
        select(Observation)
        .where(Observation.candidate_id == candidate_id)
        .order_by(Observation.observed_at.desc())
    ))
    latest_by_metric: dict[str, list[Observation]] = {}
    for row in rows:
        bucket = latest_by_metric.setdefault(row.metric, [])
        if not bucket or abs((bucket[0].observed_at - row.observed_at).total_seconds()) <= 300:
            bucket.append(row)
    conflicts: list[str] = []
    for metric, bucket in latest_by_metric.items():
        numeric = [float(r.value_json) for r in bucket if isinstance(r.value_json, (int, float))]
        if len(numeric) > 1:
            lo, hi = min(numeric), max(numeric)
            if not math.isclose(lo, hi, rel_tol=0.10, abs_tol=1.0):
                conflicts.append(metric)
    return conflicts
