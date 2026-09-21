"""The parts of the Blender bridge that can be judged without Blender.

Running Blender is the slow half and is covered by actually running it. What
is here is the reading and reporting: pulling the script out of an answer,
finding the real fault in a failed run, describing a scene back to the model,
and refusing to call an export a success when nothing reached the disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.blender import bridge


def test_the_script_is_taken_out_of_a_fenced_answer():
    answer = "Here you go:\n\n```python\nimport bpy\nbpy.ops.mesh.primitive_cube_add()\n```\n\nThat builds it."

    assert bridge.code_from(answer) == "import bpy\nbpy.ops.mesh.primitive_cube_add()"


def test_a_model_that_thinks_out_loud_does_not_get_its_thoughts_run():
    answer = "<think>\n```python\nthe wrong one\n```\n</think>\n\n```python\nimport bpy\n```"

    assert bridge.code_from(answer) == "import bpy"


def test_the_longest_block_wins_when_an_answer_carries_several():
    answer = "```python\nimport bpy\n```\nand the real one:\n```python\nimport bpy\nimport bmesh\nmesh = None\n```"

    assert bridge.code_from(answer) == "import bpy\nimport bmesh\nmesh = None"


def test_an_answer_that_is_simply_code_is_taken_whole():
    assert bridge.code_from("import bpy\nbpy.data.objects\n") == "import bpy\nbpy.data.objects"


def test_the_fault_reported_is_the_exception_not_blenders_last_word():
    # Blender's final line names the file, not the fault. Taking the last line
    # matching "Error" fed a model its own filename three attempts running
    # while the cause sat a few lines above.
    output = (
        "Read blend: island.blend\n"
        'Traceback (most recent call last):\n'
        '  File "C:/x/island_last_script.py", line 12, in <module>\n'
        "AttributeError: module 'mathutils.noise' has no attribute 'noise2'\n"
        "Error: Python script failed, check the message in the system console\n"
    )

    said = bridge.why_it_failed(output, 1)

    assert "noise2" in said
    assert "check the message in the system console" not in said


def test_a_failure_with_no_exception_still_says_something():
    said = bridge.why_it_failed("Error: cannot read file\n", 1)
    assert "cannot read file" in said

    assert "3" in bridge.why_it_failed("nothing useful here\n", 3)


def test_an_empty_file_is_described_as_empty():
    assert "empty" in bridge.scene_note({}).lower()
    assert "empty" in bridge.scene_note({"objects": {}}).lower()


def test_the_note_gives_the_model_what_is_already_there():
    note = bridge.scene_note({
        "total_triangles": 3840,
        "objects": {
            "Island_Terrain": {"size": [266.31, 245.18, 17.08], "centre": [2.21, 0.47, -3.46],
                               "triangles": 3840},
        },
    })

    assert "Island_Terrain" in note
    assert "266.31" in note
    assert "3840" in note
    # The point of the note: it must tell the model not to bulldoze the island
    # it is being asked to add a jetty to.
    assert "leave these alone" in note.lower()


def _measured(**overrides) -> dict:
    got = {"size": [4, 4, 4], "centre": [0, 0, 2], "polygons": 12, "triangles": 12,
           "all_triangles": True, "flat_shaded": True, "unapplied_scale": False}
    got.update(overrides)
    return got


def test_a_scene_that_keeps_the_rules_draws_no_complaints():
    applied = bridge.Applied(prompt="x", report={"objects": {"Test_Cube": _measured()}})

    assert applied.complaints() == []


@pytest.mark.parametrize("overrides, expected", [
    ({"all_triangles": False, "polygons": 20, "triangles": 12}, "not triangles"),
    ({"flat_shaded": False}, "smooth shaded"),
    ({"unapplied_scale": True}, "unapplied scale"),
    ({"triangles": 9000, "polygons": 9000}, "9000 triangles"),
])
def test_each_house_rule_is_reported_against_the_measured_scene(overrides, expected):
    applied = bridge.Applied(prompt="x", report={"objects": {"Tower_Body": _measured(**overrides)}})

    complaints = applied.complaints()

    assert len(complaints) == 1
    assert expected in complaints[0]
    # Named, because the feedback goes back to the model and "fix the mesh" is
    # not actionable when the scene holds nine of them.
    assert complaints[0].startswith("Tower_Body")


def _scene(**objects) -> dict:
    return {"objects": {name: {"size": list(got[0]), "centre": list(got[1]),
                               "triangles": got[2], "polygons": got[2]}
                        for name, got in objects.items()}}


def test_the_island_that_was_42_percent_too_big_is_caught():
    # The run this exists for: a shell 370 studs across when the brief said
    # 260. It was triangulated, flat shaded, under budget and clean, so every
    # automatic check passed and the file was saved.
    scene = _scene(Island_Terrain=((370.0, 370.69, 18.0), (2.0, -8.32, -3.0), 960))
    brief = {"Island_Terrain": {"size_x": [252, 266], "size_y": [232, 248]}}

    problems = bridge.shortfalls(scene, brief)

    assert len(problems) == 2
    assert "370.0 studs wide east-west" in problems[0]
    assert "252 to 266" in problems[0]


def test_a_scene_that_matches_the_brief_is_not_complained_about():
    scene = _scene(Island_Terrain=((260.0, 240.0, 18.0), (0.0, 0.0, -3.0), 960))
    brief = {"Island_Terrain": {"size_x": [252, 266], "size_y": [232, 248],
                                "top_z": [5.0, 6.5], "bottom_z": [-12.5, -11.5],
                                "max_triangles": 8000}}

    assert bridge.shortfalls(scene, brief) == []


def test_top_and_bottom_are_derived_from_the_geometry_not_the_centre():
    # A brief says "top surface at z = 6.5", never "centred at z = 3.25".
    numbers = bridge.measured({"size": [32, 32, 6.5], "centre": [0, 0, 3.25]})

    assert numbers["top_z"] == pytest.approx(6.5)
    assert numbers["bottom_z"] == pytest.approx(0.0)


def test_a_dock_deck_at_the_wrong_height_is_reported_in_the_briefs_own_words():
    scene = _scene(Dock_Deck=((20.0, 60.0, 1.0), (0.0, 150.0, 6.0), 400))
    brief = {"Dock_Deck": {"top_z": [4.4, 4.6]}}

    problems = bridge.shortfalls(scene, brief)

    assert problems == ["Dock_Deck: has its top surface at z = 6.5, wanted 4.4 to 4.6"]


def test_an_object_the_brief_names_and_the_file_lacks_is_a_shortfall():
    problems = bridge.shortfalls(_scene(), {"Tower_Body": {"size_x": [13, 15]}})

    assert problems == ["Tower_Body: missing from the file"]


def test_the_triangle_budget_is_part_of_the_brief_too():
    scene = _scene(Tower_Body=((14.0, 14.0, 40.0), (0.0, 0.0, 26.0), 9000))

    problems = bridge.shortfalls(scene, {"Tower_Body": {"max_triangles": 8000}})

    assert problems == ["Tower_Body: 9000 triangles, over the 8000 allowed"]


def test_a_measurement_nobody_takes_is_refused_rather_than_silently_passing():
    # A brief asking for "hieght" must not read as a brief asking for nothing.
    scene = _scene(Plot_NW=((32.0, 32.0, 2.0), (-90.0, -100.0, 5.5), 100))

    problems = bridge.shortfalls(scene, {"Plot_NW": {"hieght": [0, 1]}})

    assert len(problems) == 1
    assert "not something that is measured" in problems[0]


def test_the_shipped_brief_only_asks_for_measurements_that_exist():
    brief = json.loads((Path(bridge.__file__).parent / "briefs" / "island_haven.json")
                       .read_text(encoding="utf-8"))

    for name, rules in brief.items():
        if name.startswith("_"):
            continue
        for key, value in rules.items():
            assert key in bridge.MEASURES or key == "max_triangles", f"{name}.{key}"
            if key != "max_triangles":
                low, high = value
                assert low < high, f"{name}.{key} is an empty range"


def test_a_format_roblox_cannot_take_is_refused_before_blender_is_started(tmp_path):
    blend = tmp_path / "island.blend"
    blend.write_bytes(b"not really a blend, and never opened")

    with pytest.raises(ValueError):
        bridge.export(blend, tmp_path / "out", fmt="gltf")


def test_exporting_a_file_that_does_not_exist_says_so_rather_than_running(tmp_path):
    result = bridge.export(tmp_path / "missing.blend", tmp_path / "out")

    assert result["files"] == {}
    assert "does not exist" in result["error"]


def test_every_axis_the_export_accepts_is_one_the_exporter_understands():
    # The script translates the short names into the enum the obj exporter
    # wants. A name missing from that table exports with the wrong axes or
    # raises inside Blender, where it costs a whole run to find out.
    for axis in ("X", "Y", "Z", "-X", "-Y", "-Z"):
        assert f'"{axis}":' in bridge.EXPORT


def test_the_default_turn_is_the_one_that_keeps_x_and_height():
    # Measured, not chosen: exporting the island with forward +Z put its
    # centre at x = -2.21 where Blender holds it at x = 2.21. Going from a
    # right-handed Z-up space to a right-handed Y-up one costs one sign, and
    # paying it with a mirror would flip every face's winding.
    defaults = bridge.export.__defaults__ or ()
    signature = bridge.export.__kwdefaults__ or {}
    assert signature.get("forward") == "-Z"
    assert signature.get("up") == "Y"
    assert defaults == ()


def test_a_file_blender_named_but_never_wrote_is_not_counted_as_exported(monkeypatch, tmp_path):
    blend = tmp_path / "island.blend"
    blend.write_bytes(b"opened by the fake below, never by Blender")
    out = tmp_path / "out"
    landed = out / "Landed.obj"

    def fake_invoke(script_text: str, blend_path: Path, *, name: str, timeout: float):
        out.mkdir(parents=True, exist_ok=True)
        landed.write_text("v 0 0 0\n", encoding="utf-8")
        report = json.dumps({"files": {
            "Landed": {"path": str(landed), "triangles": 2, "polygons": 2},
            "Vanished": {"path": str(out / "Vanished.obj"), "triangles": 2, "polygons": 2},
        }, "refused": {}})
        return True, "", f"BRIDGE_EXPORT_BEGIN\n{report}\nBRIDGE_EXPORT_END\n"

    monkeypatch.setattr(bridge, "_invoke", fake_invoke)

    result = bridge.export(blend, out)

    assert set(result["files"]) == {"Landed"}
    assert result["files"]["Landed"]["bytes"] == landed.stat().st_size
    assert "Vanished" in result["refused"]
