# The system as it actually runs

Every box below is a module that exists in this repository. Nothing here is
aspirational: where a stage refuses to act without evidence, the refusal is
drawn, because the refusals are most of what separates this from a chatbot
with a Roblox theme.

**Legend**

```
  [LLM]   a local model is called here
  [DET]   deterministic; no model has any path into this code
  [GATE]  refuses rather than guesses, and records why
```

---

## 1. Always-on background engines

These run whether or not anybody is looking at the dashboard.

```
  +--------------------------------------------------------------+
  | WATCHDOG   scripts/watchdog.ps1, every 5 minutes              |
  |                                                               |
  | The census only runs while the service runs, and a gap cannot |
  | be backfilled: nobody can ask Roblox what the player counts   |
  | were two hours ago. The ledger carries two such holes, 7.7h   |
  | and 2.3h.                                                     |
  |                                                               |
  | Checks two different claims:                                  |
  |   1. is the scheduled task running at all                     |
  |   2. is its process listening on a socket                     |
  | Restarts on either. Nothing is logged unless something was    |
  | wrong, so every line in data/watchdog.log is an outage.       |
  +--------------------------------------------------------------+
         |
         v
   SERVICE START  (Task Scheduler, at logon -- app/service.py)
         |
         v
  +--------------------------------------------------------------+
  | app/binding.py                                        [DET]   |
  | Wait for the tailnet address to exist before binding.         |
  | Tailscale is slower to boot than we are; binding early        |
  | killed the service and nothing restarted it.                  |
  | EADDRNOTAVAIL -> wait (180s cap).  EADDRINUSE -> exit.        |
  +--------------------------------------------------------------+
         |
         v
  +--------------------------------------------------------------+
  | app/main.py  lifespan                                         |
  |   - init_db() + migrations                                    |
  |   - RESUME interrupted deep runs (checkpoint survives)        |
  |   - audit_jobs.reconcile()  (orphaned jobs marked, not lost)  |
  |   - catch_up_if_needed()    (missed daily snapshot)           |
  +--------------------------------------------------------------+
         |
         v
  +--------------------------------------------------------------+
  | app/scheduler.py   APScheduler (AsyncIOScheduler, Asia/Kolkata)|
  +--------------------------------------------------------------+
         |
         +--> JOB 1  market_pulse_sample    every 30 min, + on startup
         |            app/market_pulse.py                     [DET]
         |            Roblox explore-api get-sorts (6 shelves)
         |            + `tracked` sort for games under research
         |            -> rows into `market_samples` (census, append-only)
         |            Reason: every velocity is a derivative and a
         |            derivative needs two observations of one game.
         |
         +--> JOB 2  daily_metric_snapshot  02:00 local
                      app/scheduler.py snapshot_all           [DET]
                      games.roblox.com/v1/games  (playing/visits/faves)
                      + YouTube video stats, but ONLY through an
                      approved association record               [GATE]
                      -> observations + facts

  Passive engines, no schedule of their own:
    app/quotas.py          durable daily allowance per provider
    app/search_cache.py    identical query answered from cache, spaced 4s
    app/dependency_health.py  what each dependency last DID, not what is
                              configured; never infers one from the other
    app/research_budget.py durable per-run budget; reservations precede I/O
```

---

## 2. Ingest -> evidence ledger

Nothing downstream may read anything that did not come through here.

```
  EXTERNAL SOURCES (app/connectors.py)                        [DET]
  +-----------------------------+----------------------------------+
  | FIRST PARTY (primary tier)  | DISCOVERY (secondary tier)       |
  |  apis.roblox.com            |  SearxNG  (local, no key, free)  |
  |    explore-api/get-sorts    |  Tavily   (keyed, rationed)      |
  |    search-api/omni-search   |  YouTube Data API v3             |
  |    universes/v1/places/..   |  capture_page (raw HTML)         |
  |  games.roblox.com           |                                  |
  |    /v1/games                |  SSRF guard: public HTTP only,   |
  |    /v1/games/votes          |  private ranges refused          |
  +-----------------------------+----------------------------------+
         |
         v
  +--------------------------------------------------------------+
  | app/evidence.py      APPEND-ONLY LEDGER              [GATE]   |
  |                                                               |
  |  source_artifacts  gzipped body + SHA-256, URL, owner, tier   |
  |        |                                                      |
  |        v                                                      |
  |  observations      a JSON pointer INTO that artifact          |
  |        |           (a number nobody can retype)               |
  |        v                                                      |
  |  facts             template + slots, each slot an observation |
  |                                                               |
  |  market_samples    census rows; a claim about the market,     |
  |                    not about any single game                  |
  |                                                               |
  |  Enforced twice: SQLite UPDATE/DELETE triggers, and a         |
  |  SQLAlchemy do_orm_execute guard. Deleting either one is      |
  |  caught by a test.                                            |
  +--------------------------------------------------------------+
```

