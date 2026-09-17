"""`venture-engineer`: run the Roblox Engineer from a terminal on the game PC.

    venture-engineer check [--path DIR]    run the gate on a project (verify.ps1 + guard), no model
    venture-engineer ping                  one tiny request per key and model, to see what answers today
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
from .runs import (
    USAGE_PREFIX,
    NoDesign,
    NotConfigured,
    build_gate,
    engineer_models,
    game_repo,
    run_task,
)
from .schemas import EngineeringTask


def use_utf8(stream) -> None:
    """Make `stream` carry any check output without killing the run.

    Windows gives this process a cp1252 stdout when it is redirected to a file,
    and selene draws its diagnostics with box-drawing characters. Printing one
    raised UnicodeEncodeError inside the event callback, which unwound the whole
    loop: a real run died at attempt 1 of 6 on the encoding of a message about
    why attempt 1 failed. `errors="replace"` keeps that impossible even for
    output no encoding covers.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        pass


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


async def _ping_ollama(settings) -> int:
    """Which local models answer. No key and no quota, so the only questions
    are whether the daemon is up and whether the model has been pulled."""
    from .ollama import OllamaClient, ollama_models  # noqa: F401
    from .runs import ollama_model_names

    answered = 0
    names = ollama_model_names(settings)
    for model in names:
        client = OllamaClient(base_url=settings.engineer_ollama_base_url, timeout=120)
        try:
            reply = await client.generate(model=model, system='Reply with the JSON {"ok": true}.',
                                          prompt="ping")
            print(f"local {model}: OK ({reply.usage.get('output_tokens', 0)} tokens)")
            answered += 1
        except GeminiError as exc:
            print(f"local {model}: {type(exc).__name__} -- {exc}")
        finally:
            await client.close()
    print()
    print(f"The engineer runs locally at {settings.engineer_ollama_base_url}, "
          f"trying models in order: {', '.join(names)}.")
    return 0 if answered else 1


def _ping_antigravity(settings) -> int:
    """Whether `agy` is installed. It authenticates through the system keyring,
    so there is no key here to check and nothing to spend asking."""
    from .antigravity import AntigravityClient

    client = AntigravityClient(executable=settings.engineer_antigravity_executable)
    found = client.resolve()
    if found is None:
        print(f"antigravity: `{settings.engineer_antigravity_executable}` is not installed")
        return 0
    print(f"antigravity: {found}")
    print(f"  models: {settings.engineer_antigravity_models}, effort {settings.engineer_antigravity_effort}")
    return 1


async def _ping() -> int:
    """Every configured key against every engineer model, without waiting on limits.

    A rate-limited model is reported, not failed: it says what the engineer
    will fall back from today, which is the point of asking.
    """
    from .runs import provider_names

    settings = get_settings()
    answered = 0
    names = provider_names(settings)
    print("provider chain: " + " -> ".join(names))
    print()
    if "antigravity" in names:
        answered += _ping_antigravity(settings)
        print()
    if "ollama" in names and await _ping_ollama(settings) == 0:
        answered += 1
    if "gemini" not in names:
        return 0 if answered else 1
    keys = [("GEMINI_API_KEY_1", settings.gemini_api_key_1), ("GEMINI_API_KEY_2", settings.gemini_api_key_2)]
    for label, key in keys:
        if not key:
            print(f"{label}: not set")
            continue
        for model in engineer_models(settings):
            client = GeminiClient(keys=[key], base_url=settings.engineer_gemini_base_url, timeout=120)
            try:
                reply = await client.generate(model=model, system='Reply with the JSON {"ok": true}.',
                                              prompt="ping", max_wait=0.0)
                print(f"{label} {model}: OK ({reply.usage.get('total_tokens', 0)} tokens)")
                answered += 1
            except GeminiError as exc:
                print(f"{label} {model}: {type(exc).__name__} -- {exc}")
            finally:
                await client.close()
    uses = "keys 1 and 2" if settings.engineer_use_both_gemini_keys else "key 1 only (key 2 is Hermes's)"
    print(f"\nThe engineer uses {uses}, trying models in order: {', '.join(engineer_models(settings))}.")
    return 0 if answered else 1


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
                # The whole recorded output, not a 400-character head: the
                # output is already clipped to 1200 by GateReport.summary, and
                # the first 400 characters of verify.ps1 are the checks that
                # passed before the one that did not.
                print(f"    {check['check']}: " + check["output"].replace("\n", "\n        "))

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
    use_utf8(sys.stdout)
    use_utf8(sys.stderr)
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
