"""Shared evidence gates, canonical histories, and model-readable packets."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from .association import is_association_usable
from .association.service import resolved_candidate_id
from .evidence import (
    _load_artifact,
    candidate_facts,
    evidence_conflicts,
    fact_freshness,
    render_fact,
    resolve_json_pointer,
)
from .models import (
    AssociationRecord,
    Candidate,
    MatchCandidate,
    Observation,
    Proposal,
    SourceArtifact,
)
from .schemas import AuditGate, AuditReadiness


def canonical_candidate_ids(db, candidate_id):
    candidate = db.get(Candidate, candidate_id)
    if not candidate:
        return []
    return list(db.scalars(select(Candidate.id).where(Candidate.external_kind == candidate.external_kind,
                                                    Candidate.external_id == candidate.external_id)))


def measurement_entity(row):
    if row.metric.startswith("youtube"):
        try:
            prefix = row.pointer.split("/statistics")[0].split("/snippet")[0]
            return "youtube:" + str(resolve_json_pointer(_load_artifact(row.artifact), prefix + "/id"))
        except (ValueError, KeyError, IndexError, OSError):
            return None
    return "roblox"


# Measurements read straight out of the Roblox API response that named the
# universe. Everything else -- a scraped page, a YouTube video -- is a claim
# about this experience made somewhere else, and has to resolve through an
# approved association before anything downstream may trust it. New metrics
# are external until this set says otherwise, so the default is to refuse.
FIRST_PARTY_METRICS = frozenset({
    "roblox_name", "roblox_playing", "roblox_visits",
    "roblox_favorites", "roblox_updated", "roblox_description",
})


def admissible_observation(db, row):
    if not row.artifact or row.artifact.is_discovery_only:
        return False
    if row.metric not in FIRST_PARTY_METRICS:
        association = db.get(AssociationRecord, row.association_id) if row.association_id else None
        if not association or not is_association_usable(db, association):
            return False
        resolved = resolved_candidate_id(db, association)
        match = db.get(MatchCandidate, resolved) if resolved else None
        owner = db.get(Candidate, row.candidate_id) if row.candidate_id else None
        if match is None or owner is None or match.universe_id != owner.external_id:
            return False
    try:
        raw = _load_artifact(row.artifact)
        if row.extraction_method == "json_pointer":
            return resolve_json_pointer(raw, row.pointer) == row.value_json
        passage = row.pointer.removeprefix("passage:")
        return passage in str(raw) and str(row.value_json) in passage
    except (ValueError, KeyError, IndexError, OSError):
        return False


def verified_fact_packet(db, fact):
    """Validate an exact historical fact, without replacing it with a newer fact."""
    rows = [db.get(Observation, oid) for oid in fact.slot_observation_ids.values()]
    if not rows or any(row is None or not admissible_observation(db, row) for row in rows):
        return None
    try:
        for source_id in fact.source_ids:
            artifact = db.get(SourceArtifact, source_id)
            if artifact is None or artifact.is_discovery_only:
                return None
            _load_artifact(artifact)
        rendered = render_fact(db, fact)
    except (ValueError, KeyError, IndexError, OSError):
        return None
    return {"id": fact.id, "template_id": fact.template_id, "text": rendered,
            "source_ids": fact.source_ids, "freshness": fact_freshness(db, fact),
            "captured_at": max(row.observed_at for row in rows).isoformat(),
            "slots": [{"observation_id": row.id, "value": row.value_json, "unit": row.unit,
                       "artifact_id": row.artifact_id, "sha256": row.artifact.sha256, "pointer": row.pointer} for row in rows],
            "limitations": ["Source-reported measurement or text; not independently verified source accuracy."]}


def evidence_packet(db, candidate_id, *, include_stale=False):
    latest = {}
    for cid in canonical_candidate_ids(db, candidate_id):
        for fact in candidate_facts(db, cid):
            rows = [db.get(Observation, oid) for oid in fact.slot_observation_ids.values()]
            if not rows or any(row is None for row in rows):
                continue
            key = (fact.template_id, measurement_entity(rows[0]))
            time = max(row.observed_at for row in rows)
            if key not in latest or time > latest[key][0]:
                latest[key] = (time, fact, rows)
    packet = []
    for _, fact, _rows in latest.values():
        item = verified_fact_packet(db, fact)
        if item and (include_stale or item["freshness"] == "fresh"):
            packet.append(item)
    return packet


def audit_readiness(db, candidate_id, proposal_id=None):
    if db.get(Candidate, candidate_id) is None:
        raise KeyError(candidate_id)
    packet = evidence_packet(db, candidate_id, include_stale=True)
    metrics = {item["template_id"] for item in packet}
    required = {"roblox_name", "roblox_playing", "roblox_visits"}
    conflicts = sorted({metric for cid in canonical_candidate_ids(db, candidate_id) for metric in evidence_conflicts(db, cid)})
    rows = list(db.scalars(select(Observation).where(Observation.candidate_id.in_(canonical_candidate_ids(db, candidate_id)))))
    # Broken source bytes block audits, even when filtered out of the packet.
    # Classified by the same rule the admissibility gate uses, so a claim that
    # merely lacks an approved association is reported as unresolved rather
    # than as corrupted first-party bytes.
    broken = [row for row in rows if row.metric in FIRST_PARTY_METRICS and not admissible_observation(db, row)]
    external = [row for row in rows if row.metric not in FIRST_PARTY_METRICS]
    unresolved = [row for row in external if not admissible_observation(db, row)]
    proposal = db.get(Proposal, proposal_id) if proposal_id else db.scalar(select(Proposal).where(
        Proposal.candidate_id == candidate_id, Proposal.agent == "meta_hunter").order_by(Proposal.created_at.desc()))
    valid_proposal = proposal is not None and proposal.candidate_id == candidate_id and proposal.agent == "meta_hunter"
    def gate(label, state, detail):
        return AuditGate(label=label, state=state, passed=state == "pass", detail=detail)
    # A Hunter proposal narrows the audit to critiquing that exact design. Its
    # absence is not a failure: Scout then analyses the captured evidence
    # directly, which is the only way most candidates ever get a brief -- Hunter
    # only writes proposals for the handful of candidates a run selects.
    if proposal_id and not valid_proposal:
        hunter_gate = gate("Selected Hunter proposal", "fail",
                           "The requested proposal does not belong to this candidate")
    elif valid_proposal:
        hunter_gate = gate("Selected Hunter proposal", "pass",
                           "Audit is bound to the selected proposal version")
    else:
        hunter_gate = gate("Selected Hunter proposal", "not_applicable",
                           "No Hunter proposal; Scout analyses the captured evidence directly")
    gates = [
        gate("Required Roblox facts", "pass" if required <= metrics else "missing", ", ".join(sorted(required - metrics)) or "Name, CCU and visits available"),
        gate("Evidence integrity", "fail" if broken else ("pass" if packet else "missing"), f"{len(broken)} broken observation(s)"),
        gate("Evidence freshness", "pass" if packet and all(p["freshness"] == "fresh" for p in packet) else "missing", "Latest measurements must be within 48 hours"),
        gate("Conflicts", "fail" if conflicts else ("pass" if packet else "missing"), ", ".join(conflicts) or "No detected metric conflict"),
        gate("External associations", "fail" if unresolved else ("pass" if external else "not_applicable"),
             "Roblox-only scope; no external claim captured" if not external
             else f"{len(unresolved)} of {len(external)} external claim(s) resolve to no approved association"),
        hunter_gate,
    ]
    return AuditReadiness(candidate_id=candidate_id, ready=all(g.state in {"pass", "not_applicable"} for g in gates), gates=gates)


def history(db, candidate_id):
    """Latest verified capture per UTC day/entity; gaps stay null."""
    rows = list(db.scalars(select(Observation).where(Observation.candidate_id.in_(canonical_candidate_ids(db, candidate_id)))
                           .order_by(Observation.observed_at, Observation.id)))
    daily = {}
    for row in rows:
        if row.metric not in {"roblox_playing", "roblox_visits", "youtube_views"} or not admissible_observation(db, row):
            continue
        entity = measurement_entity(row)
        if entity is None:
            continue
        try:
            value = float(row.value_json)
        except (ValueError, TypeError):
            continue
        daily[(row.observed_at.date(), row.metric, entity)] = {"value": value, "observation_id": row.id, "artifact_id": row.artifact_id}
    if not daily:
        return {"status": "not_collected", "points": [], "trend": None}
    first = min(key[0] for key in daily)
    last = max(key[0] for key in daily)
    first = max(first, last - timedelta(days=399))
    points = []
    day = first
    while day <= last:
        measures = [{"metric": metric, "entity": entity, **value} for (date, metric, entity), value in daily.items() if date == day]
        points.append({"day": day.isoformat(), "measurements": measures or None})
        day += timedelta(days=1)
    consecutive = all((last - timedelta(days=i), "roblox_playing", "roblox") in daily for i in range(7))
    return {"status": "available" if consecutive else "insufficient_history", "points": points,
            "trend": {"label": "preliminary_7_day_ccu_change", "value": daily[(last, "roblox_playing", "roblox")]["value"] - daily[(last - timedelta(days=6), "roblox_playing", "roblox")]["value"]} if consecutive else None}
