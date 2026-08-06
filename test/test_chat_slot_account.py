"""POST /api/chat/slots/{slot}/account — pick the profile the NEXT session runs on."""

from __future__ import annotations

import json
import unittest.mock

import pytest

from kiro_crew.accounts import CODE_ACCOUNT_NOT_LOGGED_IN, CODE_ACCOUNT_UNKNOWN
from kiro_crew.config.loader import PROVIDER_CLAUDE_CODE
from kiro_crew.dashboard.chat_handlers import api_chat_slot_account


class _Slot:
    def __init__(self) -> None:
        self.account = ""


def _write_config(tmp_path, monkeypatch, *, login_empty: bool = False):
    monkeypatch.setenv("HOME", str(tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    (work / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "x"}}))
    empty = tmp_path / "empty"
    empty.mkdir()
    if login_empty:
        (empty / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "y"}})
        )
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "agent": {
                    "provider": PROVIDER_CLAUDE_CODE,
                    "accounts": {
                        "work": {"config_dir": str(work)},
                        "empty": {"config_dir": str(empty)},
                    },
                }
            }
        )
    )
    return path


async def _post(config_file, slot, body, *, slot_name="s1", state=None):
    request = unittest.mock.Mock()
    state = state or unittest.mock.Mock()
    state._slots = {slot_name: slot} if slot else {}
    request.app = {"state": state}
    request.match_info = {"slot": slot_name}
    request.json = unittest.mock.AsyncMock(return_value=body)
    with unittest.mock.patch("kiro_crew.config.loader.config_path", return_value=config_file):
        response = await api_chat_slot_account(request)
    return response.status, json.loads(response.body.decode())


@pytest.mark.asyncio
async def test_sets_a_logged_in_profile_on_the_slot(tmp_path, monkeypatch):
    slot = _Slot()

    status, body = await _post(_write_config(tmp_path, monkeypatch), slot, {"account": "work"})

    assert (status, body["account"]) == (200, "work")
    assert slot.account == "work"


@pytest.mark.asyncio
async def test_clearing_the_account_returns_to_the_config_default(tmp_path, monkeypatch):
    slot = _Slot()
    slot.account = "work"

    status, body = await _post(_write_config(tmp_path, monkeypatch), slot, {"account": ""})

    assert (status, body["account"]) == (200, "")
    assert slot.account == ""


@pytest.mark.asyncio
async def test_unknown_profile_is_refused_with_a_code(tmp_path, monkeypatch):
    slot = _Slot()

    status, body = await _post(_write_config(tmp_path, monkeypatch), slot, {"account": "nope"})

    assert (status, body["code"]) == (400, CODE_ACCOUNT_UNKNOWN)
    assert slot.account == ""


@pytest.mark.asyncio
async def test_profile_without_a_login_is_refused_here_not_mid_turn(tmp_path, monkeypatch):
    """The remedy names a profile, so the refusal belongs where the profile is picked."""
    slot = _Slot()

    status, body = await _post(_write_config(tmp_path, monkeypatch), slot, {"account": "empty"})

    assert (status, body["code"]) == (400, CODE_ACCOUNT_NOT_LOGGED_IN)
    assert slot.account == ""


@pytest.mark.asyncio
async def test_a_switch_is_pushed_to_every_open_dashboard(tmp_path, monkeypatch):
    """The dropdown renders the slot's account from the slots stream, so a switch
    that is not pushed leaves every OTHER dashboard showing the old account."""
    state = unittest.mock.Mock()

    await _post(_write_config(tmp_path, monkeypatch), _Slot(), {"account": "work"}, state=state)

    state.push_slots_update.assert_called_once_with()


@pytest.mark.asyncio
async def test_a_refused_switch_pushes_nothing(tmp_path, monkeypatch):
    state = unittest.mock.Mock()

    await _post(_write_config(tmp_path, monkeypatch), _Slot(), {"account": "nope"}, state=state)

    state.push_slots_update.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_slot_is_a_404(tmp_path, monkeypatch):
    status, body = await _post(_write_config(tmp_path, monkeypatch), None, {"account": "work"})

    assert (status, body["code"]) == (404, "slot_unknown")


@pytest.mark.asyncio
async def test_non_string_account_is_refused(tmp_path, monkeypatch):
    slot = _Slot()

    status, body = await _post(_write_config(tmp_path, monkeypatch), slot, {"account": 7})

    assert (status, body["code"]) == (400, CODE_ACCOUNT_UNKNOWN)
