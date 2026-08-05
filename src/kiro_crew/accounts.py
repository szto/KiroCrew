"""Claude account profiles — a name bound to a ``CLAUDE_CONFIG_DIR``.

Claude Code isolates its own state (credentials, settings, project history) per
``CLAUDE_CONFIG_DIR``, so a profile needs no credential handling of its own:
pointing the adapter at a different directory IS the account switch.

The implicit ``default`` profile resolves to Claude Code's own default directory,
so a config with no ``accounts`` block keeps working against the user's existing
login with no setup.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # circular import: config.loader -> providers -> accounts
    from kiro_crew.config.loader import KiroCrewConfig

logger = logging.getLogger(__name__)

# Claude Code's own default config directory. Both a profile that names no
# directory and the implicit profile resolve here.
DEFAULT_CLAUDE_CONFIG_DIR = "~/.claude"

# Name of the implicit profile a config with no ``accounts`` block gets. Not a
# reserved word: an explicit profile may also be called this.
DEFAULT_ACCOUNT_NAME = "default"

# Claude Code writes its OAuth tokens to this leaf inside a config dir. Reading it
# directly (rather than through the shared file gate) is the same pattern
# ``kiro_usage_api`` uses for the kiro-cli token store: the gate exists to stop the
# AGENT's file tools, not the gateway's own audited readers.
CREDENTIALS_LEAF = ".credentials.json"

# The key Claude Code stores the account OAuth grant under. A credentials file
# holding only ``mcpOAuth`` entries is an MCP-server login, NOT an account login,
# so presence of the file alone is not a sufficient signal.
_OAUTH_KEY = "claudeAiOauth"

# Machine-readable reasons for a non-2xx body. Backend-owned strings have no i18n
# catalog path, so the code is what the dashboard branches on.
CODE_ACCOUNT_UNKNOWN = "account_unknown"
CODE_ACCOUNT_NOT_LOGGED_IN = "account_not_logged_in"


class AccountError(Exception):
    """An account could not be resolved.

    ``code`` is the machine-readable reason a caller puts in its JSON body; the
    message is for logs and is not translated.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ResolvedAccount:
    """A profile resolved to an absolute directory plus its login state."""

    name: str
    config_dir: Path
    logged_in: bool


def _config_dir_for(raw: str) -> Path:
    """Expand a profile's configured directory, defaulting to Claude Code's own."""
    return Path(raw or DEFAULT_CLAUDE_CONFIG_DIR).expanduser()


def _is_logged_in(config_dir: Path) -> bool:
    """Whether *config_dir* holds an account OAuth grant.

    Never returns the token or logs its contents — only the boolean. A missing,
    unreadable, or non-JSON file means "not logged in" rather than an error: the
    caller's job is to tell the user to run ``claude login``, not to distinguish
    the ways a credentials file can be absent.
    """
    path = config_dir / CREDENTIALS_LEAF
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(data.get(_OAUTH_KEY))


def resolve_account(cfg: KiroCrewConfig, name: str | None = None) -> ResolvedAccount:
    """Resolve the account to run on, highest precedence first.

    1. *name* — the caller's explicit per-session pick.
    2. ``cfg.agent.account`` — the configured default.
    3. the first declared profile in declaration order when accounts exist but
       both *name* and ``cfg.agent.account`` are empty. A config that declares
       profiles should have a sensible default; falling through to ``~/.claude``
       when profiles are declared would silently ignore the user's selections.
    4. the implicit ``default`` profile on Claude Code's own config dir when no
       profiles are declared.

    Raises :class:`AccountError` with ``CODE_ACCOUNT_UNKNOWN`` when a name is
    given that no profile declares. A resolved-but-not-logged-in profile is
    returned with ``logged_in=False`` rather than raising: listing it is
    legitimate, and only the session-start path treats it as fatal.
    """
    accounts = getattr(cfg.agent, "accounts", {}) or {}
    requested = name or getattr(cfg.agent, "account", "") or ""

    if not requested:
        if not accounts:
            config_dir = _config_dir_for("")
            return ResolvedAccount(
                name=DEFAULT_ACCOUNT_NAME,
                config_dir=config_dir,
                logged_in=_is_logged_in(config_dir),
            )
        requested = next(iter(accounts))

    if accounts and requested not in accounts:
        raise AccountError(
            CODE_ACCOUNT_UNKNOWN,
            f"no account profile named {requested!r}",
        )

    entry = accounts.get(requested)
    config_dir = _config_dir_for(getattr(entry, "config_dir", "") if entry else "")
    return ResolvedAccount(
        name=requested,
        config_dir=config_dir,
        logged_in=_is_logged_in(config_dir),
    )


def list_accounts(cfg: KiroCrewConfig) -> list[ResolvedAccount]:
    """Every declared profile, or the single implicit default when none are.

    Declaration order is preserved so the dashboard dropdown is stable across
    reloads instead of reordering on dict iteration.
    """
    accounts = getattr(cfg.agent, "accounts", {}) or {}
    if not accounts:
        return [resolve_account(cfg)]
    return [resolve_account(cfg, name) for name in accounts]
