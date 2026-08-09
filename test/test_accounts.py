"""Account-profile resolution: name -> CLAUDE_CONFIG_DIR."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kiro_crew import accounts

# _keychain_has_login is bound at import time, BEFORE the conftest autouse
# fixture patches the module attribute — so the probe's own tests below exercise
# the real function while every other test sees the pinned stub.
from kiro_crew.accounts import (
    CODE_ACCOUNT_NOT_LOGGED_IN,
    CODE_ACCOUNT_UNKNOWN,
    AccountError,
    _keychain_has_login,
    list_accounts,
    resolve_account,
)


class _Agent:
    def __init__(self, account: str = "", accounts: dict | None = None) -> None:
        self.account = account
        self.accounts = accounts or {}


class _Cfg:
    def __init__(self, agent: _Agent) -> None:
        self.agent = agent


class _Acct:
    def __init__(self, config_dir: str) -> None:
        self.config_dir = config_dir


def _login(dir_path: Path) -> None:
    """Write a credentials file shaped like a logged-in Claude Code config dir."""
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "x", "refreshToken": "y"}})
    )


def test_no_accounts_block_resolves_implicit_default(tmp_path, monkeypatch):
    """A config with no accounts block still works: one implicit profile on ~/.claude."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _login(tmp_path / ".claude")

    resolved = resolve_account(_Cfg(_Agent()))

    assert resolved.name == "default"
    assert resolved.config_dir == tmp_path / ".claude"
    assert resolved.logged_in is True


def test_named_account_resolves_its_config_dir(tmp_path):
    work = tmp_path / "work"
    _login(work)
    cfg = _Cfg(_Agent(account="work", accounts={"work": _Acct(str(work))}))

    resolved = resolve_account(cfg)

    assert resolved.name == "work"
    assert resolved.config_dir == work
    assert resolved.logged_in is True


def test_explicit_name_argument_outranks_config(tmp_path):
    """The per-session pick wins over agent.account."""
    a, b = tmp_path / "a", tmp_path / "b"
    _login(a)
    _login(b)
    cfg = _Cfg(_Agent(account="a", accounts={"a": _Acct(str(a)), "b": _Acct(str(b))}))

    assert resolve_account(cfg, "b").config_dir == b


def test_first_declared_profile_when_no_explicit_choice(tmp_path):
    """Declaration order is the contract when accounts exist but no account is set.

    Dict iteration order is the mechanism; first-declared is the documented
    default so users don't get surprising behavior when they declare profiles.
    """
    first = tmp_path / "first"
    second = tmp_path / "second"
    _login(first)
    _login(second)
    cfg = _Cfg(_Agent(accounts={"first": _Acct(str(first)), "second": _Acct(str(second))}))

    resolved = resolve_account(cfg)

    assert resolved.name == "first"
    assert resolved.config_dir == first


def test_unknown_account_raises_with_code(tmp_path):
    cfg = _Cfg(_Agent(accounts={"work": _Acct(str(tmp_path / "work"))}))

    with pytest.raises(AccountError) as exc:
        resolve_account(cfg, "nope")

    assert exc.value.code == CODE_ACCOUNT_UNKNOWN


