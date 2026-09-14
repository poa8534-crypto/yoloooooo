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
