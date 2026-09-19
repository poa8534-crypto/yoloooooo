"""What start-agents.bat needs to know before it starts anything.

    python scripts/agents_preflight.py config [GAME_DIR]   KEY=VALUE lines
    python scripts/agents_preflight.py tools  [GAME_DIR]   the toolchain, exit 1 if short

Both live here rather than as one-liners inside the batch file, because cmd's
`for /f` with backticks mangles a nested `python -c "..."` beyond rescue -- the
first version printed

    '.venv\\Scripts\\python.exe" -c "from' is not recognized

and then carried on with an empty project directory. A batch file that starts
the wrong thing after misreading its own configuration is worse than one that
does not start.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def settings_for(game_dir: str):
    """Settings, with GAME_DIR overriding .env when one was passed."""
    from app.config import get_settings

    if game_dir:
        os.environ["GAME_PROJECT_DIR"] = game_dir
    get_settings.cache_clear()
    return get_settings()


def config(game_dir: str) -> int:
    settings = settings_for(game_dir)
    print(f"GAME_DIR={settings.game_project_dir}")
    print(f"DASH_HOST={settings.host}")
    print(f"DASH_PORT={settings.port}")
    print(f"DASH_URL=http://{settings.host}:{settings.port}/api/health")
    return 0


def tools(game_dir: str) -> int:
    from app.engineer.runs import game_repo
    from app.engineer.toolchain import inspect

    settings = settings_for(game_dir)
    try:
        repo = game_repo(settings)
    except Exception as exc:  # noqa: BLE001 - reported, this is the report
        print(f"   [X]  game repository: {exc}")
        return 1

    report = inspect(repo=repo, definitions=repo / settings.luau_definitions_file)
    for tool in report.tools:
        mark = "[ok]" if tool.present else "[X] "
        print(f"   {mark} {tool.name}: {tool.version or tool.detail}")
    return 0 if report.ready else 1


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    game_dir = sys.argv[2] if len(sys.argv) > 2 else ""
    if mode == "config":
        return config(game_dir)
    if mode == "tools":
        return tools(game_dir)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
