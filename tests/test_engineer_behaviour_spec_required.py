"""A system is not accepted until something runs it.

Twelve systems reached island-haven's master having passed six checks, five of
which only read the code and one of which ran whatever specs existed -- and
none existed for them, because `tests/` was outside the writable roots and
every attempt to write one was refused as an unsafe path. So the behaviour
check passed by measuring nothing.

These tests hold both halves shut: the Engineer may write its own spec and
nothing else under tests/, and the gate refuses a system that arrives without
one, or with one that never loads the module it names.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.engineer.gate import Gate
from app.engineer.workspace import UnsafePath, spec_path, validate_path


def gate() -> Gate:
    return Gate(known_services=frozenset({"Players"}), definitions=Path("nonexistent.d.luau"),
                runner=lambda args, cwd, timeout: (0, "ok"), mode="verify-script")


def project(tmp_path: Path, *, source: str = "src/server/WidgetService.luau",
            spec: str | None = None) -> Path:
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scripts/verify.ps1").write_text("exit 0", encoding="utf-8")
    written = tmp_path / source
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text("--!strict\nreturn {}\n", encoding="utf-8")
    if spec is not None:
        (tmp_path / "tests").mkdir(exist_ok=True)
        (tmp_path / "tests/WidgetService.spec.luau").write_text(spec, encoding="utf-8")
    return tmp_path


def find(report, fragment: str):
    return next(check for check in report.checks if fragment in check.name)


def test_the_engineer_may_write_its_own_spec():
    assert validate_path("tests/WidgetService.spec.luau") == "tests/WidgetService.spec.luau"
    assert spec_path("WidgetService") == "tests/WidgetService.spec.luau"


@pytest.mark.parametrize("path", ["tests/harness.luau", "tests/runner.luau",
                                  "tests/nested/A.spec.luau", "tests/notes.txt",
                                  "tests/WidgetService.luau"])
def test_nothing_else_under_tests_may_be_written(path):
    """The harness and the runner decide what passing means. A model that can
    rewrite them can pass by lowering the bar."""
    with pytest.raises(UnsafePath):
        validate_path(path)


def test_a_system_without_a_spec_is_refused(tmp_path):
    report = gate().run(project(tmp_path), "WidgetService")
    assert not report.passed
    check = find(report, "behaviour spec")
    assert not check.passed and "tests/WidgetService.spec.luau is missing" in check.output


def test_a_spec_that_never_loads_the_module_is_refused(tmp_path):
    """A file called a spec that does not run the system is the same silence
    with a file around it."""
    root = project(tmp_path, spec='return function(harness)\n\treturn { ["nothing"] = function() end }\nend\n')
    check = find(gate().run(root, "WidgetService"), "behaviour spec")
    assert not check.passed and "never loads src/server/WidgetService.luau" in check.output


def test_a_spec_that_loads_the_module_passes(tmp_path):
    root = project(tmp_path, spec='return function(harness)\n'
                                  '\tlocal s = harness.load("src/server/WidgetService.luau")\n'
                                  '\treturn { ["it works"] = function() assert(s) end }\nend\n')
    report = gate().run(root, "WidgetService")
    assert find(report, "behaviour spec").passed and report.passed


def test_the_client_is_asked_for_its_own_path_not_the_servers(tmp_path):
    """Where the source actually is, measured from the tree: a view under
    src/client must not be told to name a path under src/server."""
    root = project(tmp_path, source="src/client/WidgetService.luau")
    check = find(gate().run(root, "WidgetService"), "behaviour spec")
    assert "src/client/WidgetService.luau" in check.output
    assert "src/server/WidgetService.luau" not in check.output


def test_the_preflight_run_asks_for_no_spec(tmp_path):
    """Preflight judges the untouched project, which cannot hold a spec for a
    system nobody has written yet."""
    report = gate().run(project(tmp_path))
    assert report.passed
    assert not any("behaviour spec" in check.name for check in report.checks)
