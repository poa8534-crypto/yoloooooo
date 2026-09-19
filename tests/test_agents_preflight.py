r"""What start-agents.bat reads before it starts anything.

This lives in Python rather than as one-liners inside the batch file because
cmd's `for /f` with backticks mangles a nested `python -c "..."` beyond
rescue. The first version printed

    '.venv\Scripts\python.exe" -c "from' is not recognized

and then carried on with an empty project directory, which is worse than not
starting: a startup script that misreads its own configuration and proceeds
will start the wrong thing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "agents_preflight.py"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, timeout=120, check=False)


def test_config_prints_every_key_the_batch_file_reads():
    """The batch file sets these as variables and uses all four. A missing one
    becomes an empty variable, and an empty variable in a path is how you start
    a server against the wrong directory."""
    result = run("config")

    keys = {line.split("=", 1)[0] for line in result.stdout.splitlines() if "=" in line}
    assert {"GAME_DIR", "DASH_HOST", "DASH_PORT", "DASH_URL"} <= keys


def test_config_is_one_key_per_line_with_no_stray_output():
    """It is parsed with `for /f delims==`, so anything else on stdout becomes
    a variable named after a sentence."""
    result = run("config")

    for line in result.stdout.splitlines():
        assert "=" in line, f"not a KEY=VALUE line: {line!r}"
        assert not line.startswith(" "), f"leading space would end up in the name: {line!r}"


def test_a_directory_that_is_not_a_repository_fails_rather_than_reporting_tools(tmp_path):
    """`rokit install` is useless advice when the real problem is that the path
    is not a project at all, so it says which it is."""
    result = run("tools", str(tmp_path))

    assert result.returncode == 1
    assert "not a git repository" in result.stdout


def test_an_unknown_mode_does_not_look_like_success():
    result = run("wat")

    assert result.returncode == 2
