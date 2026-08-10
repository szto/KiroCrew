"""GET /api/accounts — names and login state only, never paths or tokens."""

from __future__ import annotations

import json
import unittest.mock

import pytest

from kiro_crew.config.loader import PROVIDER_CLAUDE_CODE
from kiro_crew.dashboard.handlers.accounts import api_accounts_get


def _write_config(tmp_path, monkeypatch):
    """Lay down a two-profile config and return its path.

    Handlers load their own config (``KiroCrewConfig.load()``), so the test controls
    what they see by pointing ``config_path`` at this file rather than by injecting a
    config object.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    (work / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "s3cr3t"}})
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "agent": {
                    "provider": PROVIDER_CLAUDE_CODE,
                    "account": "work",
                    "accounts": {
                        "work": {"config_dir": str(work)},
                        "empty": {"config_dir": str(empty)},
                    },
                }
            }
        )
    )
    return path


async def _call(config_file) -> dict:
    request = unittest.mock.Mock()
    with unittest.mock.patch("kiro_crew.config.loader.config_path", return_value=config_file):
        response = await api_accounts_get(request)
    return json.loads(response.body.decode())


@pytest.mark.asyncio
async def test_lists_every_profile_with_login_state(tmp_path, monkeypatch):
    body = await _call(_write_config(tmp_path, monkeypatch))

    assert {a["name"]: a["logged_in"] for a in body["accounts"]} == {"work": True, "empty": False}


@pytest.mark.asyncio
async def test_reports_the_active_account_and_provider(tmp_path, monkeypatch):
    body = await _call(_write_config(tmp_path, monkeypatch))

    assert body["active"] == "work"
    assert body["provider"] == PROVIDER_CLAUDE_CODE


@pytest.mark.asyncio
async def test_never_leaks_config_dirs_or_tokens(tmp_path, monkeypatch):
    """The whole body is checked, not just the fields we remembered to assert."""
    body = await _call(_write_config(tmp_path, monkeypatch))
    raw = json.dumps(body)

    assert "s3cr3t" not in raw
    assert str(tmp_path) not in raw
    assert "config_dir" not in raw


@pytest.mark.asyncio
async def test_declaration_order_is_preserved(tmp_path, monkeypatch):
    body = await _call(_write_config(tmp_path, monkeypatch))

    assert [a["name"] for a in body["accounts"]] == ["work", "empty"]


@pytest.mark.asyncio
async def test_unresolvable_active_account_still_lists_profiles(tmp_path, monkeypatch):
    """A misconfigured ``agent.account`` must not blank the list.

    The user needs to SEE the profiles in order to pick a working one, so the active
    name degrades to empty while the rows stay.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "agent": {
                    "provider": PROVIDER_CLAUDE_CODE,
                    "account": "nope",
                    "accounts": {"work": {"config_dir": str(work)}},
                }
            }
        )
    )

    body = await _call(path)

    assert body["active"] == ""
    assert [a["name"] for a in body["accounts"]] == ["work"]
