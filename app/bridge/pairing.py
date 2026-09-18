"""The pairing token, kept across restarts.

It used to be generated fresh every time the bridge started, which meant every
restart silently unpaired Roblox Studio: the plugin still held the previous
token, sent it, and got a 401 that surfaces in the widget as
`HttpError: ConnectFail`. Nothing in either process is wrong at that point, and
nothing says so -- the only fix is to go and re-paste a token, and you have to
already know that.

A restart is not a new trust relationship. The token therefore lives in a file
under `data/`, which is gitignored, and is reused until someone deletes it.

Generated with `secrets.token_urlsafe(24)` as before, and the file is created
with owner-only permissions where the platform honours them. It is a local
credential for a loopback service, so the threat it defends against is another
user of the same machine, not the network.
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

TOKEN_BYTES = 24
FILENAME = "bridge_token.txt"


def token_file(root: Path) -> Path:
    return root / "data" / FILENAME


def read_token(root: Path) -> str:
    """The stored token, or "" if there is not a usable one."""
    file = token_file(root)
    try:
        stored = file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    # A truncated or hand-edited file is not a credential. Better to make a new
    # one than to run with something short enough to guess.
    return stored if len(stored) >= 16 else ""


def write_token(root: Path, token: str) -> Path:
    file = token_file(root)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(token + "\n", encoding="utf-8")
    try:
        file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows without the POSIX bits: the file is still under the user's
        # own profile, which is the protection that actually applies there.
        pass
    return file


def pairing_token(root: Path) -> tuple[str, bool]:
    """(token, whether it is new).

    `VENTURE_BRIDGE_TOKEN` overrides the file, for a caller that already has a
    token it wants the bridge to accept -- reconnecting a plugin that is
    already paired, above all.
    """
    from_environment = os.environ.get("VENTURE_BRIDGE_TOKEN", "").strip()
    if len(from_environment) >= 16:
        return from_environment, False

    stored = read_token(root)
    if stored:
        return stored, False

    fresh = secrets.token_urlsafe(TOKEN_BYTES)
    write_token(root, fresh)
    return fresh, True
