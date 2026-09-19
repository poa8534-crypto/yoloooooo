"""Can this machine run a build?

The build is started from a browser that may be on another device, so the
person pressing the button cannot see the machine doing the work. That makes
the failure mode specific: a missing formatter is found eight minutes in, by a
run that has already spent an attempt on every system.

The tests that matter here are the ones about not lying in the optimistic
direction. A readiness check that says yes and is wrong is worse than no check,
because it converts a clear "install this" into a mysterious mid-build failure.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.engineer import toolchain


class FakeCompleted:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def repo(tmp_path):
    """A directory that looks like the game repository."""
    root = tmp_path / "game"
    (root / ".git").mkdir(parents=True)
    (root / "default.project.json").write_text("{}", encoding="utf-8")
    return root


# ---- the gate's tools ------------------------------------------------------

def test_a_tool_that_is_not_on_path_is_reported_with_how_to_get_it(monkeypatch, repo):
    monkeypatch.setattr(toolchain, "resolve_tool", lambda args: args)

    report = toolchain.check_gate_tool("stylua", "formatting", repo)

    assert report.present is False
    assert "rokit install" in report.detail


def test_a_shim_that_cannot_find_its_pinned_tool_is_not_reported_as_installed(monkeypatch, repo):
    """The trap this check exists for.

    Rokit puts a shim on PATH that looks up the pinned version from the
    project's rokit.toml. The shim is always there; the tool may not be. Taking
    the shim's presence as proof would be the readiness check passing while
    every build fails on the same tool.
    """
    monkeypatch.setattr(toolchain, "resolve_tool", lambda args: ["C:/rokit/bin/rojo.exe", *args[1:]])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(
        1, stderr=b"\x1b[31mERROR\x1b[0m Failed to find tool 'rojo'"))

    report = toolchain.check_gate_tool("rojo", "sourcemap", repo)

    assert report.present is False
    assert "Failed to find tool" in report.detail
    # And the colour codes do not travel into the browser as escapes.
    assert "\x1b" not in report.detail


def test_a_tool_that_answers_is_reported_with_its_version(monkeypatch, repo):
    monkeypatch.setattr(toolchain, "resolve_tool", lambda args: ["C:/rokit/bin/rojo.exe", *args[1:]])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(0, stdout=b"Rojo 7.7.0\n"))

    report = toolchain.check_gate_tool("rojo", "sourcemap", repo)

    assert (report.present, report.version) == (True, "Rojo 7.7.0")


def test_the_version_is_asked_for_inside_the_game_repository(monkeypatch, repo):
    """Rokit reads the pinned version out of the project it is run in, so
    asking from anywhere else answers "Failed to find tool" on a machine where
    everything is installed."""
    seen = {}
    monkeypatch.setattr(toolchain, "resolve_tool", lambda args: ["C:/rokit/bin/rojo.exe", *args[1:]])

    def record(_args, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        return FakeCompleted(0, stdout=b"Rojo 7.7.0")

    monkeypatch.setattr(subprocess, "run", record)
    toolchain.check_gate_tool("rojo", "sourcemap", repo)

    assert seen["cwd"] == repo


def test_a_tool_that_hangs_is_reported_as_absent_with_the_reason(monkeypatch, repo):
    monkeypatch.setattr(toolchain, "resolve_tool", lambda args: ["C:/rokit/bin/selene.exe", *args[1:]])

    def hang(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="selene", timeout=8)

    monkeypatch.setattr(subprocess, "run", hang)
    report = toolchain.check_gate_tool("selene", "lint", repo)

    assert report.present is False
    assert "did not answer" in report.detail


# ---- agy -------------------------------------------------------------------

class FakeClient:
    def __init__(self, found: str | None):
        self._found = found

    def resolve(self) -> str | None:
        return self._found

    def install_candidates(self) -> list[Path]:
        return [] if self._found else [Path("C:/Users/someone/AppData/Local/agy/bin/agy.exe")]


def test_agy_is_resolved_the_way_the_client_resolves_it(monkeypatch):
    """Not with shutil.which. The installer adds its folder to the registry
    PATH, which a running process never sees, so `which` reports it missing on
    exactly the machine where the build works."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(0, stdout=b"1.2.5"))

    report = toolchain.check_agy(FakeClient("C:/Users/x/AppData/Local/agy/bin/agy.exe"))

    assert report.present is True
    assert report.version == "1.2.5"
    assert report.path.endswith("agy.exe")


