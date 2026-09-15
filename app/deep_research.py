"""Bounded, checkpointed research. Model prose never enters factual slots."""
from __future__ import annotations

import asyncio
import re
from dataclasses import asdict
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import select

from .association import MatchSubjectView, is_downstream_admissible
from .association.normalize import word_tokens
from .association.subjects import SUBJECT_WEB_PAGE
from .connectors import extract_roblox_place_ids
from .evidence import (
    accept_web_claim,
    add_json_observation,
    create_fact,
    record_artifact,
)
from .models import (
    AuditRecord,
    Candidate,
    Proposal,
    ResearchReport,
    ResearchRun,
    SourceArtifact,
)
from .research_budget import BudgetExceeded, RunBudget, progress
from .research_evidence import evidence_packet, history
from .workflows import CandidateContext, _roblox_facts

QUESTION_TEXT = {
    "relevance": "Which discovered games fit the requested niche?",
    "demand": "What current demand is source-reported?",
    "history": "What historical change is supported?",
    "mechanics": "What mechanics are documented by primary sources?",
    "creators": "Which creator videos are reliably associated?",
    "counterevidence": "What contradicts the opportunity hypothesis?",
    "mvp": "What could a solo beginner attempt within a 72-hour MVP?",
}


def discovery_queries(niche):
    words = re.findall(r"[a-zA-Z]+", niche.lower())
    words = [w for w in words if w not in {"with", "and", "the", "for", "roblox", "a", "of", "in"}]
    chunks = [" ".join(words[:4]), " ".join(words[-3:]), " ".join(words[::2][:4])]
    return list(dict.fromkeys(f"site:roblox.com/games {phrase} {suffix}".strip()
                             for suffix in ("", "cooperative", "simulator", "social") for phrase in chunks if phrase))[:12]


def _contiguous(haystack, needle):
    if not needle or len(needle) > len(haystack):
        return False
    return any(haystack[i:i + len(needle)] == needle for i in range(len(haystack) - len(needle) + 1))


def restates_discovered_game(concept_title, discovered_names, minimum_tokens=2):
    """Return the game a concept title merely restates, if any.

    A run that discovers "Cheese Escape [Horror]" and then reports "Cheese
    Escape" as a research concept has handed back an existing game's identity
    dressed as a hypothesis. Matching is on contiguous normalized tokens in
    either direction, so a shared single word like "escape" is not enough.
    """
    concept = word_tokens(concept_title)
    if len(concept) < minimum_tokens:
        return None
    for name in discovered_names:
        tokens = word_tokens(name)
        if len(tokens) < minimum_tokens:
            continue
        if _contiguous(tokens, concept) or _contiguous(concept, tokens):
            return name
    return None


# What each unanswered question actually needs captured. Discovery only widens
# the pool, so it answers the questions about coverage rather than any specific
# game; `mvp` has no capture that could answer it, and saying so is better than
# spending a round pretending otherwise.
QUESTION_ACTIONS = {
    "creators": "youtube",
    "mechanics": "capture_page",
    "demand": "capture_page",
    "history": "capture_page",
    "relevance": "discover",
    "counterevidence": "discover",
    "mvp": None,
}

# Coverage gaps first: a round spent widening discovery is wasted while a
# captured game still has no creator or page evidence at all.
QUESTION_PRIORITY = ("creators", "mechanics", "demand", "history", "relevance", "counterevidence")