---

## 3. Association engine — does this source belong to this game?

A YouTube video or a web page is worthless until it is tied to a specific
Roblox experience, and tying it wrongly poisons everything above it.

```
  +--------------------------------------------------------------+
  | app/association/   version: assoc-v1     thresholds-v1 [DET]  |
  |                                                               |
  |  normalize.py   deterministic, versioned text views;          |
  |                 raw text is never mutated                     |
  |       |                                                       |
  |  retrieval.py   lexical + OPTIONAL dense embeddings           |
  |                 (BAAI/bge-small-en-v1.5, local).              |
  |                 Embeddings widen recall and can NEVER         |
  |                 approve a match alone. An outage degrades     |
  |                 to lexical and is recorded on the record.     |
  |       |                                                       |
  |  features.py    frozen, versioned feature vector              |
  |       |         no model reads or writes any of these         |
  |  rules.py       hard vetoes, applied first; a missing field   |
  |       |         degrades to review, never to a guess          |
  |  decisions.py   exactly one of:                               |
  |                   auto_associate | review_required            |
  |                   no_match       | blocked_conflict           |
  +--------------------------------------------------------------+
         |                              |
         | approved                     | review_required  [GATE]
         v                              v
  materialize.py                   MATCHING REVIEW page
  the ONLY writer of               human verdict -> labelled dataset
  cross-source evidence            -> benchmark.py measures held-out
                                      precision -> freezes a policy
                                      artifact. Fuzzy auto-association
                                      unlocks only if precision clears
                                      the floor; otherwise review-only.
```

---

## 4. MODEL 1 — measured pillars (no LLM anywhere)

```
  market_samples (census, repeated over time)
         |
         v
  +--------------------------------------------------------------+
  | app/pillars.py           version: pillars-v1      [DET][GATE] |
  |                                                               |
  |   demand         how many are playing this now                |
  |   momentum       is that audience growing or draining         |
  |   acceleration   is the growth itself steepening              |
  |   reception      first-party up/down vote integers            |
  |                  (this is why no sentiment classifier exists) |
  |   saturation     how crowded the genre is on the shelves      |
  |   visibility     does Roblox itself surface this game         |
  |                                                               |
  |  THREE RULES, each held by a test:                            |
  |   1. Nothing here sums pillars. No total, no blend.           |
  |   2. Absent evidence is never zero. A pillar with nothing     |
  |      behind it reports `insufficient_evidence` and says what  |
  |      is missing. Velocity 0 means measured and flat.          |
  |   3. A rate needs a real interval: < 15 min span reports      |
  |      insufficient rather than a spectacular hourly rate.      |
  +--------------------------------------------------------------+
```

---

## 5. MODEL 2 — opportunity arithmetic (no LLM anywhere)

```
  +--------------------------------------------------------------+
  | app/opportunity.py     version: opportunity-v1    [DET][GATE] |
  |                                                               |
  |   demand          0.30                                        |
  |   momentum        0.25                                        |
  |   reception       0.20                                        |
  |   genre headroom  0.15   (inverts saturation)                 |
  |   acceleration    0.10                                        |
  |                                                               |
  |  Fixed weights. Same rows -> same order, every time.          |
  |  Every component's contribution is printed next to the total  |
  |  so a reader can disagree with ONE input, not with a number.  |
  |                                                               |
  |  Refuses rather than guesses:                                 |
  |    - a pillar that abstained contributes nothing and is       |
  |      NAMED in `missing`                                       |
  |    - fewer than 3 measured components -> no score at all,     |
  |      because a score from 1 of 5 inputs looks identical to    |
  |      a score from 5 of 5                                      |
  |                                                               |
  |  It RANKS. It does not state a probability. Until Phase 4     |
  |  has outcomes, every score is marked `uncalibrated`.          |
  +--------------------------------------------------------------+
```

