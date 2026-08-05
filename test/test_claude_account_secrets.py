"""Claude credential stores must be invisible to the agent's file tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from kiro_crew.security import is_sensitive_path


def test_claude_credentials_file_is_sensitive(monkeypatch, tmp_path):
    """``is_sensitive_path`` is read+write by contract — one check covers both verbs,
    which matters here because a WRITABLE credentials file is a token-replacement
    vector, not just a disclosure one."""
    monkeypatch.setenv("HOME", str(tmp_path))
    target = Path.home() / ".claude" / ".credentials.json"

    assert is_sensitive_path(str(target)) is True


def test_claude_settings_stay_readable(monkeypatch, tmp_path):
    """Leaf-level classification, not whole-directory: settings are legitimate reads."""
    monkeypatch.setenv("HOME", str(tmp_path))

    assert is_sensitive_path(str(Path.home() / ".claude" / "settings.json")) is False
    assert is_sensitive_path(str(Path.home() / ".claude" / "CLAUDE.md")) is False


@pytest.mark.parametrize("prefix", [".kiro/crew", ".kirocrew"])
def test_account_dir_is_sensitive_under_both_data_homes(prefix, monkeypatch, tmp_path):
    """config_dir() can resolve to the legacy data home during a migration fallback."""
    monkeypatch.setenv("HOME", str(tmp_path))
    target = Path.home() / prefix / "accounts" / "work" / ".credentials.json"

    assert is_sensitive_path(str(target)) is True


@pytest.mark.parametrize("prefix", [".kiro/crew", ".kirocrew"])
def test_account_dir_itself_is_sensitive(prefix, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert is_sensitive_path(str(Path.home() / prefix / "accounts")) is True