def plan_round(questions, contexts, queries, round_index, per_round=3):
    """Choose this round's captures from what is still unanswered.

    Rounds used to execute a precomputed slice of searches and only then ask
    what remained open, so the answers never influenced the next round and the
    run did the same work whatever it had already found.

    Returns a list of actions and the question each one is meant to answer, so
    the choice is auditable afterwards rather than implicit in a slice index.
    """
    unanswered = {q["id"] for q in questions if q.get("state") != "answered"}
    ordered = [key for key in QUESTION_PRIORITY if key in unanswered]
    actions: list[dict] = []
    # Round-robin over the games with the least evidence, so one game does not
    # absorb every capture while others stay bare.
    ranked = sorted(contexts, key=lambda c: (len(getattr(c, "video_ids", []) or []), c.display_name))
    for key in ordered:
        kind = QUESTION_ACTIONS.get(key)
        if kind is None:
            continue
        if kind == "discover":
            index = len([a for a in actions if a["kind"] == "discover"]) + round_index * 2
            if index < len(queries):
                actions.append({"kind": "discover", "query": queries[index], "question": key})
        elif ranked:
            target = ranked[len([a for a in actions if a["kind"] == kind]) % len(ranked)]
            actions.append({"kind": kind, "candidate_id": target.candidate_id,
                            "display_name": target.display_name, "question": key})
    if not actions:
        # Nothing outstanding is capturable; widen the pool rather than repeat.
        start = round_index * per_round
        actions = [{"kind": "discover", "query": query, "question": "relevance"}
                   for query in queries[start:start + per_round]]
    # One cap, at the end: enough work to matter, not enough to fill a round
    # with follow-ups for a single question.
    return actions[:per_round]


