"""The gate fails closed: a missing script, a missing tool or a quiet warning never passes."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.engineer.gate import Gate
from app.engineer.luau_guard import render_services_module

KNOWN = frozenset({"Players", "ReplicatedStorage"})


class Runner:
    """Answers each tool with a scripted (exit code, output)."""

    def __init__(self, **answers):
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, args, cwd, timeout):
        self.calls.append(args)
        name = "sourcemap" if args[:2] == ["rojo", "sourcemap"] else args[0]
        return self.answers.get(name, (0, ""))


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src" / "shared").mkdir(parents=True)
    (tmp_path / "src" / "shared" / "Services.luau").write_bytes(render_services_module(["Players"]).encode())
    (tmp_path / "src" / "shared" / "Thing.luau").write_bytes(b"--!strict\nreturn {}\n")
    definitions = tmp_path / "globalTypes.None.d.luau"
    definitions.write_text("declare class Players extends Instance end\n")
    return tmp_path, definitions


def names(report):
    return [check.name for check in report.failed]


def test_a_clean_project_passes_every_check(project):
    root, definitions = project
    runner = Runner(selene=(0, "Results:\n0 errors\n0 warnings\n0 parse errors"))
    report = Gate(KNOWN, definitions, mode="builtin", runner=runner).run(root)
    assert report.passed, report.feedback()
    assert [check.name for check in report.checks] == ["guard", "sourcemap", "selene", "stylua", "luau-lsp"]
    assert ["luau-lsp", "analyze", "--platform=roblox", f"--definitions={definitions}",
            "--sourcemap=sourcemap.json", "src"] in runner.calls


def test_selene_warnings_fail_even_with_a_zero_exit(project):
    root, definitions = project
    report = Gate(KNOWN, definitions, mode="builtin", runner=Runner(selene=(0, "Results:\n0 errors\n2 warnings\n0 parse errors"))).run(root)
    assert names(report) == ["selene"]


def test_a_luau_diagnostic_fails_even_with_a_zero_exit(project):
    root, definitions = project
    output = "src/shared/Thing.luau(3,1): LocalUnused: Variable 'x' is never used"
    report = Gate(KNOWN, definitions, mode="builtin", runner=Runner(**{"luau-lsp": (0, output)})).run(root)
    assert names(report) == ["luau-lsp"] and output in report.feedback()


def test_every_check_reports_in_one_pass(project):
    root, definitions = project
    (root / "src" / "shared" / "Bad.luau").write_bytes(b"--!strict\ngame:MadeUp()\n")
    report = Gate(KNOWN, definitions, mode="builtin", runner=Runner(selene=(1, "error"), stylua=(1, "diff"))).run(root)
    assert names(report) == ["guard", "selene", "stylua"]
    assert "src/Bad.luau" not in report.feedback() and "src/shared/Bad.luau:2:1" in report.feedback()


def test_no_sourcemap_means_luau_lsp_did_not_pass(project):
    root, definitions = project
    runner = Runner(sourcemap=(1, "invalid project"))
    report = Gate(KNOWN, definitions, mode="builtin", runner=runner).run(root)
    assert names(report) == ["sourcemap", "luau-lsp"]
    assert not any(call[0] == "luau-lsp" for call in runner.calls)


def test_a_missing_tool_fails_instead_of_passing(project):
    root, definitions = project
    report = Gate(KNOWN, definitions, mode="builtin", runner=Runner(selene=(None, "`selene` was not found on PATH"))).run(root)
    assert names(report) == ["selene"]


def test_missing_definitions_fail(project):
    root, definitions = project
    definitions.unlink()
    assert names(Gate(KNOWN, definitions, mode="builtin", runner=Runner()).run(root)) == ["luau-lsp"]


def test_fingerprint_ignores_shifting_line_numbers(project):
    root, definitions = project
    first = Gate(KNOWN, definitions, mode="builtin", runner=Runner(**{"luau-lsp": (1, "a.luau(3,1): TypeError: x")})).run(root)
    second = Gate(KNOWN, definitions, mode="builtin", runner=Runner(**{"luau-lsp": (1, "a.luau(9,4): TypeError: x")})).run(root)
    assert first.fingerprint() == second.fingerprint()


def test_crlf_services_module_is_not_byte_identical(project):
    root, definitions = project
    module = root / "src" / "shared" / "Services.luau"
    module.write_bytes(module.read_bytes().replace(b"\n", b"\r\n"))
    report = Gate(KNOWN, definitions, mode="builtin", runner=Runner()).run(root)
    assert names(report) == ["guard"] and "services-module-edited" in report.feedback()


# ---- verify.ps1: the game repository's single definition of acceptable ----

def test_verify_script_mode_runs_the_game_repos_script_and_its_exit_code_decides(project):
    root, definitions = project
    (root / "scripts").mkdir()
    (root / "scripts" / "verify.ps1").write_text("exit 0\n")
    runner = Runner(powershell=(1, "[FAIL] luau-lsp\nsrc/shared/Thing.luau(1,1): TypeError: nope"))
    report = Gate(KNOWN, definitions, runner=runner).run(root)

    assert [check.name for check in report.checks] == ["guard", "verify.ps1"]
    assert names(report) == ["verify.ps1"] and "TypeError: nope" in report.feedback()
    [call] = runner.calls
    assert call[0] == "powershell" and call[-2:] == ["-File", str(root / "scripts" / "verify.ps1")]
    assert "-NonInteractive" in call


def test_verify_script_mode_passes_only_when_the_script_and_guard_both_pass(project):
    root, definitions = project
    (root / "scripts").mkdir()
    (root / "scripts" / "verify.ps1").write_text("exit 0\n")
    assert Gate(KNOWN, definitions, runner=Runner()).run(root).passed
    (root / "src" / "shared" / "Bad.luau").write_bytes(b"--!strict\nscript.Parent:MadeUp()\n")
    assert names(Gate(KNOWN, definitions, runner=Runner()).run(root)) == ["guard"]


def test_a_missing_verify_script_is_a_failure_not_a_skip(project):
    root, definitions = project
    runner = Runner()
    report = Gate(KNOWN, definitions, runner=runner).run(root)
    assert names(report) == ["verify.ps1"] and "not found" in report.feedback()
    assert runner.calls == []


def test_an_unknown_gate_mode_is_refused(project):
    _, definitions = project
    with pytest.raises(ValueError):
        Gate(KNOWN, definitions, mode="trust-me")


# ---- the formatter has to actually run ------------------------------------

def test_a_formatter_that_could_not_run_is_reported_not_swallowed(tmp_path):
    """StyLua missing from PATH looked exactly like StyLua succeeding, so six
    attempts of a real run were failed by a formatter that never touched the
    files. The operator would have debugged the model instead."""
    def missing(args, cwd, timeout):
        return None, "`stylua` was not found on PATH"

    gate = Gate(frozenset(), tmp_path / "defs.d.luau", runner=missing)

    problem = gate.format(tmp_path, ["src/server/A.luau"])

    assert problem and "stylua" in problem


def test_a_formatter_that_ran_reports_nothing():
    calls: list[list[str]] = []

    def ok(args, cwd, timeout):
        calls.append(args)
        return 0, ""

    gate = Gate(frozenset(), Path("defs.d.luau"), runner=ok)

    assert gate.format(Path("."), ["src/server/A.luau"]) is None
    assert calls == [["stylua", "src/server/A.luau"]]


def test_nothing_written_means_nothing_to_format():
    def refuse(args, cwd, timeout):
        raise AssertionError("stylua was run with no files")

    gate = Gate(frozenset(), Path("defs.d.luau"), runner=refuse)

    assert gate.format(Path("."), []) is None


# ---- the pinned toolchain has to be reachable -----------------------------

def test_rokit_shims_are_put_on_the_path():
    """Rokit appends its bin directory to the user PATH at install time, so a
    process started from an older shell does not have it."""
    from app.engineer.gate import rokit_environment

    shims = Path.home() / ".rokit" / "bin"
    if not shims.is_dir():
        pytest.skip("Rokit is not installed on this machine")
    assert str(shims).lower() in rokit_environment()["PATH"].lower()


def test_the_executable_is_resolved_before_the_call():
    """Passing a wider PATH in `env` is not enough on Windows: CreateProcess
    resolves the executable against the parent's PATH, so the child's PATH only
    affects what the child itself launches."""
    from app.engineer.gate import resolve_tool

    shims = Path.home() / ".rokit" / "bin"
    if not (shims / "stylua.exe").is_file():
        pytest.skip("stylua is not installed via Rokit on this machine")
    resolved = resolve_tool(["stylua", "--version"])
    assert Path(resolved[0]).is_file(), resolved
    assert resolved[1:] == ["--version"]


def test_an_unknown_tool_is_left_alone_for_the_runner_to_report():
    from app.engineer.gate import resolve_tool

    assert resolve_tool(["definitely-not-a-real-tool", "-v"]) == ["definitely-not-a-real-tool", "-v"]