def test_missing_credentials_is_not_logged_in(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    cfg = _Cfg(_Agent(account="empty", accounts={"empty": _Acct(str(empty))}))

    assert resolve_account(cfg).logged_in is False


def test_credentials_without_oauth_key_is_not_logged_in(tmp_path):
    """An MCP-only credentials file is not an account login."""
    d = tmp_path / "mcp-only"
    d.mkdir()
    (d / ".credentials.json").write_text(json.dumps({"mcpOAuth": {"slack": {}}}))
    cfg = _Cfg(_Agent(account="mcp-only", accounts={"mcp-only": _Acct(str(d))}))

    assert resolve_account(cfg).logged_in is False


def test_corrupt_credentials_is_not_logged_in(tmp_path):
    d = tmp_path / "corrupt"
    d.mkdir()
    (d / ".credentials.json").write_text("{not json")
    cfg = _Cfg(_Agent(account="corrupt", accounts={"corrupt": _Acct(str(d))}))

    assert resolve_account(cfg).logged_in is False


def test_empty_config_dir_falls_back_to_claude_default(tmp_path, monkeypatch):
    """A profile that names no directory means the default login, not an error."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _login(tmp_path / ".claude")
    cfg = _Cfg(_Agent(account="bare", accounts={"bare": _Acct("")}))

    assert resolve_account(cfg).config_dir == tmp_path / ".claude"


def test_tilde_in_config_dir_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _login(tmp_path / "custom")
    cfg = _Cfg(_Agent(account="c", accounts={"c": _Acct("~/custom")}))

    assert resolve_account(cfg).config_dir == tmp_path / "custom"


def test_list_accounts_reports_every_profile(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _login(a)
    b.mkdir()
    cfg = _Cfg(_Agent(accounts={"a": _Acct(str(a)), "b": _Acct(str(b))}))

    rows = list_accounts(cfg)

    assert {r.name for r in rows} == {"a", "b"}
    assert {r.name: r.logged_in for r in rows} == {"a": True, "b": False}


def test_list_accounts_with_no_block_reports_the_implicit_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _login(tmp_path / ".claude")

    rows = list_accounts(_Cfg(_Agent()))

    assert [r.name for r in rows] == ["default"]


def test_default_dir_account_sets_no_config_dir_env(tmp_path, monkeypatch):
    """The default account must inherit Claude Code's own resolution, not override it.

    Claude Code reads its state file from ``$CLAUDE_CONFIG_DIR/.claude.json`` once the
    variable is set, but the default layout keeps that file at ``~/.claude.json`` while
    credentials stay in ``~/.claude``. Setting the variable to the default directory
    therefore boots a session that reports its configuration file missing.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    _login(tmp_path / ".claude")

    assert resolve_account(_Cfg(_Agent())).config_dir_env is None


def test_config_dir_naming_the_default_dir_sets_no_env(tmp_path, monkeypatch):
    """Spelling the default directory out explicitly hits the same trap, so it is
    suppressed by comparing the resolved path, not by remembering whether the user
    left the field blank."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _login(tmp_path / ".claude")
    cfg = _Cfg(_Agent(account="spelled", accounts={"spelled": _Acct("~/.claude")}))

    assert resolve_account(cfg).config_dir_env is None


def test_custom_dir_account_sets_config_dir_env(tmp_path):
    work = tmp_path / "work"
    _login(work)
    cfg = _Cfg(_Agent(account="work", accounts={"work": _Acct(str(work))}))

    assert resolve_account(cfg).config_dir_env == str(work)


def test_not_logged_in_code_is_available_for_callers():
    """The API layer reports this code; keep it exported from one place."""
    assert CODE_ACCOUNT_NOT_LOGGED_IN == "account_not_logged_in"


# --- macOS Keychain fallback -------------------------------------------------
# On macOS `claude login` puts the account grant in the login Keychain, and
# `.credentials.json` carries only MCP-server entries — so the file check alone
# is a false negative for every logged-in macOS user. The conftest autouse
# fixture pins the probe to False; these tests stub the same seam explicitly.


def test_macos_keychain_login_counts_without_credentials_file(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(accounts.platform_compat, "IS_MACOS", True)
    monkeypatch.setattr(accounts, "_keychain_has_login", lambda: True)
    cfg = _Cfg(_Agent(account="empty", accounts={"empty": _Acct(str(empty))}))

    assert resolve_account(cfg).logged_in is True


def test_macos_keychain_login_counts_over_mcp_only_file(tmp_path, monkeypatch):
    """An MCP-only file must not mask a real Keychain account grant."""
    d = tmp_path / "mcp-only"
    d.mkdir()
    (d / ".credentials.json").write_text(json.dumps({"mcpOAuth": {"slack": {}}}))
    monkeypatch.setattr(accounts.platform_compat, "IS_MACOS", True)
    monkeypatch.setattr(accounts, "_keychain_has_login", lambda: True)
    cfg = _Cfg(_Agent(account="mcp-only", accounts={"mcp-only": _Acct(str(d))}))

    assert resolve_account(cfg).logged_in is True


def test_macos_without_keychain_grant_is_not_logged_in(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(accounts.platform_compat, "IS_MACOS", True)
    monkeypatch.setattr(accounts, "_keychain_has_login", lambda: False)
    cfg = _Cfg(_Agent(account="empty", accounts={"empty": _Acct(str(empty))}))

    assert resolve_account(cfg).logged_in is False


def test_non_macos_never_probes_the_keychain(tmp_path, monkeypatch):
    """Linux/Windows credentials live in the file; a probe there is a bug."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(accounts.platform_compat, "IS_MACOS", False)

    def _boom() -> bool:
        raise AssertionError("keychain probed on a non-macOS platform")

    monkeypatch.setattr(accounts, "_keychain_has_login", _boom)
    cfg = _Cfg(_Agent(account="empty", accounts={"empty": _Acct(str(empty))}))

    assert resolve_account(cfg).logged_in is False


def test_credentials_file_wins_without_probing_the_keychain(tmp_path, monkeypatch):
    """A file-based grant (keychain unavailable / migrated layout) short-circuits."""
    work = tmp_path / "work"
    _login(work)
    monkeypatch.setattr(accounts.platform_compat, "IS_MACOS", True)

    def _boom() -> bool:
        raise AssertionError("keychain probed despite a file grant")

    monkeypatch.setattr(accounts, "_keychain_has_login", _boom)
    cfg = _Cfg(_Agent(account="work", accounts={"work": _Acct(str(work))}))

    assert resolve_account(cfg).logged_in is True


def test_keychain_probe_never_requests_the_secret(monkeypatch):
    """The probe is an existence check: no ``-w`` (which would print the token to
    stdout), and both streams discarded so the entry's attributes cannot leak
    into logs either."""
    seen: list[tuple[list[str], dict]] = []

    def _fake_run(argv, **kwargs):
        seen.append((list(argv), kwargs))

        class _Proc:
            returncode = 0

        return _Proc()

    monkeypatch.setattr(
        accounts.platform_compat, "trusted_system_bin", lambda name: f"/usr/bin/{name}"
    )
    monkeypatch.setattr(accounts.subprocess, "run", _fake_run)

    assert _keychain_has_login() is True
    ((argv, kwargs),) = seen
    assert argv[0] == "/usr/bin/security"
    assert "-w" not in argv
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


def test_keychain_probe_missing_security_binary_is_not_logged_in(monkeypatch):
    monkeypatch.setattr(accounts.platform_compat, "trusted_system_bin", lambda name: None)

    assert _keychain_has_login() is False


def test_keychain_probe_nonzero_exit_is_not_logged_in(monkeypatch):
    monkeypatch.setattr(
        accounts.platform_compat, "trusted_system_bin", lambda name: f"/usr/bin/{name}"
    )

    def _fake_run(argv, **kwargs):
        class _Proc:
            returncode = 44  # errSecItemNotFound

        return _Proc()

    monkeypatch.setattr(accounts.subprocess, "run", _fake_run)

    assert _keychain_has_login() is False
