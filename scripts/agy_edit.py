"""Hand an editing task to Antigravity the way the pipeline does: no tools,
everything in the prompt, and exact search/replace edits back, applied here.

    python agy_edit.py --repo DIR --task FILE --files a.luau b.luau ... --out RESULT.json
                       [--feedback FILE] [--models m1,m2]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\tcgxu\OneDrive\Desktop\roblox-venture-agents")
from app.engineer.antigravity import AntigravityClient  # noqa: E402

FORMAT = """Answer with ONE JSON object and nothing else:
{"edits": [{"file": "<path relative to the repo>", "search": "<exact text currently in the file>", "replace": "<new text>"}], "notes": "<one short paragraph: what you changed and why>"}

Rules for edits:
- `search` must be copied EXACTLY from the file as given below (same tabs, spaces and line breaks) and must occur exactly ONCE in that file. Include enough surrounding lines to make it unique. Keep each search small (a few lines).
- To create a new file, use "search": "" and put the whole file in "replace".
- Edits to one file are applied in order, each to the result of the previous one.
- Luau is --!strict: every new function has typed parameters and return types; no `any` where a type is known.
- Never use the global `game` in src/ (require the Services module as the file already does). Never name a variable `shared`.
- Match the surrounding code: tabs for indentation, comments in plain full sentences, GameRules holds pure rules, GameConfig holds numbers, services only wire events.
"""

NO_TOOLS = ("You have no working directory and no tools. Do not call any tool, do not run any command, and do "
            "not read or write any file. Everything you need is in this message. Answer immediately with the "
            "JSON object and nothing else.")


def build_prompt(repo: Path, task: str, files: list[str], feedback: str | None) -> str:
    parts = [NO_TOOLS, FORMAT, "# The task\n\n" + task]
    if feedback:
        parts.append("# Feedback on your previous answer (fix these)\n\n" + feedback)
    parts.append("# The files, as they are now")
    for name in files:
        path = repo / name
        body = path.read_text(encoding="utf-8") if path.exists() else "(this file does not exist yet)"
        parts.append(f"=== FILE: {name} ===\n{body}\n=== END FILE: {name} ===")
    return "\n\n".join(parts)


def parse(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    # strict=False: models sometimes put a raw tab or newline inside a string
    # (Luau source is full of tabs); the edit is still exactly what they meant.
    return json.loads(text[start:end + 1], strict=False)


def apply(repo: Path, edits: list[dict]) -> tuple[list[str], list[str]]:
    applied, failed = [], []
    for index, edit in enumerate(edits):
        name, search, replace = edit.get("file", ""), edit.get("search", ""), edit.get("replace", "")
        path = repo / name
        if not name or ".." in Path(name).parts:
            failed.append(f"edit {index}: bad file name {name!r}")
            continue
        if search == "":
            if path.exists():
                failed.append(f"edit {index}: {name} already exists; use a search/replace edit")
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(replace, encoding="utf-8", newline="")
            applied.append(f"created {name}")
            continue
        if not path.exists():
            failed.append(f"edit {index}: {name} does not exist")
            continue
        body = path.read_text(encoding="utf-8")
        count = body.count(search)
        if count != 1:
            failed.append(f"edit {index}: in {name} the search text occurs {count} times (must be exactly once):\n{search[:400]}")
            continue
        path.write_text(body.replace(search, replace), encoding="utf-8", newline="")
        applied.append(f"edited {name}")
    return applied, failed


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--files", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--feedback")
    parser.add_argument("--models", default="gemini-3.8-flash-high,gemini-3.8-flash-medium")
    args = parser.parse_args()

    repo = Path(args.repo)
    task = Path(args.task).read_text(encoding="utf-8")
    feedback = Path(args.feedback).read_text(encoding="utf-8") if args.feedback else None
    prompt = build_prompt(repo, task, args.files, feedback)
    client = AntigravityClient(timeout=1500.0)
    errors = []
    reply = None
    for model in args.models.split(","):
        try:
            reply = await client.generate(model=model, system="", prompt=prompt, json_output=True)
            break
        except Exception as exc:  # noqa: BLE001 -- try the next model, keep the reason
            errors.append(f"{model}: {exc}")
    result: dict = {"prompt_chars": len(prompt), "errors": errors}
    if reply is None:
        Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return
    result["model"] = reply.model
    result["usage"] = reply.usage
    try:
        answer = parse(reply.text)
    except (json.JSONDecodeError, ValueError) as exc:
        result["parse_error"] = str(exc)
        result["raw"] = reply.text[:4000]
        # The whole reply, so a long answer that fails to parse is not lost.
        raw_path = Path(args.out).with_suffix(".raw.txt")
        raw_path.write_text(reply.text, encoding="utf-8")
        result["raw_file"] = str(raw_path)
        Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2)[:3000])
        return
    applied, failed = apply(repo, answer.get("edits", []))
    result.update(notes=answer.get("notes", ""), applied=applied, failed=failed)
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2)[:6000])


if __name__ == "__main__":
    asyncio.run(main())
