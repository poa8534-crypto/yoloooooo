"""Drive the bridge from a terminal.

    python -m app.bridge.cli status  --token TOKEN
    python -m app.bridge.cli smoke   --token TOKEN [--no-play]
    python -m app.bridge.cli result  --token TOKEN BATCH_ID
    python -m app.bridge.cli errors  --token TOKEN [--build BUILD_ID]

`smoke` sends the hard-coded integration build: a floor, a red block, a spawn,
a ModuleScript, a server Script that moves the block, and Play mode. No model is
involved, so a failure is the pipeline rather than the generation.
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

from .smoke import expected_checklist, smoke_batch

DEFAULT_URL = "http://127.0.0.1:34873"


def _client(url: str, token: str) -> httpx.Client:
    return httpx.Client(base_url=url, headers={"X-Bridge-Token": token}, timeout=30.0)


def _reachable(url: str) -> tuple[bool, str]:
    """Told apart from a wrong token on purpose: they are different problems
    with different instructions."""
    try:
        response = httpx.get(f"{url}/bridge/hello", timeout=5.0)
    except httpx.HTTPError as exc:
        return False, (f"no bridge at {url}: {exc}\n"
                       "Start it with:  python -m app.bridge.run")
    if response.status_code != 200:
        return False, f"{url} answered {response.status_code}; is something else on that port?"
    return True, response.text


def command_status(args) -> int:
    ok, detail = _reachable(args.url)
    print(detail)
    if not ok:
        return 2
    with _client(args.url, args.token) as client:
        response = client.get("/bridge/status")
        if response.status_code == 401:
            print("the bridge is running but refused that token; it prints the right one on start")
            return 2
        status = response.json()
    print(json.dumps(status, indent=2))
    if not status["plugin_connected"]:
        print("\nThe plugin has not checked in. In Studio: open the Venture Engineer panel, "
              "paste the token, press Connect.")
        return 1
    return 0


def command_smoke(args) -> int:
    ok, detail = _reachable(args.url)
    if not ok:
        print(detail)
        return 2

    batch = smoke_batch(play=not args.no_play)
    with _client(args.url, args.token) as client:
        status = client.get("/bridge/status")
        if status.status_code == 401:
            print("the bridge refused that token")
            return 2
        if not status.json()["plugin_connected"]:
            print("The Studio plugin is not connected, so nothing would apply this batch.")
            print("In Studio: open the Venture Engineer panel, paste the token, press Connect.")
            return 1
        queued = client.post("/bridge/batches", json=batch.model_dump(by_alias=True, mode="json"))
        if queued.status_code != 202:
            print(f"the bridge refused the batch: {queued.status_code} {queued.text}")
            return 1

    print(f"queued {batch.batch_id}: {len(batch.operations)} operations")
    for operation in batch.operations:
        target = getattr(operation, "path", getattr(operation, "mode", ""))
        print(f"  {operation.sequence:>3}  {operation.operation.value:<16} {target}")
    print("\nWhat you should see in Studio:")
    for line in expected_checklist():
        print(f"  - {line}")
    print(f"\nThen:  python -m app.bridge.cli result --token ... {batch.batch_id}")
    return 0


def command_result(args) -> int:
    with _client(args.url, args.token) as client:
        response = client.get(f"/bridge/results/{args.batch_id}")
    if response.status_code == 404:
        print("no result yet: the plugin has not finished, or never received it")
        return 1
    result = response.json()
    for entry in result["results"]:
        mark = {"applied": "OK  ", "skipped": "SKIP", "failed": "FAIL"}[entry["status"]]
        print(f"{mark} {entry['operation_id']}  {entry['detail']}")
    failed = [entry for entry in result["results"] if entry["status"] == "failed"]
    print(f"\nplace: {result['place_name'] or 'unknown'}")
    print(f"{len(result['results'])} operation(s), {len(failed)} failed")
    return 1 if failed else 0


def command_errors(args) -> int:
    with _client(args.url, args.token) as client:
        response = client.get("/bridge/runtime-errors", params={"build_id": args.build or ""})
    errors = response.json()["errors"]
    if not errors:
        print("no runtime errors reported")
        return 0
    for error in errors:
        where = f"{error['script_path']}:{error['line']}" if error["script_path"] else ""
        print(f"[{error['severity']}] {where} {error['message'][:400]}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bridge", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--token", default="", help="the pairing token the bridge printed")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--no-play", action="store_true",
                       help="build it but do not start a test session")
    result = commands.add_parser("result")
    result.add_argument("batch_id")
    errors = commands.add_parser("errors")
    errors.add_argument("--build", default="")

    args = parser.parse_args(argv)
    if not args.token:
        print("--token is required; the bridge prints it when it starts")
        return 2
    return {"status": command_status, "smoke": command_smoke,
            "result": command_result, "errors": command_errors}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
