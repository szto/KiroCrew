"""The claude_code provider factory: backend selection and account env."""

from __future__ import annotations

import json
import unittest.mock

import pytest

from kiro_crew.accounts import CODE_ACCOUNT_NOT_LOGGED_IN, AccountError
from kiro_crew.acp.types import (
    ACP_BACKEND_CLAUDE,
    CC_PERMISSION_MODE_AUTO,
    CC_PERMISSION_MODE_DEFAULT,
)
from kiro_crew.config.loader import PROVIDER_CLAUDE_CODE, KiroCrewConfig
from kiro_crew.providers.claude_code_factory import build_claude_code_factory


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    (work / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "x"}}))
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "agent": {
                    "provider": PROVIDER_CLAUDE_CODE,
                    "account": "work",
                    "accounts": {"work": {"config_dir": str(work)}},
                }
            }
        )
    )
    with unittest.mock.patch("kiro_crew.config.loader.config_path", return_value=path):
        return KiroCrewConfig.load()


def _captured(monkeypatch):
    """Replace AcpProvider with a recorder so no process is spawned."""
    seen: dict = {}

    class _Fake:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr("kiro_crew.providers.claude_code_factory.AcpProvider", _Fake, raising=True)
    return seen


def test_factory_selects_the_claude_backend(cfg, monkeypatch):
    seen = _captured(monkeypatch)

    build_claude_code_factory(cfg)("slot-1")

    assert seen["acp_backend"] == ACP_BACKEND_CLAUDE


def test_factory_injects_the_account_config_dir(cfg, monkeypatch, tmp_path):
    seen = _captured(monkeypatch)

    build_claude_code_factory(cfg)("slot-1")

    assert seen["extra_env"]["CLAUDE_CONFIG_DIR"] == str(tmp_path / "work")


def test_default_account_injects_no_config_dir(tmp_path, monkeypatch):
    """An account on Claude Code's own directory must leave CLAUDE_CONFIG_DIR unset.

    Setting it to the default directory makes Claude Code look for its state file at
    ``~/.claude/.claude.json``, which the default layout keeps at ``~/.claude.json``
    instead — the session boots reporting its configuration file missing. The factory
    consumes ``ResolvedAccount.config_dir_env`` so this case injects nothing.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    default_dir = tmp_path / ".claude"
    default_dir.mkdir()
    (default_dir / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "x"}})
    )
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"agent": {"provider": PROVIDER_CLAUDE_CODE}}))
    with unittest.mock.patch("kiro_crew.config.loader.config_path", return_value=path):
        bare = KiroCrewConfig.load()
    seen = _captured(monkeypatch)

    build_claude_code_factory(bare)("slot-1")

    assert "CLAUDE_CONFIG_DIR" not in seen["extra_env"]


def test_caller_extra_env_is_preserved(cfg, monkeypatch):
    """Account env must merge into the caller's, not replace it."""
    seen = _captured(monkeypatch)

    build_claude_code_factory(cfg)("slot-1", extra_env={"FOO": "bar"})

    assert seen["extra_env"]["FOO"] == "bar"
    assert "CLAUDE_CONFIG_DIR" in seen["extra_env"]


def test_account_kwarg_outranks_config(cfg, monkeypatch, tmp_path):
    """The per-session pick beats agent.account."""
    from kiro_crew.config.loader import AccountConfig

    other = tmp_path / "other"
    other.mkdir()
    (other / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "y"}}))
    cfg.agent.accounts["other"] = AccountConfig(config_dir=str(other))
    seen = _captured(monkeypatch)

    build_claude_code_factory(cfg)("slot-1", account="other")

    assert seen["extra_env"]["CLAUDE_CONFIG_DIR"] == str(other)


def test_not_logged_in_account_raises_with_code(cfg, monkeypatch, tmp_path):
    from kiro_crew.config.loader import AccountConfig

    empty = tmp_path / "empty"
    empty.mkdir()
    cfg.agent.accounts["empty"] = AccountConfig(config_dir=str(empty))
    _captured(monkeypatch)

    with pytest.raises(AccountError) as exc:
        build_claude_code_factory(cfg)("slot-1", account="empty")

    assert exc.value.code == CODE_ACCOUNT_NOT_LOGGED_IN


def test_auto_approval_maps_to_the_auto_permission_mode(cfg, monkeypatch):
    cfg.agent.approval_mode = "auto"
    seen = _captured(monkeypatch)

    build_claude_code_factory(cfg)("slot-1")

    assert seen["permission_mode"] == CC_PERMISSION_MODE_AUTO


def test_interactive_approval_maps_to_the_default_permission_mode(cfg, monkeypatch):
    """``approval_mode`` enum is ["auto", "interactive"] — interactive keeps per-tool
    approval, which Kiro Crew's own PreToolUse gate still evaluates independently."""
    cfg.agent.approval_mode = "interactive"
    seen = _captured(monkeypatch)

    build_claude_code_factory(cfg)("slot-1")

    assert seen["permission_mode"] == CC_PERMISSION_MODE_DEFAULT


def test_model_override_is_translated_to_a_claude_provider_id(cfg, monkeypatch):
    """Canonical registry keys must not reach the backend unresolved."""
    seen = _captured(monkeypatch)

    build_claude_code_factory(cfg)("slot-1", model_override="auto")

    assert seen["model"] == ""


def test_create_provider_factory_dispatches_on_the_provider(cfg, monkeypatch):
    """The loader must route claude_code here instead of returning _acp."""
    seen = _captured(monkeypatch)

    cfg.create_provider_factory()("slot-1")

    assert seen["acp_backend"] == ACP_BACKEND_CLAUDE
