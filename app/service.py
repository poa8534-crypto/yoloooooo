from __future__ import annotations

import sys
import traceback
from datetime import UTC, datetime

from .config import ROOT
from .main import run


def main() -> None:
    """Run the local server with durable logs for a windowless scheduled task."""
    log_directory = ROOT / "data"
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / "service.log"

    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        sys.stdout = log
        sys.stderr = log
        print(f"[{datetime.now(UTC).isoformat()}] Starting Roblox Venture Agents")
        try:
            run()
        except BaseException:
            traceback.print_exc()
            raise


if __name__ == "__main__":
    main()
