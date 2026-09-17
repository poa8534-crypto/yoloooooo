"""Can this machine actually run a build?

A build is driven from a browser that may be on another device entirely, so
the person pressing the button is not the person who can see whether `agy` is
installed. Without this, a missing tool is discovered eight minutes in, by a
run that has already spent an attempt on every system before it.

Everything here resolves tools exactly the way the gate resolves them --
`resolve_tool` and `rokit_environment`, not a second opinion. That matters more
than it sounds: Rokit appends its shims to the user PATH at install time and
Windows resolves an executable against the PARENT process's PATH, so a service
started before the install finds nothing while a fresh terminal finds
everything. A readiness check that used plain `shutil.which` would report a
toolchain the build cannot actually reach, which is worse than not checking.

The reports are deliberately shaped around "what would I do about it": a tool
is present or not, at a path, with a version, and if it is missing the detail
says how to get it.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .gate import resolve_tool, rokit_environment

# Every tool the six-check gate shells out to, with the flag that makes it
# say what it is. Rokit pins all four in the game repository's rokit.toml.
GATE_TOOLS: tuple[tuple[str, str], ...] = (
    ("rojo", "the sourcemap luau-lsp resolves instance paths against"),
    ("selene", "lint against the Roblox standard library"),
    ("stylua", "formatting, as configured in the game repository"),
    ("luau-lsp", "type checking with --platform=roblox"),
)
ROKIT_HINT = ("install it with Rokit in the game repository: `rokit install`, "
              "then restart whatever runs the service so it inherits the new PATH")
VERSION_TIMEOUT = 8.0
# Rokit's shims colour their errors, and the codes travel through JSON into the
# browser as unreadable escapes.
ANSI = re.compile(r"\[[0-9;]*m")


@dataclass
class ToolReport:
    name: str
    present: bool
    path: str = ""
    version: str = ""
    detail: str = ""
    purpose: str = ""
    required: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ToolchainReport:
    tools: list[ToolReport] = field(default_factory=list)

    @property
    def missing(self) -> list[str]:
        return [tool.name for tool in self.tools if tool.required and not tool.present]

    @property
    def ready(self) -> bool:
        return not self.missing

    def as_dict(self) -> dict:
        return {
            "ready": self.ready,
            "missing": self.missing,
            "tools": [tool.as_dict() for tool in self.tools],
        }


def _version(args: list[str], cwd: Path | None = None) -> tuple[bool, str, str]:
    """(answered, first line of output, why not).

    `cwd` is not optional in practice for the gate's tools. Rokit's shims read
    the pinned version out of the project's `rokit.toml`, so running one from
    anywhere else answers "Failed to find tool 'rojo'" -- which looks exactly
    like a broken install and is not one. The gate runs them inside the game
    repository; so does this.

    A tool that is on disk but cannot answer `--version` is reported as
    present with the reason it stayed quiet, rather than as missing: the two
    have different fixes, and calling a hung tool "not installed" sends the
    person to reinstall something that is already there.
    """
    try:
        completed = subprocess.run(resolve_tool(args), capture_output=True,
                                   timeout=VERSION_TIMEOUT, check=False,
                                   cwd=cwd if cwd and cwd.is_dir() else None,
                                   env=rokit_environment())
    except FileNotFoundError:
        return False, "", "not found on PATH"
    except subprocess.TimeoutExpired:
        return False, "", f"did not answer --version within {VERSION_TIMEOUT:.0f}s"
    except OSError as exc:  # noqa: BLE001 - reported, never raised at a caller
        return False, "", str(exc)[:200]
    text = ANSI.sub("", (completed.stdout + b"\n" + completed.stderr)
                    .decode("utf-8", "replace")).strip()
    first = text.splitlines()[0][:160] if text else ""
    # A non-zero exit is the shim saying it could not find the pinned tool.
    # Treating that as an answer would report a version string that is really
    # an error message, and mark the tool ready.
    if completed.returncode != 0:
        return False, "", first or f"`{args[0]} --version` exited {completed.returncode}"
    return True, first, ""


def check_gate_tool(name: str, purpose: str, repo: Path) -> ToolReport:
    """Present means the gate could run it, so the shim has to answer.

    A Rokit shim on PATH proves nothing on its own: it is a stub that looks up
    the pinned version and can fail to find it. Reporting that as installed
    would be the readiness check passing while the build fails.
    """
    resolved = resolve_tool([name, "--version"])
    found = resolved[0] != name  # resolve_tool returns the input unchanged when it fails
    if not found:
        return ToolReport(name=name, present=False, purpose=purpose,
                          detail=f"not found on PATH. {ROKIT_HINT}")
    answered, version, why = _version([name, "--version"], cwd=repo)
    return ToolReport(name=name, present=answered, path=resolved[0], version=version,
                      purpose=purpose,
                      detail="" if answered else f"{why}. {ROKIT_HINT}")


def check_agy(client=None) -> ToolReport:
    """The Antigravity CLI, resolved the way the client resolves it.

    Its own `resolve` knows about `%LOCALAPPDATA%\\agy\\bin`, which the
    installer adds to the registry PATH where a running process never sees it.
    Asking `shutil.which` here would report it missing on exactly the machine
    where it works.
    """
    from .antigravity import AntigravityClient

    client = client or AntigravityClient()
    found = client.resolve()
    if not found:
        return ToolReport(
            name="agy", present=False, purpose="the primary model provider",
            detail="not found. Install the Antigravity CLI and restart whatever runs "
                   "the service, so it inherits the new PATH.")
    answered, version, why = _version([found, "--version"])
    return ToolReport(name="agy", present=True, path=found, version=version,
                      purpose="the primary model provider",
                      detail="" if answered else why)


def check_repo(repo: Path) -> ToolReport:
    """The game repository the Engineer writes into."""
    if not repo.exists():
        return ToolReport(name="game repository", present=False, purpose="where systems are written",
                          detail=f"{repo} does not exist")
    if not (repo / ".git").exists():
        return ToolReport(name="game repository", present=False, purpose="where systems are written",
                          detail=f"{repo} is not a git repository, so no branch can be made for a system")
    if not (repo / "default.project.json").is_file():
        return ToolReport(name="game repository", present=False, purpose="where systems are written",
                          detail=f"{repo} has no default.project.json, so rojo cannot build a sourcemap")
    branch = ""
    try:
        completed = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo,
                                   capture_output=True, timeout=VERSION_TIMEOUT, check=False)
        branch = completed.stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.SubprocessError):
        branch = ""
    return ToolReport(name="game repository", present=True, path=str(repo),
                      version=branch, purpose="where systems are written")


def check_definitions(definitions: Path) -> ToolReport:
    """Roblox's type definitions. luau-lsp runs without them and finds nothing."""
    present = definitions.is_file()
    return ToolReport(
        name="Roblox definitions", present=present,
        path=str(definitions) if present else "",
        purpose="what luau-lsp type-checks against",
        detail="" if present else
               f"not found at {definitions}. Without it the type check cannot run, "
               "and every system would be accepted unchecked.")


def inspect(*, repo: Path, definitions: Path, client=None) -> ToolchainReport:
    """Every tool a build needs, on the machine that would run it."""
    tools = [check_agy(client)]
    tools.extend(check_gate_tool(name, purpose, repo) for name, purpose in GATE_TOOLS)
    tools.append(check_definitions(definitions))
    tools.append(check_repo(repo))
    return ToolchainReport(tools=tools)