---

## 6. Phase 4 — P(CCU >= target) from outcomes actually watched

```
  +--------------------------------------------------------------+
  | app/calibration_outcomes.py                       [DET][GATE] |
  |                                                               |
  |  A game becomes an OUTCOME once watched for 24 hours.         |
  |  The score is computed from the history BEFORE the window,    |
  |  and the window is asked one question: did it reach target.   |
  |  The future cannot leak into its own prediction.              |
  |                                                               |
  |  Under 30 completed windows it reports `insufficient_outcomes`|
  |  and no number. A base rate over three games presented as a   |
  |  probability is worse than nothing: a reader can discount an  |
  |  absence, not a figure that looks computed.                   |
  |                                                               |
  |  The sampler produces outcomes on its own. This becomes       |
  |  measurable by WAITING, not by changing anything here.        |
  +--------------------------------------------------------------+

  Separate, older: app/calibration.py  version calibration-v1
    logistic regression + isotonic calibration (scikit-learn) over
    decision features; gates whether confidence may be displayed.
```

---

## 7. META HUNTER — finds the ideas

```
  YOU type a niche  ->  POST /api/research-runs
         |
         v
  +--------------------------------------------------------------+
  | app/workflows.py  ResearchOrchestrator                        |
  | app/deep_research.py  DeepResearch                            |
  |                                                               |
  | BUDGET (durable, app/research_budget.py):                     |
  |   1800s | 4 rounds | 12 tavily | 12 youtube | 30 captures     |
  |   30 universes | 60 videos | 24 model attempts                |
  |   Reservations precede I/O, so a crash cannot overspend.      |
  +--------------------------------------------------------------+
         |
         v
  app/query_planner.py                                  [LLM+DET]
    The model PROPOSES queries in the vocabulary Roblox players
    use. A curated vocabulary then expands, normalises and
    REFUSES the nonsense. ("toilet simulator simulator" was real.)
    Queries are not evidence. Planned once per run, then reused.
         |
         v
  ROUND LOOP (up to 4)                                      [DET]
    plan_round() picks actions that answer a STILL-OPEN question:
      discover  -> search -> app/discovery_rank.py ranks leads
      capture   -> first-party capture of a specific game
      youtube   -> gameplay video for a CAPTURED name only
    ...every capture lands in the ledger (section 2) and every
    cross-source tie goes through association (section 3).
         |
    after each round: rebuild dossiers, recompute open questions
    STOP EARLY on: all questions answered | 2 stagnant rounds
                   | candidate-inspection limit | 180s reserve
         |
         v
  concepts_and_audits()                                 [LLM+GATE]
    select_concept_dossiers() -> top 3 by relevance and demand
    A concept that merely restates a discovered game's name is
    rejected. Model prose NEVER enters a factual slot.
         |
         v
    Proposals  (agent = meta_hunter)
         |
         v
  CHECKPOINT written continuously -> a power cut or a restart
  resumes; it does not start over. (Cost two real runs to learn.)
```

---

## 8. Routing — Hunter to Scout

```
  +--------------------------------------------------------------+
  | app/scout_queue.py                                    [DET]   |
  |                                                               |
  | Routing is AUTOMATIC. Running is NOT.                         |
  |                                                               |
  | Every un-audited Hunter concept lands here by itself. The     |
  | queue then sits until you press the button.                   |
  |                                                               |
  | Why the split: one audit is several minutes of the local      |
  | model, and the model is serialized. A queue that ran itself   |
  | would decide for you how the next half hour of GPU goes.      |
  +--------------------------------------------------------------+
         |
         v
   DASHBOARD:  [ Start N audits ]  with a de-selection panel,
               horizontally scrolling concept cards, and a live
               count of what is ticked.
         |
         | you press it
         v
```

