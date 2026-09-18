"""Start the bridge on this machine.

    python -m app.bridge.run

It binds loopback only. A bridge reachable from the network is a way to write
scripts into someone else's Studio, and no configuration flag makes that a good
idea, so there is not one.
"""

from __future__ import annotations

import uvicorn

from pathlib import Path

from .pairing import pairing_token, token_file
from .service import BridgeState, create_bridge

ROOT = Path(__file__).resolve().parent.parent.parent
HOST = "127.0.0.1"
PORT = 34873  # one above Rojo's 34872, so both can run together


def main() -> int:
    # Kept across restarts. A new token every start silently unpaired Studio:
    # the plugin went on sending the previous one and got a 401, which the
    # widget shows as "HttpError: ConnectFail" with nothing saying why.
    token, is_new = pairing_token(ROOT)
    state = BridgeState(token=token)
    app = create_bridge(state)
    print("Venture Engineer bridge")
    print(f"  http://{HOST}:{PORT}")
    print()
    if is_new:
        print("  Pairing token (paste this into the Studio plugin):")
        print(f"      {state.token}")
        print()
        print(f"  Kept in {token_file(ROOT)}, so a restart will not unpair Studio.")
    else:
        print("  Paired already: reusing the stored token, so Studio stays connected.")
        print(f"  If you need to see it:  type {token_file(ROOT)}")
    print()
    print("  Keep this window open. Loopback only; nothing outside this machine can reach it.")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
