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

from . import subprocesses
from .luau_guard import check_project
from .workspace import CLIENT_SERVICES_PATH, SERVICES_PATH, WRITABLE_ROOTS, spec_path

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
        # The checks start rojo, selene, luau-lsp, StyLua and PowerShell, and a
        # timed-out check used to leave whatever they had started running.
        completed = subprocesses.run(resolve_tool(args), cwd=str(cwd),
                                     timeout=timeout, env=rokit_environment())
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
                 "output": _clip_around(check.output, 1200)} for check in self.checks]


_FAILING = re.compile(r"\bFAIL(ED)?\b|\berror\b|TypeError", re.IGNORECASE)


def _clip_around(text: str, limit: int) -> str:
    """A stored record that keeps the lines it exists to explain.

    Two things were learned the hard way here. A report's verdict is at the
    bottom, so keeping only the head stores a list of PASSes -- and the
    behaviour runner prints its specs in alphabetical order, so a failing
    case sits wherever the alphabet puts it, which a head-and-tail clip drops
    just as thoroughly. PlayerLifecycleManager's two failures were 16,000
    characters inside a report whose head and tail were both PASSes.

    So the failing lines are kept first, and the head and tail fill whatever
    room is left.
    """
    if len(text) <= limit:
        return text
    failing = [line.strip() for line in text.splitlines() if _FAILING.search(line)]
    kept = "\n".join(failing[:30])[: limit * 2 // 3]
    room = max(limit - len(kept), limit // 3)
    head, tail = room // 2, room - room // 2
    omitted = max(len(text) - head - tail, 0)
    return (f"{text[:head]}\n... [{omitted} characters omitted; the failing lines follow]\n"
            f"{kept}\n...\n{text[-tail:]}")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... [{len(text) - limit} more characters]"


VERIFY_SCRIPT = "scripts/verify.ps1"


def _sources_for(root: Path, system: str) -> list[str]:
    """Where this system's source could be, as a spec would name it.

    Measured from the tree rather than assumed, so a system in src/client is
    not asked to name a path under src/server.
    """
    found = [path.relative_to(root).as_posix()
             for source_root in WRITABLE_ROOTS
             for path in sorted((root / source_root).glob(f"{system}.luau"))]
    return found or [f"{source_root}/{system}.luau" for source_root in WRITABLE_ROOTS]


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

    def run(self, root: Path, system: str | None = None) -> GateReport:
        """`system` is the system this attempt is building, when there is one.

        Given, the gate also requires that system's behaviour spec. The
        preflight run passes nothing: it judges the untouched project, which
        cannot be expected to hold a spec for a system not yet written.
        """
        checks = [self._behaviour_spec(root, system)] if system else []
        if self.mode == "verify-script":
            return GateReport((self._guard(root), self._verify_script(root), *checks))
        report = self._builtin(root)
        return GateReport((*report.checks, *checks))

    def _behaviour_spec(self, root: Path, system: str) -> CheckResult:
        """The system must come with a spec that executes it.

        Every other check reads the code. This one insists something runs it:
        `verify.ps1` runs whatever specs exist, so a system delivered without
        one passes the behaviour check by contributing nothing to it. Twelve
        systems reached master that way before this existed.

        Existence is not enough -- a spec that never loads the module it names
        is the same silence with a file around it -- so the spec must mention
        the source file it covers.
        """
        name = f"behaviour spec ({system})"
        path = root / spec_path(system)
        if not path.is_file():
            return CheckResult(name, False,
                               f"{spec_path(system)} is missing. Every system ships with a behaviour "
                               f"spec that loads {_sources_for(root, system)[0]} through the harness "
                               f"and checks its acceptance criteria; see tests/ for the specs already "
                               f"in the project.")
        text = path.read_text(encoding="utf-8", errors="replace")
        covered = [source for source in _sources_for(root, system) if source in text]
        if not covered:
            wanted = " or ".join(_sources_for(root, system)) or f"src/**/{system}.luau"
            return CheckResult(name, False,
                               f"{spec_path(system)} never loads {wanted}. A spec that does not run the "
                               f"module it names proves nothing; load it with harness.load(...).")
        return CheckResult(name, True, f"{spec_path(system)} loads {covered[0]}")

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
