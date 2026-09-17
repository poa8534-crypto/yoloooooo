"""The guard for what luau-lsp cannot type-check.

The failure that matters is a false pass: a `game` the guard does not see,
because it hid in an interpolated string, behind a concatenation, or in an
edited Services module. Those cases are tested first. False alarms on comments,
strings and field names are tested second, because a guard that cries wolf
teaches the model to write around it.
"""

from __future__ import annotations

import pytest

from app.engineer.luau_guard import (
    LuauSyntaxError,
    check_project,
    check_services_module,
    check_source,
    render_services_module,
    tokenize,
)

KNOWN = frozenset({"Players", "ReplicatedStorage", "Workspace", "DataStoreService"})


def rules(source: str) -> list[str]:
    return [v.rule for v in check_source("--!strict\n" + source, "x.luau")]


# ---- the blind spot itself -------------------------------------------------

@pytest.mark.parametrize("code", [
    "game:TotallyMadeUpMethod()",
    'local Players = game:GetService("Players")',
    "local p = game.Players.LocalPlayer",
    "print(Game)",
    "workspace.Gravity = 0",
    "local w = Workspace",
    'local s = "prefix" .. game.Name',
    "local s = `name: {game.Name}`",
    "local s = `outer {`inner {game.Name}`} end`",
    "local s = `{ {a = 1} } {game}`",
    "local t = { game }",
    "call(game)",
    "local g = game  local x = g:Anything()",
])
def test_every_route_to_game_is_rejected(code):
    assert "forbidden-global" in rules(code)


@pytest.mark.parametrize("code", [
    "getfenv(1).game:Foo()",
    'setfenv(1, {})',
    'loadstring("return game")()',
    "_G.Services = 1",
    "shared.x = 1",
])
def test_environment_escape_hatches_are_rejected(code):
    assert "forbidden-global" in rules(code)


def test_require_by_asset_id_is_rejected():
    assert rules("local m = require(123456789)") == ["asset-require"]


def test_the_violation_points_at_the_offending_token():
    [violation] = check_source("--!strict\nlocal a = 1\n  game:Foo()\n", "src/server/Combat.luau")
    assert (violation.path, violation.line, violation.column) == ("src/server/Combat.luau", 3, 3)
    assert "Services" in violation.message


def test_missing_strict_header_is_rejected():
    assert [v.rule for v in check_source("local x = 1\n", "x.luau")] == ["strict-mode"]
    assert [v.rule for v in check_source("--!nonstrict\nlocal x = 1\n", "x.luau")] == ["strict-mode"]


def test_unterminated_input_fails_closed():
    assert rules('local s = "open') == ["syntax"]
    assert rules("local s = `a {game") == ["syntax"]
    with pytest.raises(LuauSyntaxError):
        tokenize("--[[ never closed")


# ---- no false alarms ------------------------------------------------------

@pytest.mark.parametrize("code", [
    "-- game:Foo() in a line comment",
    "--[[ game:Foo()\n spans lines ]]",
    "--[==[ game ]] still inside ]==]",
    'local s = "game:Foo()"',
    "local s = 'workspace'",
    "local s = [[game]]",
    "local s = [=[ ]] game ]=]",
    "local s = `plain game text`",
    "local s = `escaped \\{game}`",
    "local n = player.game",
    "obj:game()",
    "local Services = require(script.Parent.Services)\nServices.Workspace.Gravity = 0",
    "local r = require(script.Parent.Module)",
    "local x = 1e-5 + 0x1F + 1_000",
    "local gameState = 1",
])
def test_legitimate_code_passes(code):
    assert rules(code) == []


# ---- the Services module --------------------------------------------------

def test_generated_services_module_is_accepted_and_deterministic():
    source = render_services_module(["ReplicatedStorage", "Players", "Players"])
    assert source == render_services_module({"Players", "ReplicatedStorage"})
    assert '\tPlayers = game:GetService("Players") :: Players,\n' in source
    assert source.startswith("--!strict\n")
    assert check_services_module(source, "Services.luau", KNOWN) == []


def test_empty_services_module_is_valid():
    assert check_services_module(render_services_module([]), "Services.luau", KNOWN) == []


@pytest.mark.parametrize("tamper", [
    lambda s: s.replace("return {\n", "return {\n\tRaw = game,\n"),
    lambda s: s.replace(":: Players", ":: any"),
    lambda s: s + "\ngame:MadeUp()\n",
    lambda s: s.replace("-- Generated", "-- Hand edited"),
])
def test_an_edited_services_module_is_rejected(tamper):
    source = tamper(render_services_module(["Players"]))
    assert "services-module-edited" in [v.rule for v in check_services_module(source, "S.luau", KNOWN)]


def test_a_service_that_does_not_exist_is_rejected():
    source = render_services_module(["Players", "MadeUpService"])
    assert [v.rule for v in check_services_module(source, "S.luau", KNOWN)] == ["unknown-service"]


def test_check_project_exempts_only_the_services_module(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "Services.luau").write_text(render_services_module(["Players"]), encoding="utf-8")
    (shared / "Copy.luau").write_text(render_services_module(["Players"]), encoding="utf-8")
    (tmp_path / "Ok.luau").write_text("--!strict\nreturn {}\n", encoding="utf-8")

    violations = check_project(tmp_path, shared / "Services.luau", KNOWN)

    assert {(v.path, v.rule) for v in violations} == {("shared/Copy.luau", "forbidden-global")}
