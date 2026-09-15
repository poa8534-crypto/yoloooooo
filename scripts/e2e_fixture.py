"""Run the app against an isolated ledger, for browser tests.

Browser tests have to exercise the actions that change data -- starting a run,
auditing a game, approving a match -- and none of that may touch the operator's
real ledger, artifacts or API quota. So this starts two throwaway servers:

* a stub that answers the model endpoint with canned, schema-valid JSON, so an
  audit really runs end to end without a GPU or a queue;
* the application itself, pointed at a temporary database and artifact
  directory, with the stub as its model server and no API credentials at all.

Connector calls are not stubbed because the seeded fixture already contains the
captured evidence the pages read. A test that needs a network capture is out of
scope here and is covered by the backend suite instead.

    python -m scripts.e2e_fixture --port 8799 --state-file <path>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# A design the firewall accepts: ordinals only, no measurements in prose.
STUB_PROPOSAL = {
    "concept_title": "Harbour Lantern Cooperative",
    "core_loop": "Two players share one boat, choosing where to cast and when to return to port.",
    "differentiator": "The boat is shared rather than owned, so every choice is negotiated.",
    "build_steps": ["Day 1: block out the harbour and one fishing spot",
                    "Day 2: add the shared boat and casting",
                    "Day 3: playtest with two people and cut what does not land"],
    "risks": ["Nothing captured shows whether shared control is enjoyable."],
    "questions": [],
    "supporting_fact_ids": [],
    "design_assumptions": [],
    "essential_features": ["A shared boat", "One casting interaction"],
    "excluded_features": ["Monetisation", "Trading"],
    "dependencies": ["A single reusable interaction script"],
    "validation_tasks": ["Playtest the loop with two people"],
    "counterevidence": [],
    "executive_summary": "One shared boat is the smallest cooperative loop worth building first, "
                         "and it is the only part a solo beginner can finish in the time available.",
    "opportunity_gap": "The captured evidence shows what exists, not why anyone stays, so the "
                       "cooperative angle remains an untested hypothesis rather than a gap.",
    "competitive_notes": ["Nearby experiences are single-player."],
}

# Long enough that a browser test can cancel a run, short enough that the rest
# of the suite is not slowed to a crawl.
MODEL_DELAY_SECONDS = float(os.environ.get("E2E_MODEL_DELAY", "1.5"))

STUB_CRITIQUE = {
    "weaknesses": ["Scope assumes art that does not exist yet"],
    "missing_dependencies": [],
    "scope_risks": [],
    "unsupported_claims": [],
}


def start_model_stub() -> str:
    """A minimal stand-in for the local model server."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def _send(self, payload, status=200):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send({"models": [{"name": "qwen3:14b"}, {"name": "qwen3:8b"}]})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
            # A real pass takes minutes. Answering instantly would make a job
            # finish before a test could reach the cancel control, so the
            # cancellable window is real but short.
            time.sleep(MODEL_DELAY_SECONDS)
            body = json.loads(raw or b"{}")
            schema = body.get("format") or {}
            properties = schema.get("properties") or {}
            if "weaknesses" in properties:
                self._send({"message": {"content": json.dumps(STUB_CRITIQUE)}})
                return
            # The real model is constrained to cite from an enum of the exact
            # supplied fact IDs. The stub honours the same constraint, so the
            # citation guard is genuinely exercised rather than bypassed.
            allowed = (properties.get("supporting_fact_ids", {}).get("items", {}) or {}).get("enum") or []
            payload = {**STUB_PROPOSAL, "supporting_fact_ids": allowed[:1]}
            self._send({"message": {"content": json.dumps(payload)}})

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}"


def seed(database_url: str, artifact_dir: Path) -> None:
    """Deterministic evidence, so assertions can name exact values."""
    from app.db import Base, SessionLocal, engine
    from app.evidence import add_json_observation, create_fact, record_artifact
    from app.migrations import install_append_only_triggers
    from app.models import Candidate, Proposal, ResearchRun

    Base.metadata.create_all(engine)
    install_append_only_triggers(engine)
    captured = datetime.now(UTC) - timedelta(hours=1)
    with SessionLocal() as db:
        run = ResearchRun(niche="cooperative fishing", status="complete",
                          message="Research concepts only.", completed_at=captured)
        db.add(run)
        db.flush()
        payload = {"data": [
            {"id": 101, "name": "Lantern Bay Fishing", "playing": 412, "visits": 90210,
             "favoritedCount": 1200, "updated": "source date", "description": "Fish together at dusk."},
            {"id": 202, "name": "Deep Harbour Crew", "playing": 88, "visits": 4100,
             "favoritedCount": 90, "updated": "source date", "description": "Crew up and sail."},
        ]}
        artifact = record_artifact(db, url="https://games.roblox.com/v1/games?universeIds=101,202",
                                   retrieval_method="scheduled_roblox_snapshot",
                                   content_type="application/json", payload=payload,
                                   source_tier="primary", owner="roblox.com", captured_at=captured)
        for index, item in enumerate(payload["data"]):
            candidate = Candidate(run_id=run.id, external_id=str(item["id"]))
            db.add(candidate)
            db.flush()
            for field, metric, unit in (("name", "roblox_name", None),
                                        ("playing", "roblox_playing", "players"),
                                        ("visits", "roblox_visits", "visits"),
                                        ("favoritedCount", "roblox_favorites", "favorites"),
                                        ("updated", "roblox_updated", None),
                                        ("description", "roblox_description", None)):
                observation = add_json_observation(
                    db, artifact=artifact, candidate_id=candidate.id, metric=metric,
                    pointer=f"/data/{index}/{field}", unit=unit, observed_at=captured,
                )
                create_fact(db, template_id=metric, slots={"value": observation})
            if index == 0:
                db.add(Proposal(candidate_id=candidate.id, agent="meta_hunter",
                                payload=STUB_PROPOSAL, model_name="fixture"))
        db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()

    workspace = Path(tempfile.mkdtemp(prefix="venture-e2e-"))
    artifacts = workspace / "artifacts"
    artifacts.mkdir()
    database_url = f"sqlite:///{(workspace / 'e2e.db').as_posix()}"

    # Set before app modules are imported: settings are read once and cached.
    os.environ.update({
        "DATABASE_URL": database_url,
        "ARTIFACT_DIR": str(artifacts),
        "MODEL_DIR": str(workspace / "models"),
        "OLLAMA_BASE_URL": start_model_stub(),
        # No credentials: nothing here may reach a metered API.
        "TAVILY_API_KEY": "",
        "YOUTUBE_API_KEY": "",
        "PORT": str(args.port),
    })

    seed(database_url, artifacts)
    print(f"e2e fixture ledger at {workspace}", flush=True)

    import uvicorn

    from app.main import app
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