class DeepResearch:
    def __init__(self, orchestrator, run_id):
        self.o, self.factory, self.run_id = orchestrator, orchestrator.session_factory, run_id
        self.b = RunBudget(self.factory, run_id)
        with self.factory() as db:
            self.niche = db.get(ResearchRun, run_id).niche
        self.contexts = [CandidateContext(**c) for c in self.b.state.get("contexts", [])]

    def capture(self, db, result, method, tier, owner):
        cache = self.b.state.setdefault("artifacts", {})
        key = method + ":" + result.url
        if key in cache:
            existing = db.get(SourceArtifact, cache[key])
            if existing is not None:
                return existing
        row = record_artifact(db, url=result.url, retrieval_method=method, content_type=result.content_type,
                              payload=result.payload, source_tier=tier, discovery_only=tier == "discovery", owner=owner)
        # Persist mapping after caller's commit, not before its artifact exists.
        cache[key] = row.id
        return row

    async def discover(self, query):
        search = await self.b.call(self.o.connectors, "tavily_search", query)
        with self.factory() as db:
            self.capture(db, search, "tavily_search:" + query, "discovery", "tavily.com")
            db.commit()
        self.b.save()
        places = extract_roblox_place_ids(search.payload)[:30]
        resolved = {}
        for place in places:
            if len(self.contexts) + len(resolved) >= self.b.state["limits"]["universes"]:
                break
            try:
                response = await self.b.call(self.o.connectors, "universe_for_place", place)
                uid = str(response.payload.get("universeId") or "")
                if uid and uid not in {c.universe_id for c in self.contexts}:
                    resolved.setdefault(uid, []).append(place)
            except BudgetExceeded:
                raise
            except Exception as exc:
                self.b.error("resolve_game", exc)
        if not resolved:
            return
        self.b.reserve("universes", len(resolved))
        games = await self.b.call(self.o.connectors, "roblox_games", sorted(resolved))
        with self.factory() as db:
            artifact = self.capture(db, games, "roblox_games_api", "primary", "roblox.com")
            for index, item in enumerate(games.payload.get("data", [])):
                uid = str(item.get("id", ""))
                if uid not in resolved or uid in {c.universe_id for c in self.contexts}:
                    continue
                candidate = db.scalar(select(Candidate).where(Candidate.run_id == self.run_id, Candidate.external_id == uid))
                if candidate is None:
                    candidate = Candidate(run_id=self.run_id, external_id=uid)
                    db.add(candidate)
                    db.flush()
                facts = _roblox_facts(db, candidate, artifact, index)
                if item.get("description"):
                    observation = add_json_observation(db, artifact=artifact, candidate_id=candidate.id,
                                                       metric="roblox_description", pointer=f"/data/{index}/description")
                    facts.append(create_fact(db, template_id="roblox_description", slots={"value": observation}).id)
                creator = item.get("creator") or {}
                self.contexts.append(CandidateContext(candidate.id, str(item.get("name", "Sourced experience")), uid, facts,
                    tuple(dict.fromkeys(resolved[uid] + ([str(item["rootPlaceId"])] if item.get("rootPlaceId") else []))),
                    str(creator.get("name", "")), str(creator.get("id", "")), str(item.get("description") or "")))
                self.o._record_decision(db, candidate.id)
            db.commit()
        self.b.save(contexts=[asdict(c) for c in self.contexts])

    async def capture_primary(self, context):
        if not context.place_ids:
            return
        url = f"https://www.roblox.com/games/{context.place_ids[0]}"
        if url in self.b.state.get("processed_pages", []):
            return
        result = await self.b.call(self.o.connectors, "capture_page", url)
        host = urlparse(result.url).hostname or ""
        if host != "roblox.com" and not host.endswith(".roblox.com"):
            return
        with self.factory() as db:
            artifact = self.capture(db, result, "primary_game_page", "primary", "roblox.com")
            # Only quote an exact API-sourced description, never infer mechanics
            # from boilerplate, navigation, third-party comments or a search snippet.
            if context.description and context.description in str(result.payload):
                # A webpage metric is external evidence even when the page is the
                # experience's own, so it resolves through the association engine
                # like any other. The place ID in the URL is exact-ID evidence, so
                # this auto-associates rather than queueing for review.
                pool = [item.as_match_candidate() for item in self.contexts]
                subject = MatchSubjectView(
                    subject_id=f"page:{context.place_ids[0]}",
                    subject_type=SUBJECT_WEB_PAGE,
                    external_id=str(context.place_ids[0]),
                    raw_title=context.display_name,
                    raw_description=context.description,
                    raw_url=url,
                    niche=self.niche,
                    source_artifact_id=artifact.id,
                    source_artifact_sha256=artifact.sha256,
                    extraction_method="page_capture",
                    source_tier="primary",
                )
                decision = self.o.associations.associate(
                    db, subject, pool, niche=self.niche,
                    candidate_row_ids={item.candidate_id: item.candidate_id for item in self.contexts},
                )
                if is_downstream_admissible(decision.record):
                    accept_web_claim(db, candidate_id=context.candidate_id, claim_value=context.description,
                                     metric="web_description", evidence=[(artifact, context.description)],
                                     association_id=decision.record.id)
                else:
                    self.b.abstain("primary_page", f"page claim not admissible: {decision.record.outcome}")
            db.commit()
        self.b.save(processed_pages=list(dict.fromkeys(self.b.state.get("processed_pages", []) + [url])))

    def dossiers(self):
        with self.factory() as db:
            return [{"candidate_id": c.candidate_id, "universe_id": c.universe_id,
                     "facts": evidence_packet(db, c.candidate_id), "history": history(db, c.candidate_id),
                     "niche_relevance": "provisional_requires_human_review"} for c in self.contexts]

    def questions(self, dossiers, final=False):
        facts = [f for d in dossiers for f in d["facts"]]
        def evidence(template):
            return [f["id"] for f in facts if f["template_id"] in template]
        support = {"demand": evidence({"roblox_playing", "roblox_visits"}),
                   "mechanics": evidence({"roblox_description", "web_claim"}),
                   "creators": evidence({"youtube_title", "youtube_views"})}
        limitations = {
            "relevance": "Discovery is provisional; no validated game-to-niche relevance rule or human review yet.",
            "history": "Consecutive captured history is required; no historical data is invented.",
            "mechanics": "Descriptions are developer claims, not independent gameplay observation.",
            "creators": ("Associated metadata is not watched video or audience sentiment." if support["creators"]
                         else "No usable creator association; metadata is not watched video or audience sentiment."),
            "counterevidence": "No defensible absence-of-competition or market-success conclusion; interpretations remain speculative.",
            "mvp": "Planning estimates are unverified design assumptions, not measured build times.",
            "demand": "Missing fresh source-reported demand measurements.",
        }
        questions = []
        for key, text in QUESTION_TEXT.items():
            ids = support.get(key, [])
            answered = bool(ids) or (key == "history" and any(d["history"]["trend"] is not None for d in dossiers))
            questions.append({"id": key, "question": text, "state": "answered" if answered else ("limited" if final else "open"),
                              "fact_ids": ids, "limitation": limitations[key] if key != "demand" or not ids else "Current snapshots do not establish growth."})
        self.b.save(questions=questions, evidence_additions=len(facts))
        return questions

    async def investigate(self):
        queries = discovery_queries(self.niche)
        stagnant = self.b.state.get("stagnant_rounds", 0)
        prior = self.b.state.get("last_evidence_count", 0)
        questions = self.b.state.get("questions") or self.questions(self.dossiers())
        for round_index in range(self.b.state.get("round", 0), self.b.state["limits"]["rounds"]):
            if self.b.remaining <= 180:
                return "finalization_reserve"
            # Every capture this round exists to answer something still open.
            plan = plan_round(questions, self.contexts, queries, round_index)
            if not plan:
                return "all_questions_answered"
            self.b.save(stage=f"Investigating round {round_index + 1}", plan=plan)
            for action in plan:
                if self.b.remaining <= 180:
                    return "finalization_reserve"
                budget = max(.01, self.b.remaining - 180)
                try:
                    if action["kind"] == "discover":
                        await asyncio.wait_for(self.discover(action["query"]), budget)
                    elif action["kind"] == "youtube":
                        # Names are captured primary metadata, never model-authored.
                        query = f'Roblox "{action["display_name"][:100]}" gameplay'
                        await asyncio.wait_for(self.o._attach_youtube(
                            self.niche, self.contexts, budget=self.b, query=query), budget)
                    else:
                        target = next((c for c in self.contexts
                                       if c.candidate_id == action["candidate_id"]), None)
                        if target is not None:
                            await asyncio.wait_for(self.capture_primary(target), budget)
                except Exception as exc:
                    self.b.error(action["kind"], exc)
            dossiers = self.dossiers()
            questions = self.questions(dossiers)
            count = sum(len(d["facts"]) for d in dossiers)
            stagnant = stagnant + 1 if count <= prior else 0
            prior = count
            self.b.save(round=round_index + 1, stagnant_rounds=stagnant, last_evidence_count=count)
            if all(q.get("state") == "answered" for q in questions):
                return "all_questions_answered"
            if stagnant >= 2:
                return "no_new_admissible_evidence_two_rounds"
        return "round_limit"

    async def concepts_and_audits(self):
        self.b.save(stage="Comparing evidence and drafting research concepts")
        dossiers = self.dossiers()
        def demand(dossier):
            for fact in dossier["facts"]:
                if fact["template_id"] == "roblox_playing":
                    return float(fact["slots"][0]["value"])
            return -1
        chosen = sorted(dossiers, key=lambda d: (-demand(d), d["universe_id"]))[:3]
        # Bounded packet fits local context; descriptions are already untrusted.
        comparison = [{"candidate_id": d["candidate_id"], "facts": d["facts"], "niche_relevance": d["niche_relevance"]} for d in chosen]
        gaps = [q["limitation"] for q in self.b.state["questions"] if q["state"] != "answered"]
        for dossier in chosen:
            if self.b.remaining <= 5:
                return
            cid = dossier["candidate_id"]
            metrics = {f["template_id"] for f in dossier["facts"]}
            if not {"roblox_name", "roblox_playing", "roblox_visits"} <= metrics:
                continue
            with self.factory() as db:
                hunter = db.scalar(select(Proposal).where(Proposal.candidate_id == cid, Proposal.agent == "meta_hunter"))
            try:
                if hunter is None:
                    generated = await asyncio.wait_for(self.o.llm.generate(agent="Meta Hunter", niche=self.niche,
                        sourced_name="Selected sourced experience", fact_ids=[f["id"] for f in dossier["facts"]], evidence=dossier["facts"],
                        comparison=comparison, gaps=gaps, before_attempt=self.b.model_attempt, require_citations=True), timeout=self.b.remaining)
                    with self.factory() as db:
                        titles = [p.payload.get("concept_title", "").casefold() for p in db.scalars(select(Proposal).join(Candidate).where(Candidate.run_id == self.run_id))]
                        if generated.payload.concept_title.casefold() in titles:
                            self.b.abstain("hunter", "duplicate concept title; no second concept emitted")
                            continue
                        # A concept that restates a discovered game is not a
                        # hypothesis, it is that game's name handed back.
                        existing = restates_discovered_game(
                            generated.payload.concept_title, [c.display_name for c in self.contexts]
                        )
                        if existing:
                            self.b.abstain("hunter", f"concept restates a discovered game ({existing}); not an original hypothesis")
                            continue
                        hunter = Proposal(candidate_id=cid, agent="meta_hunter", payload=generated.payload.model_dump(), model_name=generated.model)
                        db.add(hunter)
                        db.commit()
                with self.factory() as db:
                    audit = db.scalar(select(AuditRecord).where(AuditRecord.proposal_id == hunter.id))
                if audit is None:
                    if not self.b.state.get("scout_followup_done") and self.b.remaining > 180 and hunter.payload.get("questions"):
                        self.b.save(scout_followup_done=True, stage="Scout follow-up: checking remaining evidence gaps")
                        target = next(c for c in self.contexts if c.candidate_id == cid)
                        try:
                            if self.b.state["usage"].get("youtube_search", 0) < self.b.state["limits"]["youtube_search"]:
                                await asyncio.wait_for(self.o._attach_youtube(self.niche, self.contexts, budget=self.b,
                                    query=f'Roblox "{target.display_name[:100]}" gameplay review'), timeout=max(.01, self.b.remaining - 180))
                            await asyncio.wait_for(self.capture_primary(target), timeout=max(.01, self.b.remaining - 180))
                        except Exception as exc:
                            self.b.error("scout_followup", exc)
                        updated = self.questions(self.dossiers())
                        gaps = [q["limitation"] for q in updated if q["state"] != "answered"]
                    self.b.save(stage="Venture Scout: auditing selected proposal")
                    await self.o.audit(cid, hunter.id, budget=self.b, gaps=gaps)
            except Exception as exc:
                self.b.error("concept_or_audit", exc)

    def finalize(self, stop, partial=False):
        dossiers = self.dossiers()
        questions = self.questions(dossiers, final=True)
        self.b.save(stage="partial" if partial else "complete", stop_reason=stop)
        with self.factory() as db:
            run = db.get(ResearchRun, self.run_id)
            run.status = "partial" if partial else "complete"
            run.message = "Research concepts only; niche relevance remains provisional and scoring is locked."
            run.completed_at = datetime.now(UTC)
            proposals = list(db.scalars(select(Proposal).join(Candidate).where(Candidate.run_id == self.run_id, Proposal.agent == "meta_hunter")))
            audits = list(db.scalars(select(AuditRecord).join(Candidate).where(Candidate.run_id == self.run_id)))
            payload = {"version": "deep-report-v1", "run_id": self.run_id, "niche": self.niche,
                       "progress": progress(db, run), "questions": questions, "comparison": dossiers,
                       "selection_rule": "Up to three captured candidates by current source-reported CCU; not an opportunity score or validated niche ranking.",
                       "concepts": [{"id": p.id, "candidate_id": p.candidate_id, "classification": "speculative_research_concept", "payload": p.payload} for p in proposals],
                       "audits": [{"audit_id": a.id, **a.payload} for a in audits],
                       "limitations": [q["limitation"] for q in questions], "passing_recommendations": [],
                       "abstentions": self.b.state.get("abstentions", []),
                       "api_usage": self.b.state["usage"], "model_attempts": self.b.state["model_calls"],
                       "quota_note": "Local reservations only; remote remaining quotas are unknown. No video watching, transcripts or audience sentiment analysis performed."}
            db.add(ResearchReport(run_id=self.run_id, payload=payload))
            db.commit()

    async def run(self):
        if self.b.remaining <= 0:
            self.finalize("deadline", True)
            return
        stop, partial = "complete", False
        try:
            async with asyncio.timeout(self.b.remaining):
                stop = await self.investigate()
                await self.concepts_and_audits()
                # Only genuine failures downgrade the run. Reaching a configured
                # cap is recorded in `budget_stops` and reported separately.
                partial = bool(self.b.state["errors"])
        except asyncio.CancelledError:
            self.b.save(stage="interrupted", stop_reason="service_shutdown")
            raise
        except Exception as exc:
            self.b.error("controller", exc)
            stop, partial = ("deadline" if isinstance(exc, TimeoutError) else "controller_error"), True
        self.finalize(stop, partial)
