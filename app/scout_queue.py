"""Hunter concepts waiting for the Venture Scout, and what happens to them.

The two agents were only joined by hand. Meta Hunter drafted up to three
concepts per run, and reaching any of them meant opening the Idea Panel,
finding the right candidate and starting one audit. Concepts that nobody
happened to open were never audited and nothing anywhere said so.

Routing is automatic; running is not. Every un-audited Hunter concept lands in
this queue on its own, and the queue sits there until somebody presses the
button. That split is deliberate: an audit spends several minutes of the local
model per concept, and the model is serialized, so a queue that ran itself
would decide for the operator how the next half hour of compute is spent.
"""

from __future__ import annotations

from sqlalchemy import select

from .models import AuditRecord, Candidate, Observation, Proposal, ResearchRun
from .research_evidence import evidence_packet

HUNTER = "meta_hunter"


def _title(payload) -> str:
    if isinstance(payload, dict):
        for key in ("concept_title", "title"):
            if payload.get(key):
                return str(payload[key])
    return "Untitled concept"


def _summary(payload) -> str:
    return str(payload.get("core_loop", "")) if isinstance(payload, dict) else ""


def pending(db, *, active_candidates: set[str] | None = None) -> list[dict]:
    """Hunter concepts with no audit yet, newest first.

    A concept whose candidate already has a Scout job in flight is listed but
    marked unavailable rather than hidden: disappearing from the queue the
    moment something else touches the same game is how an operator loses track
    of what is actually waiting.
    """
    audited = set(db.scalars(select(AuditRecord.proposal_id)
                             .where(AuditRecord.proposal_id.is_not(None))))
    busy = active_candidates or set()
    rows: list[dict] = []
    for proposal in db.scalars(
        select(Proposal).where(Proposal.agent == HUNTER).order_by(Proposal.created_at.desc())
    ):
        if proposal.id in audited:
            continue
        candidate = db.get(Candidate, proposal.candidate_id)
        if candidate is None:
            continue
        run = db.get(ResearchRun, candidate.run_id) if candidate.run_id else None
        packet = evidence_packet(db, candidate.id)
        name = next((str(fact["slots"][0]["value"]) for fact in packet
                     if fact["template_id"] == "roblox_name"), "")
        rows.append({
            "proposal_id": proposal.id,
            "candidate_id": candidate.id,
            "universe_id": candidate.external_id,
            "game_name": name or "Sourced Roblox experience",
            "concept_title": _title(proposal.payload),
            "core_loop": _summary(proposal.payload),
            "niche": run.niche if run else "",
            "created_at": proposal.created_at.isoformat(),
            "facts": len(packet),
            # An audit with no admissible evidence behind it cannot be bound to
            # anything, so it is not offered rather than failing on the gate.
            "available": bool(packet) and candidate.id not in busy,
            "unavailable_reason": (
                "" if packet and candidate.id not in busy
                else "A Scout run is already active for this game"
                if candidate.id in busy else
                "No admissible evidence remains for this game"
            ),
        })
    return rows


def _observed_name(db, candidate) -> str:
    """The candidate's name as it was captured, never as anyone typed it.

    The name lives on an observation pointing into a hashed artifact, so this
    reads that row rather than accepting a string from anywhere else. An
    unnamed candidate stays unnamed: inventing a label here would put an
    unsourced name on a card that everything else on the page traces.
    """
    if candidate is None or not candidate.display_name_observation_id:
        return ""
    observation = db.get(Observation, candidate.display_name_observation_id)
    value = observation.value_json if observation else None
    return str(value) if isinstance(value, str) else ""


def label(db, jobs: list[dict]) -> list[dict]:
    """Name each Scout job by the concept it is auditing.

    A job listed as `2681a175 — complete` is unreadable: it is a truncated
    identifier for work the operator asked for by name, and three of them in a
    column say nothing about which concept did what. The title already exists
    on the Hunter proposal the job points at, so the card can carry it.

    A job whose proposal or candidate has gone reports empty fields rather
    than a placeholder, and the page shows the identifier it does have.
    """
    labelled: list[dict] = []
    for job in jobs:
        proposal = db.get(Proposal, job["proposal_id"]) if job.get("proposal_id") else None
        candidate = db.get(Candidate, job["candidate_id"]) if job.get("candidate_id") else None
        run = db.get(ResearchRun, candidate.run_id) if candidate and candidate.run_id else None
        labelled.append({
            **job,
            "concept_title": _title(proposal.payload) if proposal else "",
            "core_loop": _summary(proposal.payload) if proposal else "",
            "game_name": _observed_name(db, candidate),
            "niche": run.niche if run else "",
            "universe_id": candidate.external_id if candidate else "",
        })
    return labelled


def audited(db, limit: int = 60) -> list[dict]:
    """Finished audits, newest first, as the cards the Scout page shows."""
    cards: list[dict] = []
    # `id` breaks the tie: two audits recorded in the same second would
    # otherwise come back in whatever order the database felt like, and the
    # strip would reshuffle itself between refreshes.
    for record in db.scalars(select(AuditRecord)
                             .order_by(AuditRecord.created_at.desc(), AuditRecord.id.desc())
                             .limit(limit)):
        payload = record.payload if isinstance(record.payload, dict) else {}
        proposal = db.get(Proposal, record.proposal_id) if record.proposal_id else None
        candidate = db.get(Candidate, record.candidate_id)
        run = db.get(ResearchRun, candidate.run_id) if candidate and candidate.run_id else None
        design = payload.get("proposal") if isinstance(payload.get("proposal"), dict) else {}
        cards.append({
            "audit_id": record.id,
            "candidate_id": record.candidate_id,
            "proposal_id": record.proposal_id,
            "universe_id": candidate.external_id if candidate else "",
            "niche": run.niche if run else "",
            "concept_title": _title(design) if design else _title(proposal.payload if proposal else None),
            "core_loop": _summary(design) or _summary(proposal.payload if proposal else None),
            "evidence_state": str(payload.get("evidence_state", "unknown")),
            "risks": len(payload.get("risks") or []),
            "cited_facts": len(payload.get("cited_fact_ids") or payload.get("supporting_fact_ids") or []),
            "created_at": record.created_at.isoformat(),
        })
    return cards
