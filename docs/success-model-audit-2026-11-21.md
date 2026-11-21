# Success-probability model audit (21 November 2026)

What this audit asked: given a game idea or a game's data, can this system give a
calibrated probability that it succeeds? Short answer: **not yet, and the one
number it currently labels "measured" is misleading.** Every figure below was
measured against the live ledger (`data/venture_agents.db`) on 21 Nov 2026.

## What exists

- `app/market_pulse.py` samples Roblox's front-page shelves (`explore-api/v1/get-sorts`)
  every 30 minutes, plus the live player count of every researched game ("tracked").
- `app/pillars.py` turns a game's rows into demand, momentum, acceleration,
  reception, genre saturation and shelf visibility.
- `app/opportunity.py` blends five of those with fixed hand-picked weights into a 0..1 index.
- `app/calibration_outcomes.py` buckets that index into four bands and reports, per band,
  the share of games whose peak CCU in their last 24 h of history reached 5,000.
  The dashboard shows this as `state: "measured"`.
- `app/calibration.py` is a separate, older trainer (7-day features, 30-day outcome,
  niche clusters). It is gated off and its gates have never passed.

Ledger today: 1,298,168 rows, 1,606 games, 1,765 captures from 15 Sep to 21 Nov 2026.

## Findings, worst first

### 1. The "measured" calibration is circular

The index is 30 % "demand", which is the game's current player count. The outcome is
"peak player count in the next 24 h is at least 5,000". So the index mostly predicts
that a game with many players now still has many players tomorrow.

Measured on the same 1,385 outcomes the dashboard uses:

| Predictor | Result |
| --- | --- |
| Rule "already at 5,000+ at the cutoff" | 89.7 % accuracy |
| Raw player count at cutoff, alone | AUC 0.973 |
| Games below 5,000 at cutoff that then reached it | 119 of 879 |

The dashboard's "96 % of games scoring 0.5-0.75 reached 5,000" is persistence, not
foresight. There is also no held-out split (the bands are reported on the data they
are computed from), no Brier score or log loss, and no comparison against a baseline.

### 2. It answers a different question from the one asked

Every row is an existing game, mostly one Roblox already put on a front-page shelf.
Nothing in the system takes an idea (genre, mechanics, comparable games) and returns a
probability. `probability_for` only works for a universe ID that already exists.

### 3. The ledger has a hole every night, during US peak hours

69 gaps longer than an hour; most are 8 to 20 hours, one is 45 hours. Windows'
power log matches them exactly: **the PC goes to sleep** (roughly 22:30-10:00 local,
which is about 17:00-04:30 UTC). The watchdog cannot help, because it does not run
while the machine sleeps, and its task does not have "wake to run" set.

The holes fall on US afternoon and evening, which is Roblox's daily peak. So:

- recorded peaks are biased low, and "reached 5,000" undercounts;
- momentum is computed first-row to last-row and so mixes growth with time of day;
- a gap is not recorded anywhere, so a rate across a 20-hour gap looks like any other rate.

### 4. Tracked rows cite the wrong evidence

`sample_market` stores only the explore-sorts payload as the artifact, then writes the
"tracked" rows (583,666 of the 1.3 M) pointing at it, although their numbers came from
`games/v1/games` and `games/v1/games/votes`. Those payloads are never stored, so 45 % of
the census cannot be traced to the bytes it came from. This breaks the repo's own
provenance rule.

### 5. The data needed to judge a new game is not recorded

The explore payload has no creation date. `games/v1/games` does (`created`, `updated`,
`visits`, `favoritedCount`, `genre_l2`), and the service already calls it, but only
player count and genre are kept for tracked games, and nothing is fetched for shelf
games. Without the creation date the system cannot tell a two-week-old breakout from a
five-year-old incumbent, which is the whole question for a new idea.

### 6. Survivorship is not stated

Shelf games are winners by construction. Tracked games come from search and include
failures, which makes them the less biased sample, but there are only 350. Any rate
computed here is "given the game was noticed", and must say so.

### 7. Slow

`/api/calibration/outcomes` took 1 min 49 s. `/api/opportunity/{id}` calls
`calibrate()` inside every request, so each game page pays that cost.

### 8. Minor

- The weights and reference constants in `opportunity.py` are a hand prior; that is
  stated in the code, which is fine, but they are code, not data.
- Target (5,000 CCU) and horizon (24 h) are constants; a "success" definition should be data.
- The watchdog task runs `powershell.exe -WindowStyle Hidden`, which still flashes a
  console for a moment because the window exists before it is hidden.

## Creator Exchange (creatorexchange.io)

Found and checked.

- **What it has.** A public JSON endpoint (`/api/v2/roblox/discover`) listing games with
  `playing`, `visits`, `favoritedCount`, `created`, `updated`, `genre_l1/l2`, like ratio,
  and its own `revenue` and `revenueIndicator`. The momentum and rank-shift fields were
  all null when checked.
- **robots.txt** allows generic crawlers everywhere.
- **Terms of service, section 3.2**: you may not "copy, modify, or create derivative works
  based on the Service(s) or Service Data", or use it to build a competing product.
  Use is limited to viewing for internal business purposes. Section 7: they own the Service Data.
- **Value.** Everything except revenue is Roblox first-party data we can read directly
  from `games.roblox.com`, which the service already uses. The only unique field,
  revenue, is their own model's estimate, not a measurement.

**Recommendation: do not integrate it automatically.** Storing their data in the ledger
and modelling on it is a derivative work under 3.2, and the one thing it adds is an
estimate, which the "no plausible-looking numbers" rule would have to label as
someone else's guess. The same first-party fields are fetched directly instead
(fix B below). If revenue estimates are wanted later, the clean route is asking them
for written permission or a paid data licence (contact listed in their terms).

