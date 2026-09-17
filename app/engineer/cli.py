"""`venture-engineer`: run the Roblox Engineer from a terminal on the game PC.

    venture-engineer check [--path DIR]    run the gate on a project (verify.ps1 + guard), no model
    venture-engineer ping                  one tiny request per Gemini key, to prove both work
    venture-engineer run TASK.json         build one system from an audited design
    venture-engineer usage                 today's Gemini calls and tokens per key

The Roblox service list is refreshed by `python scripts/refresh_roblox_services.py`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from ..config import get_settings
from .catalog import CatalogMissing
from .gemini import GeminiClient, GeminiError
from .runs import USAGE_PREFIX, NoDesign, NotConfigured, build_gate, game_repo, run_task
from .schemas import EngineeringTask


def _print_report(report) -> None:
    for check in report.checks:
        print(f"[{'PASS' if check.passed else 'FAIL'}] {check.name}")
        if not check.passed and check.output:
            print("    " + check.output.replace("\n", "\n    "))
    print("\nAll checks passed." if report.passed else f"\n{len(report.failed)} check(s) failed.")


def _check(path: str | None) -> int:
    settings = get_settings()
    report = build_gate(settings).run(Path(path) if path else game_repo(settings))
    _print_report(report)
    return 0 if report.passed else 1


async def _ping() -> int:
    settings = get_settings()
    failures = 0
    for label, key in (("GEMINI_API_KEY_1", settings.gemini_api_key_1), ("GEMINI_API_KEY_2", settings.gemini_api_key_2)):
        if not key:
            print(f"{label}: not set")
            failures += 1
            continue
        client = GeminiClient(keys=[key], base_url=settings.gemini_base_url, timeout=120)
        try:
            reply = await client.generate(model=settings.gemini_pro_model, system="Reply with the JSON {\"ok\": true}.",
                                          prompt="ping")
            print(f"{label}: OK via {reply.model} ({reply.usage.get('total_tokens', 0)} tokens)")
        except GeminiError as exc:
            print(f"{label}: FAILED -- {exc}")
            failures += 1
        finally:
            await client.close()
    return 1 if failures else 0


async def _run(task_file: str) -> int:
    from ..db import SessionLocal

    settings = get_settings()
    try:
        task = EngineeringTask.model_validate_json(Path(task_file).read_bytes())
    except (OSError, ValidationError) as exc:
        print(f"Task file refused: {exc}")
        return 2

    def show(entry: dict) -> None:
        print(f"[{entry['stage']}] {entry['detail']}")
        for check in entry.get("checks", []):
            if not check["passed"]:
                print(f"    {check['check']}: " + check["output"][:400].replace("\n", "\n        "))

    result = await run_task(task, settings=settings, factory=SessionLocal, on_event=show)
    print(f"\n{result.status.upper()}: {result.reason}")
    if result.branch and result.status == "complete":
        print(f"Branch {result.branch} at {result.commit}. Review it, then merge into {settings.game_base_branch}.")
    elif result.branch:
        print(f"The last refused attempt is kept on {result.branch} at {result.commit}. Do not merge it.")
    if result.handoff:
        print(f"Claude Code handoff written to {result.handoff}")
    return 0 if result.status == "complete" else 1


def _usage() -> int:
    from ..db import SessionLocal
    from ..models import SystemState

    key = USAGE_PREFIX + datetime.now(UTC).date().isoformat()
    with SessionLocal() as db:
        row = db.get(SystemState, key)
    print(json.dumps(row.value_json if row else {}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="venture-engineer", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("--path")
    commands.add_parser("ping")
    run = commands.add_parser("run")
    run.add_argument("task")
    commands.add_parser("usage")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            return _check(args.path)
        if args.command == "ping":
            return asyncio.run(_ping())
        if args.command == "run":
            return asyncio.run(_run(args.task))
        return _usage()
    except (NotConfigured, NoDesign, CatalogMissing) as exc:
        print(exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
