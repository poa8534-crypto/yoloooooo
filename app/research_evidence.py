"""Shared evidence gates, canonical histories, and model-readable packets."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from .association import is_association_usable
from .evidence import (
    _load_artifact,
    candidate_facts,
    evidence_conflicts,
    fact_freshness,
    render_fact,
    resolve_json_pointer,
)
from .models import AssociationRecord, Candidate, Observation, Proposal
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


def admissible_observation(db, row):
    if not row.artifact or row.artifact.is_discovery_only:
        return False
    if row.metric.startswith("youtube"):
        association = db.get(AssociationRecord, row.association_id) if row.association_id else None
        if not association or not is_association_usable(db, association):
            return False
    try:
        raw = _load_artifact(row.artifact)
        if row.extraction_method == "json_pointer":
            return resolve_json_pointer(raw, row.pointer) == row.value_json
        passage = row.pointer.removeprefix("passage:")
        return passage in str(raw) and str(row.value_json) in passage
    except (ValueError, KeyError, IndexError, OSError):
        return False


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
    for _, fact, rows in latest.values():
        if any(not admissible_observation(db, row) for row in rows):
            continue
        freshness = fact_freshness(db, fact)
        if not include_stale and freshness != "fresh":
            continue
        try:
            text = render_fact(db, fact)
        except (ValueError, KeyError, IndexError, OSError):
            continue
        packet.append({"id": fact.id, "template_id": fact.template_id, "text": text,
                       "source_ids": fact.source_ids, "freshness": freshness,
                       "captured_at": max(row.observed_at for row in rows).isoformat(),
                       "slots": [{"observation_id": row.id, "value": row.value_json, "unit": row.unit,
                                  "artifact_id": row.artifact_id, "sha256": row.artifact.sha256,
                                  "pointer": row.pointer} for row in rows],
                       "limitations": ["Source-reported measurement or text; not independently verified source accuracy."]})
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
    broken = [row for row in rows if not row.metric.startswith("youtube") and not admissible_observation(db, row)]
    external = [row for row in rows if row.metric.startswith("youtube")]
    unresolved = [row for row in external if not admissible_observation(db, row)]
    proposal = db.get(Proposal, proposal_id) if proposal_id else db.scalar(select(Proposal).where(
        Proposal.candidate_id == candidate_id, Proposal.agent == "meta_hunter").order_by(Proposal.created_at.desc()))
    valid_proposal = proposal is not None and proposal.candidate_id == candidate_id and proposal.agent == "meta_hunter"
    def gate(label, state, detail):
        return AuditGate(label=label, state=state, passed=state == "pass", detail=detail)
    gates = [
        gate("Required Roblox facts", "pass" if required <= metrics else "missing", ", ".join(sorted(required - metrics)) or "Name, CCU and visits available"),
        gate("Evidence integrity", "fail" if broken else ("pass" if packet else "missing"), f"{len(broken)} broken observation(s)"),
        gate("Evidence freshness", "pass" if packet and all(p["freshness"] == "fresh" for p in packet) else "missing", "Latest measurements must be within 48 hours"),
        gate("Conflicts", "fail" if conflicts else ("pass" if packet else "missing"), ", ".join(conflicts) or "No detected metric conflict"),
        gate("External associations", "fail" if unresolved else ("pass" if external else "not_applicable"), "Roblox-only scope; creator evidence unavailable" if not external else f"{len(unresolved)} unresolved observation(s)"),
        gate("Selected Hunter proposal", "pass" if valid_proposal else "missing", "Audit is bound to the selected proposal version"),
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
