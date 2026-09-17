"""Start the bridge on this machine.

    python -m app.bridge.run

It binds loopback only. A bridge reachable from the network is a way to write
scripts into someone else's Studio, and no configuration flag makes that a good
idea, so there is not one.
"""

from __future__ import annotations

import uvicorn

from .service import BridgeState, create_bridge

HOST = "127.0.0.1"
PORT = 34873  # one above Rojo's 34872, so both can run together


def main() -> int:
    state = BridgeState()
    app = create_bridge(state)
    print("Venture Engineer bridge")
    print(f"  http://{HOST}:{PORT}")
    print()
    print("  Pairing token (paste this into the Studio plugin):")
    print(f"      {state.token}")
    print()
    print("  Keep this window open. Loopback only; nothing outside this machine can reach it.")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
