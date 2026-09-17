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
    # `newline=""` on every write. `write_text` translates \n to \r\n on
    # Windows, and the Services module's check is byte-exact, so without it
    # this passes on macOS and fails here with `services-module-edited` for a
    # file the generator itself produced. That is the guard being right about a
    # test that wrote the file wrongly, which is also the trap the generator's
    # own callers have to avoid.
    def write(path, text):
        path.write_text(text, encoding="utf-8", newline="")

    write(shared / "Services.luau", render_services_module(["Players"]))
    write(shared / "Copy.luau", render_services_module(["Players"]))
    write(tmp_path / "Ok.luau", "--!strict\nreturn {}\n")

    violations = check_project(tmp_path, shared / "Services.luau", KNOWN)

    assert {(v.path, v.rule) for v in violations} == {("shared/Copy.luau", "forbidden-global")}


# ---- `script`: the same hole reached without naming `game` -----------------
#
# luau-lsp accepted both of these on the real toolchain, because `script` is a
# sourcemap node rather than a typed value:
#
#     script:FindFirstAncestorWhichIsA("DataModel"):MadeUp()
#     script.Parent:MadeUp()
#
# The ban is narrow on purpose. From a typed receiver the same methods are
# checked -- `Services.Players:FindFirstAncestorWhichIsA("DataModel"):MadeUp()`,
# `Services.Workspace.Parent:MadeUp()` and
# `Services.Workspace:FindFirstChild("Thing"):MadeUp()` are all rejected -- so
# only the bare global is a hole.

@pytest.mark.parametrize("code", [
    'script:FindFirstAncestorWhichIsA("DataModel"):MadeUp()',
    "script.Parent:MadeUp()",
    "local x = script.Parent.NotARealChild",
    "local s = script",
    "local n = script.Name",
    "local x = script.Parent.Parent.Parent",
    'local s = `where: {script.Name}`',
    "return script",
])
def test_reaching_through_script_is_refused(code):
    assert "untyped-script" in rules(code), code


@pytest.mark.parametrize("code", [
    "local S = require(script.Parent.Services)",
    'local S = require(script.Parent:WaitForChild("Services"))',
    "local S = require(script.Parent.Parent.shared.Services)",
])
def test_require_is_the_one_way_script_may_be_used(code):
    """It is the only route to the Services module, so banning it outright
    would ban the thing the guard exists to make possible."""
    assert rules(code) == [], code


def test_a_legal_require_does_not_excuse_a_later_reach():
    """The span is the require's own parentheses, not the rest of the file."""
    source = "local S = require(script.Parent.Services)\nscript.Parent:MadeUp()"
    assert rules(source) == ["untyped-script"]


@pytest.mark.parametrize("code", [
    "local x = obj.script",
    "local x = obj:script()",
    'local x = "script.Parent.Thing"',
    "-- script.Parent is fine in a comment",
])
def test_the_word_script_elsewhere_is_not_the_global(code):
    assert rules(code) == [], code


def test_an_annotated_script_is_still_refused():
    """`local s: Instance = script` really is type-safe, and is refused anyway:
    admitting it means reading the annotation, and `local s: any = script`
    passes that reading while restoring the hole in full."""
    assert "untyped-script" in rules("local s: Instance = script")


def test_the_message_names_the_one_legal_use():
    """It is fed back to the model that wrote the violation, so it has to say
    what to write instead."""
    violation = next(v for v in check_source("--!strict\nscript.Parent:MadeUp()", "x.luau")
                     if v.rule == "untyped-script")
    assert "require(script.Parent.X)" in violation.message


def test_the_services_module_may_still_use_game():
    """The `script` rule must not leak into the one file allowed to touch
    `game`, which check_services_module handles instead."""
    source = render_services_module(["Players"])
    assert check_services_module(source, "Services.luau", KNOWN) == []


def test_check_project_reads_bytes_so_a_crlf_services_module_is_refused(tmp_path):
    """`Path.read_text` translates CRLF to LF on every platform, so a CRLF copy
    of the Services module used to compare equal to generator output and pass
    the byte-identity check that exists to refuse it."""
    shared = tmp_path / "shared"
    shared.mkdir()
    module = shared / "Services.luau"
    module.write_bytes(render_services_module(["Players"]).replace("\n", "\r\n").encode("utf-8"))

    violations = check_project(tmp_path, module, KNOWN)

    assert [v.rule for v in violations] == ["services-module-edited"]


# ---- the DataModel, which is not a service --------------------------------
#
# `BindToClose` lives only on the DataModel and the engineering standards
# require saving there, while every route to it was banned. Six attempts of a
# real run died on `game:BindToClose(...)` -- the model obeying one rule and
# breaking another it had no way to keep.
#
# Measured on the real toolchain, which is why the cast is trusted:
#     local dm = game :: DataModel
#     dm:BindToClose(function() end)  -- accepted, a real method
#     dm:MadeUpMethod()               -- rejected, Key not found in DataModel