def test_agy_missing_says_to_restart_whatever_runs_the_service():
    report = toolchain.check_agy(FakeClient(None))

    assert report.present is False
    assert "restart" in report.detail


# ---- the repository and the definitions ------------------------------------

def test_a_repository_without_git_cannot_hold_a_branch_per_system(tmp_path):
    root = tmp_path / "game"
    root.mkdir()

    report = toolchain.check_repo(root)

    assert report.present is False
    assert "not a git repository" in report.detail


def test_a_repository_without_a_rojo_project_cannot_produce_a_sourcemap(tmp_path):
    root = tmp_path / "game"
    (root / ".git").mkdir(parents=True)

    report = toolchain.check_repo(root)

    assert report.present is False
    assert "default.project.json" in report.detail


def test_missing_definitions_are_a_failure_not_a_warning(tmp_path):
    """luau-lsp runs without them and finds nothing, so a build would accept
    every system with the type check silently doing no work."""
    report = toolchain.check_definitions(tmp_path / "globalTypes.None.d.luau")

    assert report.present is False
    assert "accepted unchecked" in report.detail


# ---- the whole report ------------------------------------------------------

def test_one_missing_tool_makes_the_machine_not_ready(monkeypatch, repo, tmp_path):
    monkeypatch.setattr(toolchain, "check_agy", lambda _client=None: toolchain.ToolReport(
        name="agy", present=False, detail="not found"))
    monkeypatch.setattr(toolchain, "resolve_tool", lambda args: ["C:/rokit/bin/x.exe", *args[1:]])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(0, stdout=b"1.0"))
    (repo / "globalTypes.None.d.luau").write_text("-- types", encoding="utf-8")

    report = toolchain.inspect(repo=repo, definitions=repo / "globalTypes.None.d.luau")

    assert report.ready is False
    assert report.missing == ["agy"]


def test_a_ready_machine_lists_every_tool_with_a_version(monkeypatch, repo):
    monkeypatch.setattr(toolchain, "check_agy", lambda _client=None: toolchain.ToolReport(
        name="agy", present=True, version="1.2.5"))
    monkeypatch.setattr(toolchain, "resolve_tool", lambda args: ["C:/rokit/bin/x.exe", *args[1:]])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(0, stdout=b"1.0"))
    (repo / "globalTypes.None.d.luau").write_text("-- types", encoding="utf-8")

    report = toolchain.inspect(repo=repo, definitions=repo / "globalTypes.None.d.luau")

    assert report.ready is True
    assert {tool.name for tool in report.tools} >= {
        "agy", "rojo", "selene", "stylua", "luau-lsp", "game repository"}


# ---- the endpoint and the refusal -----------------------------------------

@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_the_toolchain_is_readable_over_http(client):
    response = client.get("/api/engineer/toolchain")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["ready"], bool)
    assert {tool["name"] for tool in body["tools"]} >= {"agy", "rojo", "selene", "stylua"}


def test_a_build_is_refused_up_front_when_a_tool_is_missing(client, monkeypatch):
    """Rather than accepted, queued, and failed several minutes later on a
    device that is not the one showing the error."""
    from app.blueprint import api

    monkeypatch.setattr(api, "_toolchain", lambda: {
        "ready": False, "missing": ["stylua"], "tools": []})

    response = client.post("/api/builds", json={"blueprint_id": "bp", "token": "t"})

    assert response.status_code == 409
    assert "stylua" in response.text
