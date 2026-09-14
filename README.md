# Roblox Venture Agents

A local, evidence-first research dashboard with two constrained agent workflows:

- **Meta Hunter** discovers Roblox market candidates and collects primary evidence.
- **Venture Scout** proposes a small MVP and risk checklist for a selected candidate.

The trusted layer guarantees **zero unsupported factual claims**: facts are compiled from hashed source artifacts through JSON pointers or captured exact passages. The language model cannot author URLs, metrics, scores, confidence, or verdicts.

## Current operating phase

The application begins in **collection mode**. It intentionally emits no composite score and recommends no opportunity until all of these gates pass:

1. At least 200 candidates have complete 30-day observation windows.
2. The chronological held-out benchmark reaches at least 95% recommendation precision.
3. At least ten held-out examples are automatically recommended.
4. The frozen provenance and feature-schema checks pass.

The learned model uses only the opening seven-day evidence window to predict the later 30-day outcome. Outcome-period changes are never reused as input features, preventing target leakage.

This is a market-growth signal, not a promise that a game will succeed.

## The association engine

A YouTube video or a captured web page only counts towards a Roblox experience if the
association between them is recorded, versioned and approved. `app/association/` owns that
decision end to end; nothing else in the codebase is allowed to decide a match.

**Identity, not display names.** Every source (`MatchSubject`) and every target
(`MatchCandidate`) has an internal ID, and a verified external ID where one exists. A display
name never establishes identity: two experiences can share a name, and a name can change after
capture, which is why a Roblox game link is reduced to its place ID and its slug is discarded.

**Deterministic all the way down.** Normalization (`norm-v1`), the feature set
(`features-v1`) and the decision policy (`thresholds-v1`) are pure functions of stored text and
stored identifiers. The language model has no path into any of them. Every verdict stores its
full feature vector, which inputs were actually present, the runner-up and the margin, the
rationale codes, every version identifier, the embedding model, and the source artifact hashes.

**Four outcomes, and abstaining is a success.** `auto_associate`, `review_required`,
`no_match`, `blocked_conflict`. Automatic association needs no hard conflict, a score above the
high threshold, a minimum top-one/top-two margin, enough required-feature coverage, and either
exact verified-ID evidence or a validated combination of independent features. Dense embeddings
may retrieve a candidate; they can never approve one, and an embedding outage degrades to
lexical retrieval with the outage recorded on the record.

**Hard rules run before scoring.** A direct Roblox URL that resolves to a candidate is strong
positive evidence; an explicit ID belonging to a different experience is a hard contradiction;
two conflicting explicit IDs block association outright; a generic title cannot auto-associate
on dense similarity alone; a title that names a different experience is not overruled by a
description link; missing fields are reported, never guessed; duplicated or syndicated content
counts once; and text that tries to issue instructions is stored as untrusted content with no
control effect on any verdict.

**Shadow mode.** The engine ships in shadow mode: every proposal is recorded, only exact
verified-ID matches are accepted automatically, and every fuzzy proposal goes to the **Matching
Review** page. Unresolved associations create no observations, so they cannot reach scoring or
recommendations.

**Append-only review.** A human review or override is a new row. It never edits the engine's
original verdict, and reviews become labeled data for the benchmark.

**The downstream gate.** Only two things let a YouTube or web metric through:
an `auto_associate` verdict resting on exact verified-ID evidence or coming from a validated
matcher version, or a human confirmation. This holds for the daily snapshot too: a video whose
association was never approved, or was later rejected, stops being measured.

### Validating the thresholds

```powershell
uv run venture-assoc-benchmark --write
```

The dataset is a hand-authored seed file (`app/association/data/seed_dataset.json`) covering
clear positives and negatives, similar-name competitors, sequels, clones, generic titles,
multi-game videos, renamed experiences, missing descriptions, name collisions and
prompt-injection text, merged with every operator review recorded so far. Splitting is grouped
by candidate/niche cluster *and* ordered by discovery time, so a sequel cannot leak into the
held-out set through its base game.

Fuzzy automatic association is enabled only when held-out precision reaches 99% on a large
enough sample. On the seed data alone the engine makes **no false positives**, but there are too
few held-out fuzzy decisions to support a 99% claim, so fuzzy matching stays in review-only mode
until enough real review labels accumulate. That is the intended behaviour, not a failure.

## Setup

Requirements already supported by this machine: Python 3.12, Node.js, `uv`, and Ollama.

1. Put a Tavily key and YouTube Data API key in `.env`.
2. Install dependencies and build the dashboard:

   ```powershell
   uv sync --extra dev --extra embeddings
   Set-Location frontend
   npm install
   npm run build
   Set-Location ..
   ```

3. Install the primary model if needed:

   ```powershell
   ollama pull qwen3:14b
   ```

4. Start the dashboard:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\start_dashboard.ps1
   ```

   Open `http://127.0.0.1:8742`. The service binds only to this PC.

5. Register automatic startup:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\install_startup.ps1
   ```

   This registers and immediately starts a hidden per-user task. Windows restarts it
   after failures, and starts it again at sign-in. Service output is written to
   `data/service.log`.

Daily model-free snapshots run at 02:00 Asia/Kolkata. If the PC was off, one catch-up run starts after the service returns; missing dates are never invented.

## Development

```powershell
uv run pytest -q
Set-Location frontend
npm run build
```

Train the score only after the calibration page reports enough complete windows:

```powershell
uv run venture-calibrate
```

The resulting transparent JSON artifact records the dataset hash, feature order, learned coefficients, scaler, calibration curve, threshold, and held-out results. Failed benchmarks stay inactive.

## Source policy

- Roblox and YouTube JSON APIs are primary evidence.
- Tavily results only discover URLs and identifiers; snippets never create facts.
- A webpage requires one primary publisher or two independent corroborating publishers, exact captured passages, and distinct content hashes.
- Conflicts, stale data, failed parsing, unknown evidence IDs, and invalid model output fail closed.

The system does not guarantee that a publisher is truthful, that research is complete, or that a proposed game will succeed.