## Proposed fix plan, in priority order

A. **Honest evaluation.** Replace the in-sample band table with a chronological
   backtest: fit on earlier windows, score later ones, report Brier score, log loss,
   AUC and a reliability table, always next to two baselines (overall base rate, and
   "already above target"). Change the default outcome to a growth question that is
   not answered by today's CCU (for example "a game below the target at the cutoff
   reaches it"). Success definitions (threshold, horizon, condition) become data.
   The dashboard stops calling anything "measured probability" unless it beats the
   baselines out of sample. Cache the result per census so pages stop taking minutes.

B. **Record what a new-game model needs.** For every universe seen, fetch
   `games/v1/games` (batched 50, first-party) and keep `created`, `updated`, `visits`,
   `favoritedCount`, `genre_l2` as a time series. One-time backfill for the 1,606 known games.
   Store the games and votes payloads as their own artifacts, fixing the provenance bug.

C. **Coverage, not just samples.** Record each sampler run's coverage, so a gap is a
   known gap. Momentum is computed only between observations with no gap longer than
   a limit, and compares like hours of the day where possible. The dashboard shows
   coverage per day.

D. **Idea-level base rates.** Given a genre (and optionally a list of comparable games
   found by search), report the observed share of young games (created within N days of
   first sighting) that reached a success definition, with a Beta posterior interval and
   the sample size, stated as conditional on being noticed. Backtested the same way as A.
   Refuses below a minimum sample.

E. **Keep the PC sampling at night** (owner decision, needs a choice). Options:
   (1) the service asks Windows not to idle-sleep while it runs (`SetThreadExecutionState`;
   a closed lid or manual sleep still sleeps); (2) set "wake to run" on a sampler task so
   the PC wakes every 30 min (changes power behaviour; owner's call); (3) leave it and
   rely on C to mark the holes.

F. **Watchdog without the flash.** Launch it through a hidden `wscript` wrapper (or
   `conhost --headless`) instead of `powershell -WindowStyle Hidden`.

What will remain true after all of this: the system can report how often games like an
idea have succeeded, and how accurate that has been on held-out history. It cannot know
whether one specific new game will succeed, and the output will say so with the measured
error next to it.

---

## What was built (approved 21 Nov 2026: A, B, C, D, F; Creator Exchange skipped)

- **A** `app/success_model.py`, `config/success_definitions.json`. Three game
  questions as data. Chronological held-out backtest against the base rate and a
  player-count-only model, with a day-block bootstrap; a probability is shown
  only for a model that wins in at least 95 % of resamples. Recomputed in a
  worker thread after each census, so pages no longer wait 1 min 49 s.
  `app/calibration_outcomes.py` and its tests were retired; its endpoint now
  says so and returns the backtest.
- **B** `game_snapshots` table (append-only): creation date, update date,
  visits, favourites, sub-genre per game, each row citing its stored payload.
  New games are dated on first sighting; every known game daily. Tracked
  rows now cite their own games/votes payload. One-time backfill
  (`scripts/backfill_game_metadata.py`): 1,319 of 1,407 games dated; Roblox
  returned nothing for the other 88.
- **C** `app/coverage.py` and `/api/census/coverage`. Momentum and
  acceleration (pillars-v2) use only the latest unbroken stretch, at most 24 h.
- **D** `app/idea_rates.py` and `/api/success/idea?genre=...`.
- **F** the watchdog runs through `scripts/watchdog_hidden.vbs` (landed by a
  parallel session as b653963; checked: task result 0, no window).

## Measured results on the live ledger (21 Nov 2026, held out, chronological)

| Question | Test examples | Verdict | Held-out Brier (model / base rate) | AUC |
| --- | --- | --- | --- | --- |
| Below 5,000 now, reaches 5,000 in 24 h | 6,759 (340 successes) | player count only | 0.0271 / 0.0478 | 0.973 |
| 200+ players now, doubles in 72 h | 6,184 | no skill, nothing shown | 0.1409 / 0.1409 | 0.50 |
| 5,000+ now, still 5,000+ after 7 days | 2,703 | player count only | 0.0274 / 0.0315 | 0.888 |

Momentum (same hour of day, 24 h and 72 h), votes, shelf placement and game
age added nothing measurable over today's player count on any question. The
honest summary: **the census can tell how big a game will be tomorrow from
how big it is today, and cannot yet predict growth.**

Calibration of the 5,000-in-24 h model (held out): predicted 15 % came true
9 %, 25 % came true 21 %, 35 % came true 29 %, 45 % came true 40 %, 55 % came
true 68 %. Slightly overconfident below 50 %, underconfident above.

Idea level (young games first seen within 30 days of creation, 391 games):
reaching 5,000 players within 30 days, genre rates beat the all-genre rate on
118 later games in 96 % of resamples (Brier 0.202 vs 0.209), a small effect.
For 20,000 players genre did not help reliably (78 %). Example: Simulation,
5,000 in 30 days: 75 % (90 % interval 70-80 %), versus 69 % for all genres. These
are rates among games that got noticed, not among games published.

## Still open

- **Night gaps** (half the history is in gaps). The owner wants the PC's
  stability fixed before deciding whether it should stay awake.
- **No growth signal yet.** Candidates: ingest favourites/visits growth from
  the daily snapshots once there are weeks of them; sub-genre; update recency.
- **Sample of failures.** The cohort is mostly shelf games. Tracking a random
  sample of newly created games (not just shelved ones) would give a real
  denominator for "probability a published game succeeds".