def test_the_datamodel_is_rendered_as_a_cast_not_a_getservice():
    source = render_services_module(["Players", "DataModel"])
    assert "\tDataModel = game :: DataModel,\n" in source
    assert 'GetService("DataModel")' not in source, "DataModel is not a service"


def test_a_module_exposing_the_datamodel_is_accepted():
    source = render_services_module(["Players", "DataModel"])
    assert check_services_module(source, "Services.luau", KNOWN) == []


def test_the_datamodel_is_not_reported_as_an_unknown_service():
    """It is a class, not a service, so the API dump does not tag it and the
    service-name cache will never contain it."""
    source = render_services_module(["DataModel"])
    assert check_services_module(source, "Services.luau", frozenset()) == []


def test_an_invented_service_is_still_refused_alongside_it():
    source = render_services_module(["DataModel", "MadeUpService"])
    assert [v.rule for v in check_services_module(source, "S.luau", KNOWN)] == ["unknown-service"]


def test_a_hand_written_datamodel_line_is_still_an_edit():
    """The module has to stay byte-identical to generator output, so a
    plausible-looking variant is refused like any other edit."""
    source = render_services_module(["DataModel"]).replace(
        "\tDataModel = game :: DataModel,", "\tDataModel = game :: any,")
    assert "services-module-edited" in [v.rule for v in check_services_module(source, "S.luau", KNOWN)]


def test_calling_the_datamodel_through_the_module_passes_the_guard():
    """What the engineer is now told to write."""
    assert rules("local S = require(script.Parent.Services)\nS.DataModel:BindToClose(save)") == []


def test_bare_bindtoclose_is_still_refused():
    assert "forbidden-global" in rules("game:BindToClose(save)")


# ---- client code: the one tree that is not the tree luau-lsp checked --------
#
# Rojo maps src/client to StarterPlayer.StarterPlayerScripts.Client, and Roblox
# COPIES StarterPlayerScripts into Player.PlayerScripts at run time. Inside the
# copied folder relative depth survives, so `script.Parent.Thing` is the same
# instance in both trees. One step further up is not: the sourcemap says
# StarterPlayerScripts, a live game says PlayerScripts, and above that they
# diverge completely -- Player, Players, DataModel against StarterPlayer,
# DataModel. luau-lsp resolves against the sourcemap, so an escaping require
# type-checks perfectly against a tree that will not exist when it runs.

def client(code: str) -> list[str]:
    return [v.rule for v in check_source("--!strict\n" + code, "src/client/Controller.luau")]


@pytest.mark.parametrize("code", [
    "local S = require(script.Parent.Services)",
    "local M = require(script.Parent.Hud)",
    "local M = require(script.Parent.Widgets.Button)",
    'local M = require(script.Parent:WaitForChild("Hud"))',
])
def test_a_client_require_that_stays_in_its_folder_is_allowed(code):
    assert client(code) == [], code


@pytest.mark.parametrize("code", [
    "local S = require(script.Parent.Parent.Shared.Thing)",
    "local S = require(script.Parent.Parent.Parent.ReplicatedStorage.Shared.Services)",
    'local S = require(script.Parent.Parent:WaitForChild("Shared").Thing)',
])
def test_a_client_require_that_climbs_out_is_refused(code):
    assert "client-require-escapes" in client(code), code


def test_the_message_says_why_the_type_checker_disagrees():
    """It is fed back to the model, which has just watched luau-lsp accept the
    line it is being told to change."""
    [violation] = [v for v in check_source(
        "--!strict\nlocal S = require(script.Parent.Parent.Shared.Thing)",
        "src/client/Controller.luau") if v.rule == "client-require-escapes"]
    assert "PlayerScripts" in violation.message


def test_the_same_require_is_fine_on_the_server():
    """Server scripts are not copied anywhere; their tree is the one in the
    sourcemap, so the rule would be false alarm there."""
    assert rules("local S = require(script.Parent.Parent.Parent.ReplicatedStorage.Shared.Services)") == []


def test_the_rule_counts_climbs_per_require_not_per_file():
    source = ("--!strict\nlocal A = require(script.Parent.One)\n"
              "local B = require(script.Parent.Two)\n")
    assert check_source(source, "src/client/Controller.luau") == []


def test_a_walk_that_nets_back_to_where_it_started_is_refused_anyway():
    """`script.Parent.Nodes.Parent` ends up back in the client folder, so it
    does not really escape. The rule counts climbs and cannot tell a net-zero
    walk from an escape without resolving the tree, and no legitimate require
    needs the form -- requiring a folder is not requiring a module. Refusing it
    is the conservative side of a line that has to be drawn somewhere."""
    assert "client-require-escapes" in client("local M = require(script.Parent.Nodes.Parent)")
