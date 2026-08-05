"""Provider factory for the ``claude_code`` backend.

Drives claude-agent-acp through the SAME :class:`AcpProvider` the kiro-cli path
uses — the backend id is the only structural difference, so every consumer that
already branches on ``_is_claude_backend`` lights up without further wiring.

An account profile becomes one env var: ``CLAUDE_CONFIG_DIR``. That is what
isolates the account's credentials and history, so no credential handling lives
here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from kiro_crew import model_registry
from kiro_crew.accounts import CODE_ACCOUNT_NOT_LOGGED_IN, AccountError, resolve_account
from kiro_crew.acp.types import (
    ACP_BACKEND_CLAUDE,
    CC_PERMISSION_MODE_AUTO,
    CC_PERMISSION_MODE_DEFAULT,
)
from kiro_crew.providers.acp import AcpProvider

if TYPE_CHECKING:  # circular import: config.loader -> providers
    from kiro_crew.config.loader import KiroCrewConfig

logger = logging.getLogger(__name__)

# The env var Claude Code reads its config directory from. Owning it here keeps the
# account layer free of provider-specific spelling.
CLAUDE_CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"

# ``approval_mode`` value that means "do not stop for per-tool approval". The other
# enum value, ``interactive``, keeps the backend's per-tool prompt. Either way
# KiroCrew's OWN PreToolUse gate still evaluates the call — the backend's mode is
# not the security boundary.
_APPROVAL_MODE_AUTO = "auto"

# Registry index for the claude backend. Distinct from the ``acp`` index: the
# claude path downgrades ids kiro serves natively (there is no Haiku here).
_REGISTRY_PROVIDER = "claude_code"


def _permission_mode(approval_mode: str) -> str:
    """Map KiroCrew's approval mode onto the backend's permission mode."""
    if approval_mode == _APPROVAL_MODE_AUTO:
        return CC_PERMISSION_MODE_AUTO
    return CC_PERMISSION_MODE_DEFAULT


def build_claude_code_factory(cfg: KiroCrewConfig) -> Callable[..., AcpProvider]:
    """Return the provider factory for ``agent.provider == "claude_code"``.

    The returned callable mirrors the ``_acp`` factory's call signature so the
    session layer needs no branch. ``account`` arrives through
    ``SessionManager.get_or_create``'s ``**extra_factory_kwargs``, so adding it
    changes no existing signature.
    """
    default_model = cfg.agent.model
    permission_mode = _permission_mode(cfg.agent.approval_mode)
    sandbox = cfg.agent.sandbox
    tool_search = cfg.agent.tool_search

    def _claude_code(
        session_key: str | None = None,
        agent: str | None = None,
        channel_id: str | None = None,
        model_override: str | None = None,
        cwd: str | None = None,
        extra_env: dict[str, str] | None = None,
        reasoning_effort_override: str | None = None,
        account: str | None = None,
        **_kwargs: Any,
    ) -> AcpProvider:
        resolved = resolve_account(cfg, account)
        if not resolved.logged_in:
            # Fail at session start rather than letting the adapter surface an
            # opaque auth error mid-turn: the actionable instruction is to run
            # `claude login` against THIS account's directory, and only we know
            # which directory that is.
            raise AccountError(
                CODE_ACCOUNT_NOT_LOGGED_IN,
                f"account {resolved.name!r} has no Claude login in {resolved.config_dir}",
            )

        # Merge, never replace: the caller's env carries unrelated per-session
        # values and dropping them would silently change session behavior.
        env: dict[str, str] = dict(extra_env or {})
        # ``None`` means this account sits on Claude Code's own default directory,
        # where the variable must stay UNSET — pointing it at that directory makes
        # Claude Code read its state file from ``$CLAUDE_CONFIG_DIR/.claude.json``
        # while the default layout keeps that file at ``~/.claude.json``, and the
        # session boots reporting its configuration file missing.
        config_dir_env = resolved.config_dir_env
        if config_dir_env is not None:
            env[CLAUDE_CONFIG_DIR_ENV] = config_dir_env

        model = model_override or default_model
        # Translation boundary: the wire/dropdown value is a canonical registry key
        # ("opus-4.8-1m"), which the backend does not accept. ``auto`` resolves to
        # "" — meaning "let the backend pick".
        model = model_registry.to_provider_id(model, _REGISTRY_PROVIDER) if model else ""

        return AcpProvider(
            work_dir=Path(cwd) if cwd else None,
            model=model,
            agent=agent,
            sandbox_mode=sandbox,
            session_key=session_key,
            channel_id=channel_id,
            extra_env=env,
            acp_backend=ACP_BACKEND_CLAUDE,
            tool_search=tool_search,
            permission_mode=permission_mode,
        )

    return _claude_code
