"""Config surface for the claude_code provider and its account profiles."""

from __future__ import annotations

import json
import unittest.mock
from pathlib import Path

from kiro_crew.config.loader import (
    PROVIDER_ACP,
    PROVIDER_CLAUDE_CODE,
    AccountConfig,
    KiroCrewConfig,
)


def _load(tmp_path: Path, payload: dict) -> KiroCrewConfig:
    """Load *payload* as the active config.

    ``KiroCrewConfig.load()`` takes no path — it reads the data home — so the
    repo-wide pattern is to patch ``config_path`` instead of passing a file.
    """
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload))
    with unittest.mock.patch("kiro_crew.config.loader.config_path", return_value=path):
        return KiroCrewConfig.load()


def test_provider_field_metadata_lists_both_providers():
    """The JSON schema is generated from this metadata; a missing enum value makes
    validation log a violation and silently fall back to the default."""
    from kiro_crew.config.loader import AgentConfig

    meta = AgentConfig.__dataclass_fields__["provider"].metadata
    assert meta["enum"] == [PROVIDER_ACP, PROVIDER_CLAUDE_CODE]


def test_claude_code_provider_survives_a_config_round_trip(tmp_path):
    """An enum violation would be logged and silently replaced by the default."""
    cfg = _load(
        tmp_path,
        {
            "agent": {
                "provider": "claude_code",
                "account": "work",
                "accounts": {"work": {"config_dir": "~/.claude-work"}},
            }
        },
    )

    assert cfg.agent.provider == PROVIDER_CLAUDE_CODE
    assert cfg.agent.account == "work"
    assert cfg.agent.accounts["work"].config_dir == "~/.claude-work"
    assert isinstance(cfg.agent.accounts["work"], AccountConfig)


def test_absent_account_block_defaults_to_empty(tmp_path):
    cfg = _load(tmp_path, {"agent": {"provider": PROVIDER_ACP}})

    assert cfg.agent.account == ""
    assert cfg.agent.accounts == {}


def test_account_entry_without_config_dir_is_accepted(tmp_path):
    """A bare profile means "the default login" — accounts.py resolves it."""
    cfg = _load(tmp_path, {"agent": {"accounts": {"bare": {}}}})

    assert cfg.agent.accounts["bare"].config_dir == ""


def test_non_dict_account_entry_is_dropped_not_fatal(tmp_path):
    """A hand-edited config must not crash the gateway at boot."""
    cfg = _load(tmp_path, {"agent": {"accounts": {"bad": "oops", "ok": {}}}})

    assert "bad" not in cfg.agent.accounts
    assert "ok" in cfg.agent.accounts
