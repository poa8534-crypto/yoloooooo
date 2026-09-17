"""Package the plugin as a model file and put it where Studio looks.

    python scripts/install_studio_plugin.py [--dry-run]

Studio's local Plugins folder definitely loads `.rbxm` / `.rbxmx` model files --
that is how Rojo ships -- and whether it loads a loose `.luau` script varies.
So the plugin is packaged as `.rbxmx`, which is XML and can be written without
a Roblox toolchain.

The source is XML-escaped rather than wrapped in CDATA: Luau block comments end
with `]]`, and one `]]>` anywhere in the file would end a CDATA section early
and produce a plugin that is silently truncated.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from xml.sax.saxutils import escape

SOURCE = Path(__file__).resolve().parent.parent / "plugin" / "VentureEngineer.server.luau"
PLUGIN_NAME = "VentureEngineer"


def plugins_folder() -> Path:
    """Where Studio looks on this machine."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Roblox" / "Plugins"
    # macOS, for completeness; untested from here.
    return Path.home() / "Documents" / "Roblox" / "Plugins"


def as_rbxmx(source: str, name: str = PLUGIN_NAME) -> str:
    """One Script instance carrying the plugin's source."""
    return (
        '<roblox version="4">\n'
        '\t<Item class="Script" referent="RBX0">\n'
        "\t\t<Properties>\n"
        f"\t\t\t<string name=\"Name\">{escape(name)}</string>\n"
        f"\t\t\t<ProtectedString name=\"Source\">{escape(source)}</ProtectedString>\n"
        "\t\t\t<bool name=\"Disabled\">false</bool>\n"
        "\t\t</Properties>\n"
        "\t</Item>\n"
        "</roblox>\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not SOURCE.is_file():
        print(f"no plugin source at {SOURCE}")
        return 2
    folder = plugins_folder()
    target = folder / f"{PLUGIN_NAME}.rbxmx"
    packaged = as_rbxmx(SOURCE.read_text(encoding="utf-8"))

    print(f"source : {SOURCE} ({len(SOURCE.read_text(encoding='utf-8'))} characters)")
    print(f"target : {target}")
    if args.dry_run:
        print("dry run; nothing written")
        return 0

    folder.mkdir(parents=True, exist_ok=True)
    target.write_text(packaged, encoding="utf-8", newline="\n")

    # A loose .luau beside it would load as a SECOND plugin on any Studio that
    # does read them, and two plugins creating one toolbar name is a conflict
    # that looks like the plugin being broken.
    for stale in (folder / f"{PLUGIN_NAME}.luau", folder / f"{PLUGIN_NAME}.lua"):
        if stale.is_file():
            stale.unlink()
            print(f"removed : {stale}")

    print(f"written : {target.stat().st_size} bytes")
    print("\nRestart Roblox Studio. The Plugins tab should then show 'Venture Engineer'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
