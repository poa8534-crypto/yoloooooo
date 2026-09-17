"""The engineer reads the gate's service list, and refuses to run without a whole one."""

from __future__ import annotations

import json

import pytest

from app.engineer.catalog import CatalogMissing, load_services


def test_the_list_written_by_the_refresh_script_is_read(tmp_path):
    path = tmp_path / "roblox-services.json"
    names = [f"Service{i}" for i in range(212)]
    path.write_text(json.dumps(names, indent=1) + "\n", encoding="utf-8")
    assert load_services(path) == frozenset(names)


@pytest.mark.parametrize("content, message", [
    (None, "No Roblox service list"),
    ("{not json", "not valid JSON"),
    ('{"Players": true}', "not a list of names"),
    (json.dumps(["Players", "Workspace"]), "only 2 names"),
])
def test_a_missing_or_partial_list_is_refused(tmp_path, content, message):
    path = tmp_path / "roblox-services.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(CatalogMissing, match=message) as caught:
        load_services(path)
    assert "scripts/refresh_roblox_services.py" in str(caught.value)
