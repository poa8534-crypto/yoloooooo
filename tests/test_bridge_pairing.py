"""The pairing token across restarts.

A new token on every start silently unpaired Studio. The plugin went on
sending the token it had saved, the bridge answered 401, and the widget showed
`HttpError: ConnectFail` -- which reads as "the bridge is not running" when the
bridge is running perfectly and simply does not recognise you. Nothing in
either process is wrong at that point and nothing says so.

A restart is not a new trust relationship, so these are about the token
surviving one.
"""

from __future__ import annotations

import pytest

from app.bridge.pairing import pairing_token, read_token, token_file, write_token


def test_a_restart_keeps_the_same_token(tmp_path):
    """The bug, in one test: two starts, one pairing."""
    first, first_is_new = pairing_token(tmp_path)
    second, second_is_new = pairing_token(tmp_path)

    assert first == second
    assert first_is_new is True
    assert second_is_new is False


def test_the_first_start_makes_a_real_credential(tmp_path):
    token, is_new = pairing_token(tmp_path)

    assert is_new is True
    assert len(token) >= 24
    assert token_file(tmp_path).is_file()


def test_the_token_lives_under_data_which_is_not_committed(tmp_path):
    pairing_token(tmp_path)

    assert token_file(tmp_path).parent.name == "data"


def test_a_truncated_file_is_replaced_rather_than_trusted(tmp_path):
    """A hand-edited or half-written file is not a credential, and running with
    something short enough to guess is worse than pairing again."""
    write_token(tmp_path, "short")

    token, is_new = pairing_token(tmp_path)

    assert is_new is True
    assert token != "short"
    assert len(token) >= 24


def test_a_missing_file_is_not_an_error(tmp_path):
    assert read_token(tmp_path) == ""


def test_the_environment_overrides_the_file(tmp_path, monkeypatch):
    """For a caller that already holds a token it wants honoured -- bringing a
    plugin that is still paired back online without touching Studio."""
    write_token(tmp_path, "a-stored-token-long-enough")
    monkeypatch.setenv("VENTURE_BRIDGE_TOKEN", "an-override-token-long-enough")

    token, is_new = pairing_token(tmp_path)

    assert token == "an-override-token-long-enough"
    assert is_new is False


def test_a_short_override_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("VENTURE_BRIDGE_TOKEN", "nope")

    token, _is_new = pairing_token(tmp_path)

    assert token != "nope"


@pytest.mark.parametrize("stored", ["", "   ", "\n"])
def test_an_empty_file_is_not_a_token(tmp_path, stored):
    token_file(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    token_file(tmp_path).write_text(stored, encoding="utf-8")

    assert read_token(tmp_path) == ""