---

## 9. VENTURE SCOUT — evaluates one idea

```
  +--------------------------------------------------------------+
  | app/audit_jobs.py   addressable, bounded, persisted           |
  |   Start | Restart | Resume | Cancel                           |
  |   Resume  = original deadline and attempt count (interrupted) |
  |   Restart = a NEW job with a fresh budget (failed/timed out)  |
  |   Collapsing the two would either strand a failed audit or    |
  |   hand a runaway one an unlimited allowance.                  |
  +--------------------------------------------------------------+
         |
         v
  app/workflows.py  audit()
         |
         v
  READINESS GATES                                          [GATE]
    The Scout works on Hunter output and NOTHING else. A game
    with no Hunter proposal is refused outright -- otherwise the
    Scout spent minutes of model time on a game the Hunter never
    thought worth a concept.
    Failing gates -> recorded as risks, no model call is made.
         |
         v
  EVIDENCE PACKET  (app/research_evidence.py)              [DET]
    verified facts only, each resolving back to a hashed artifact
         |
         v
  +--------------------------------------------------------------+
  | app/llm.py   Ollama, LOCAL             [LLM]                  |
  |   primary  qwen3:14b     fallback  qwen3:8b                   |
  |   16k context, 600s per request, thinking on                  |
  |                                                               |
  |   PASS 1  draft      read the evidence, write the design      |
  |   PASS 2  critique   read its OWN draft back for weak scope   |
  |                      and unsupported claims                   |
  |   PASS 3  revise     correct the draft against that critique  |
  |                                                               |
  |   A pass that cannot produce usable output is recorded as     |
  |   skipped and the concerns are carried forward UNRESOLVED.    |
  |   An unrevised draft is never presented as a finished audit.  |
  |                                                               |
  |   Untrusted model text is length-capped and redacted, and     |
  |   never reaches a factual slot or the activity feed.          |
  +--------------------------------------------------------------+
         |
         v
  CITATION RE-VERIFICATION                                 [GATE]
    every supporting_fact_id is checked against the ledger again;
    anything that no longer holds is WITHDRAWN and listed
         |
         v
  AuditRecord written (append-only)
  evidence_state: source_backed_design_speculative
               |  source_backed_design_incomplete  (unresolved)
               |  blocked                           (gates failed)

  LIVE THE WHOLE TIME:
    app/audit_activity.py -> SSE feed, dual-write
      1. in-memory queue + asyncio.Event, zero-latency stream
      2. durable SQLite ledger, so a reload or restart never
         loses the chronological record
    It carries DESCRIPTIONS of the work, never model output.
```

---

## 10. Serving and access

```
  +--------------------------------------------------------------+
  | FastAPI  app/main.py                                          |
  |   app/access.py   token middleware, constant-time compare     |
  |                   over UTF-8 BYTES (a BOM in the token file   |
  |                   once 500'd it), min 24 chars, /login page   |
  |   assert_bindable: loopback needs nothing; anything wider     |
  |                    REQUIRES a token. No other unlock exists.  |
  |   FreshIndex: index.html is `no-cache`. Hashed assets cache   |
  |               forever. A cached index.html pinned the whole   |
  |               dashboard to the previous build.                |
  +--------------------------------------------------------------+
         |
         v
  Bound to the TAILSCALE address, not 0.0.0.0.
  Private tailnet, no public URL. Your MacBook anywhere in the
  world reaches this machine's GPU, because the browser is
  remote and the model is here.
         |
         v
  React SPA (frontend/src/App.tsx)
    home | ideas | sources | history | market | matching
    meta (Hunter) | scout (Venture Scout) | calibration | health
    Every number on screen links back to the fact, the
    observation, and the hashed artifact it came from.
```

---

## What the whole thing refuses to do

- Report a rate from one observation.
- Report zero when it means unmeasured.
- Let a model write a number into a factual slot.
- Let an embedding approve an association by itself.
- Present a probability before outcomes exist to calibrate it.
- Score a candidate from fewer than three measured components.
- Update or delete anything in the ledger.
