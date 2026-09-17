"""Which Roblox classes are services: the cached list the gate also reads.

The list is written by `scripts/refresh_roblox_services.py` from Roblox's API
dump, and read by `scripts/guard_check.py` inside the game repo's verify.ps1.
The engineer reads the same file, so the loop and the gate cannot disagree
about what a service is. See docs/GATING.md.

The gate carries on with a warning when the list is missing. The engineer does
not: without it, an invented service name in a generated Services module could
not be refused, and generating that module is the engineer's job.
"""

from __future__ import annotations

import json
from pathlib import Path

# A real dump tags a couple of hundred services (212 when this was written).
# Far fewer means a truncated or wrong list, which would refuse real services.
MINIMUM_SERVICES = 100
REFRESH_HINT = "Refresh it with `python scripts/refresh_roblox_services.py`."


class CatalogMissing(RuntimeError):
    pass


def load_services(path: Path) -> frozenset[str]:
    try:
        names = json.loads(path.read_bytes())
    except FileNotFoundError:
        raise CatalogMissing(f"No Roblox service list at {path}. {REFRESH_HINT}") from None
    except ValueError:
        raise CatalogMissing(f"The Roblox service list at {path} is not valid JSON. {REFRESH_HINT}") from None
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise CatalogMissing(f"The Roblox service list at {path} is not a list of names. {REFRESH_HINT}")
    if len(names) < MINIMUM_SERVICES:
        raise CatalogMissing(f"The Roblox service list at {path} has only {len(names)} names. {REFRESH_HINT}")
    return frozenset(names)
