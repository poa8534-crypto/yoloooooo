"""Cache the list of real Roblox service names.

`globalTypes.None.d.luau` cannot answer this. It carries no service marker:
`Players extends Instance` and `Folder extends Instance` are structurally
identical, `Workspace extends WorldRoot`, `ServiceProvider.GetService` has no
string-literal overloads, and the only attribute in the file is
`@[deprecated]`. 594 of its 2241 declared types extend `Instance` directly and
only a minority are services.

Roblox's own API dump does tag them, which is what this reads. Refresh it
alongside `globalTypes.None.d.luau`: both describe one Roblox version, and a
service list from a different version is worse than none, because it refuses
real services and admits retired ones.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

VERSION_URL = "https://setup.rbxcdn.com/versionQTStudio"
DUMP_URL = "https://setup.rbxcdn.com/{version}-API-Dump.json"
OUT = Path(__file__).resolve().parent.parent / "data" / "roblox-services.json"
TIMEOUT = 120


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:  # noqa: S310
        return response.read()


def main() -> int:
    try:
        version = fetch(VERSION_URL).decode("utf-8").strip()
        dump = json.loads(fetch(DUMP_URL.format(version=version)))
    except (OSError, ValueError) as failure:
        print(f"could not fetch the API dump: {failure}", file=sys.stderr)
        return 1

    services = sorted(
        entry["Name"] for entry in dump.get("Classes", [])
        if "Service" in (entry.get("Tags") or [])
    )
    # A dump that parsed but yielded nothing means the shape changed. Writing
    # an empty list would silently disable the check that uses it.
    if not services:
        print("the dump parsed but tagged no services; refusing to write an "
              "empty list", file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(services, indent=1) + "\n", encoding="utf-8")
    print(f"{len(services)} services from {version} -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
