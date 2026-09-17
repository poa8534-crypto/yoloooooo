"""The checks generated Luau must pass before it is accepted.

The definition of acceptable is the game repository's `scripts/verify.ps1`
(docs/GATING.md): one script, so the engineer, Hermes and a person can never
be holding different standards. By default this runs that script, in the run's
worktree, and its exit code is the verdict. Its output is the feedback.

The guard (app/engineer/luau_guard.py) also runs in-process, first. It is the
same code verify.ps1 calls, so it is not a second standard; it reports each
violation with a file and line, and it still runs if verify.ps1 cannot reach
the agents checkout from a worktree.

"builtin" mode runs the same checks command by command:

    guard      `game`, `script` and the other routes luau-lsp does not type-check
    sourcemap  rojo sourcemap -- the instance tree luau-lsp resolves against
    selene     lint against the Roblox standard library
    stylua     formatting, as configured in the game repository
    luau-lsp   type checking with --platform=roblox and the None definitions

It exists for machines without verify.ps1, and for tests. Every check runs even
when an earlier one fails, so one attempt learns about every problem at once;
the exception is luau-lsp, which cannot run without a sourcemap. A zero exit
whose output still reports a diagnostic fails: a warning in generated code is a
question nobody is going to come back and answer.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .luau_guard import check_project
from .workspace import CLIENT_SERVICES_PATH, SERVICES_PATH

OUTPUT_LIMIT = 6000
_SELENE_COUNTS = re.compile(r"^\s*(\d+)\s+(?:errors?|warnings?|parse errors?)\s*$", re.MULTILINE | re.IGNORECASE)
_LUAU_DIAGNOSTIC = re.compile(r"\(\d+,\d+\):\s*\w+")

CommandRunner = Callable[[list[str], Path, float], tuple[int | None, str]]


def rokit_environment() -> dict[str, str]:
    """The environment with Rokit's shims on PATH.

    The toolchain is pinned by Rokit and reached through shims in
    `%USERPROFILE%\\.rokit\\bin`, which a fresh terminal has but this process
    does not: Rokit appends it to the user PATH at install time, so anything
    started from an older shell never sees it.

    `verify.ps1` sets its own PATH, which is why the checks passed while
    `Gate.format` silently did nothing -- it invokes `stylua` directly. Six
    attempts of a real run were then failed by a formatter that had never run.
    """
    environment = dict(os.environ)
    shims = Path.home() / ".rokit" / "bin"
    if shims.is_dir():
        current = environment.get("PATH", "")
        if str(shims).lower() not in current.lower():
            environment["PATH"] = f"{shims}{os.pathsep}{current}"
    return environment


def resolve_tool(args: list[str]) -> list[str]:
    """`args` with the executable resolved against Rokit's shims.

    Passing an environment with a wider PATH is not enough on Windows:
    CreateProcess resolves the executable against the *parent* process's PATH,
    so the child's PATH only affects what the child itself launches. The
    command has to be resolved here, before the call.
    """
    if not args:
        return args
    environment = rokit_environment()
    found = shutil.which(args[0], path=environment.get("PATH"))
    return [found, *args[1:]] if found else args


def run_command(args: list[str], cwd: Path, timeout: float) -> tuple[int | None, str]:
    """(exit code, combined output). A missing tool is exit None, not a crash."""
    try:
        completed = subprocess.run(resolve_tool(args), cwd=cwd, capture_output=True,
                                   timeout=timeout, check=False, env=rokit_environment())
    except FileNotFoundError:
        return None, f"`{args[0]}` was not found on PATH (expected via Rokit in %USERPROFILE%\\.rokit\\bin)"
    except subprocess.TimeoutExpired:
        return None, f"`{args[0]}` did not finish within {timeout:.0f}s"
    output = (completed.stdout + b"\n" + completed.stderr).decode("utf-8", "replace").strip()
    return completed.returncode, output


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    output: str
    exit_code: int | None = None


@dataclass(frozen=True)
class GateReport:
    checks: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)

    @property
    def failed(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.passed]

    def feedback(self, limit: int = OUTPUT_LIMIT) -> str:
        """The failures, verbatim, for the next attempt to fix."""
        sections = [f"## {check.name} FAILED\n{_clip(check.output, limit)}" for check in self.failed]
        return "\n\n".join(sections)

    def fingerprint(self) -> str:
        """Identity of this failure, ignoring positions that shift between attempts."""
        text = "|".join(f"{check.name}:{check.output}" for check in self.failed)
        return re.sub(r"\d+", "#", text)

    def summary(self) -> list[dict]:
        return [{"check": check.name, "passed": check.passed, "exit_code": check.exit_code,
                 "output": _clip(check.output, 1200)} for check in self.checks]


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... [{len(text) - limit} more characters]"


VERIFY_SCRIPT = "scripts/verify.ps1"


class Gate:
    def __init__(self, known_services: frozenset[str], definitions: Path, *,
                 runner: CommandRunner = run_command, timeout: float = 300.0,
                 mode: str = "verify-script"):
        if mode not in ("verify-script", "builtin"):
            raise ValueError(f"unknown gate mode {mode!r}")
        self.known_services = known_services
        self.definitions = definitions
        self.runner = runner
        self.timeout = timeout
        self.mode = mode

    def format(self, root: Path, paths: list[str]) -> str | None:
        """Formatting is deterministic, so StyLua does it rather than a model attempt.

        A parse error leaves the file unformatted; the checks then report it.

        Returns why formatting did not happen, or None when it did. The result
        used to be discarded, so StyLua missing from PATH looked exactly like
        StyLua succeeding, and the formatting check then failed attempt after
        attempt on files the formatter had never touched.
        """
        if not paths:
            return None
        code, output = self.runner(["stylua", *paths], root, self.timeout)
        if code == 0:
            return None
        return output.strip() or f"stylua exited {code}"

    def run(self, root: Path) -> GateReport:
        if self.mode == "verify-script":
            return GateReport((self._guard(root), self._verify_script(root)))
        return self._builtin(root)

    def _verify_script(self, root: Path) -> CheckResult:
        script = root / VERIFY_SCRIPT
        if not script.is_file():
            # Missing is a failure, never a skip: a check that quietly
            # disappears is worse than one that fails loudly.
            return CheckResult("verify.ps1", False,
                               f"{VERIFY_SCRIPT} not found in {root}; it defines what acceptable means")
        return self._command("verify.ps1", ["powershell", "-NoProfile", "-NonInteractive",
                                            "-ExecutionPolicy", "Bypass", "-File", str(script)], root)

    def _builtin(self, root: Path) -> GateReport:
        checks = [self._guard(root)]
        sourcemap = self._command("sourcemap", ["rojo", "sourcemap", "default.project.json", "-o", "sourcemap.json"], root)
        checks.append(sourcemap)
        checks.append(self._command("selene", ["selene", "src"], root, counts=_SELENE_COUNTS))
        checks.append(self._command("stylua", ["stylua", "--check", "src"], root))
        if not self.definitions.is_file():
            checks.append(CheckResult("luau-lsp", False, f"Roblox definitions not found at {self.definitions}"))
        elif sourcemap.passed:
            checks.append(self._command("luau-lsp", [
                "luau-lsp", "analyze", "--platform=roblox", f"--definitions={self.definitions}",
                "--sourcemap=sourcemap.json", "src"], root, diagnostic=_LUAU_DIAGNOSTIC))
        else:
            checks.append(CheckResult("luau-lsp", False, "not run: the sourcemap could not be generated"))
        return GateReport(tuple(checks))

    def _guard(self, root: Path) -> CheckResult:
        source_root = root / "src"
        if not source_root.is_dir():
            return CheckResult("guard", False, f"no src directory in {root}")
        violations = check_project(source_root,
                                   (root / SERVICES_PATH, root / CLIENT_SERVICES_PATH),
                                   self.known_services)
        # check_project reports paths relative to src/; report them from the project root.
        return CheckResult("guard", not violations, "\n".join(f"src/{violation}" for violation in violations))

    def _command(self, name: str, args: list[str], root: Path, *,
                 counts: re.Pattern | None = None, diagnostic: re.Pattern | None = None) -> CheckResult:
        exit_code, output = self.runner(args, root, self.timeout)
        passed = exit_code == 0
        if passed and counts is not None and any(int(n) for n in counts.findall(output)):
            passed = False
        if passed and diagnostic is not None and diagnostic.search(output):
            passed = False
        return CheckResult(name, passed, output, exit_code)
