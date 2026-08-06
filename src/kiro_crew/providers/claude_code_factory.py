"""Provider factory for the ``claude_code`` backend.

Drives claude-agent-acp through the SAME :class:`AcpProvider` the kiro-cli path
uses — the backend id is the only structural difference, so every consumer that
already branches on ``_is_claude_backend`` lights up without further wiring.

An account profile becomes one env var: ``CLAUDE_CONFIG_DIR``. That is what
isolates the account's credentials and history, so no credential handling lives
here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
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

# How long a probed model list stays fresh. The dashboard re-polls /api/models every
# 8s while its list looks degraded, and a probe costs a real session/new handshake,
# so without a cache a settings page open in a tab would spawn an adapter every 8s.
# Claude Code's served set changes on release cadence, not minutes.
_MODEL_PROBE_TTL_SECS = 600.0

# Ceiling on one probe. It covers spawn + initialize + session/new; past this the
# caller falls back to the static registry rather than holding the request open.
_MODEL_PROBE_TIMEOUT_SECS = 25.0

# The config option claude-agent-acp advertises its model vocabulary under. It sends
# no ``models.availableModels`` payload at all, so this IS the list.
_MODEL_CONFIG_OPTION_ID = "model"

# Probed rows keyed by config dir ("" = Claude Code's own). Entitlement is per
# account, so two profiles must not share an entry.
_probe_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}

# ``approval_mode`` value that means "do not stop for per-tool approval". The other
# enum value, ``interactive``, keeps the backend's per-tool prompt. Either way
# Kiro Crew's OWN PreToolUse gate still evaluates the call — the backend's mode is
# not the security boundary.
_APPROVAL_MODE_AUTO = "auto"

# Registry index for the claude backend. The registry no longer supplies the ids
# sent on the wire — the adapter's own vocabulary does — but its canonical keys are
# still recognised so a value stored before that switch is not sent as-is.
_REGISTRY_PROVIDER = "claude_code"

# The sentinel meaning "let the backend pick", spelled the same in config and in
# the dropdown. Distinct from the adapter's own ``default`` choice, which is a real
# selectable value.
_AUTO_MODEL = "auto"

# Canonical keys the registry knows for this backend. Only used to RECOGNISE a
# stale stored value, never to translate one.
_REGISTRY_CANONICAL_KEYS = frozenset(
    row["model_name"] for row in model_registry.display_list(_REGISTRY_PROVIDER)
)


def _permission_mode(approval_mode: str) -> str:
    """Map Kiro Crew's approval mode onto the backend's permission mode."""
    if approval_mode == _APPROVAL_MODE_AUTO:
        return CC_PERMISSION_MODE_AUTO
    return CC_PERMISSION_MODE_DEFAULT


def _backend_model_id(model: str) -> str:
    """Translate a stored model onto what ``session/set_config_option`` accepts.

    The dropdown's values come from the adapter itself (see
    :func:`probe_available_models`), so the normal case is a pass-through: the
    backend's vocabulary is versionless (``opus`` means whatever Opus is today)
    and translating it through the registry would rewrite Opus 5 into
    ``global.anthropic.claude-opus-4-8[1m]`` — an id this backend rejects.

    ``auto`` and empty both mean "let the backend pick", which is ``""``.

    A canonical registry key stored before the dropdown served backend ids
    (``opus-4.8-1m``) is degraded to ``""`` rather than passed through: the
    registry cannot say which live model it corresponds to, and booting on the
    backend's own default is recoverable where a rejected id is a failed session.
    """
    if not model or model == _AUTO_MODEL:
        return ""
    if model in _REGISTRY_CANONICAL_KEYS:
        logger.info(
            "Stored model %r is a registry key, not a %s id; deferring to the "
            "backend default. Re-pick the model to pin one.",
            model,
            _REGISTRY_PROVIDER,
        )
        return ""
    return model


def _models_from_config_options(config_options: Any) -> list[dict[str, str]]:
    """Map the ``model`` config option's choices onto the dashboard's row shape.

    ``value`` is what ``session/set_config_option`` accepts, so it becomes
    ``modelId`` untranslated — the same round-trip ``AcpClient.set_model`` relies
    on for this backend.
    """
    if not isinstance(config_options, list):
        return []
    for opt in config_options:
        if not isinstance(opt, dict) or opt.get("id") != _MODEL_CONFIG_OPTION_ID:
            continue
        options = opt.get("options")
        if not isinstance(options, list):
            return []
        return [
            {
                "modelId": str(o["value"]),
                "name": str(o.get("name") or o["value"]),
                "description": str(o.get("description") or ""),
            }
            for o in options
            if isinstance(o, dict) and o.get("value")
        ]
    return []


async def _handshake_for_models(argv: list[str], env: dict[str, str], cwd: str) -> list[dict]:
    """Spawn the adapter, run ``initialize`` + ``session/new``, return configOptions.

    Deliberately raw JSON-RPC rather than a real :class:`AcpClient`: this is a
    read-only enumeration with no MCP servers, no agent config and no turn, and
    booting a full client would register the session with the manager.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
        cwd=cwd,
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None

        async def send(req_id: int, method: str, params: dict) -> None:
            payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            proc.stdin.write((json.dumps(payload) + "\n").encode())  # type: ignore[union-attr]
            await proc.stdin.drain()  # type: ignore[union-attr]

        async def await_response(req_id: int) -> dict:
            while True:
                line = await proc.stdout.readline()  # type: ignore[union-attr]
                if not line:
                    raise RuntimeError("adapter closed stdout before responding")
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                # The adapter asks the client questions during session/new (fs
                # capability probes). An unanswered request stalls the handshake,
                # so acknowledge anything addressed to us with an empty result.
                if "method" in msg and "id" in msg:
                    ack = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
                    proc.stdin.write((json.dumps(ack) + "\n").encode())  # type: ignore[union-attr]
                    await proc.stdin.drain()  # type: ignore[union-attr]
                    continue
                if msg.get("id") == req_id:
                    return msg

        await send(1, "initialize", {"protocolVersion": 1, "clientCapabilities": {"fs": {}}})
        await await_response(1)
        await send(2, "session/new", {"cwd": cwd, "mcpServers": []})
        resp = await await_response(2)
        options = (resp.get("result") or {}).get("configOptions")
        return options if isinstance(options, list) else []
    finally:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            proc.kill()


async def probe_available_models(
    cfg: KiroCrewConfig, account: str | None = None
) -> list[dict[str, str]]:
    """Ask the adapter what models this account is served, with a TTL cache.

    The dashboard's model list is only authoritative while a session is live —
    the backend advertises its vocabulary at ``session/new`` and nowhere else. On
    a cold gateway there is no session, so this runs one throwaway handshake to
    get the same answer instead of falling back to a static registry that goes
    stale every time Claude Code ships a model.

    Returns ``[]`` on any failure (adapter missing, not logged in, timeout): the
    caller treats that as "unknown" and shows the registry, which is the same
    degraded path as before.
    """
    from kiro_crew.acp.client import _resolve_claude_acp_bin, _resolve_claude_code_executable
    from kiro_crew.env import augmented_path

    try:
        resolved = resolve_account(cfg, account)
    except AccountError:
        return []
    if not resolved.logged_in:
        # A signed-out profile cannot enumerate, and probing would make the
        # adapter try to open a browser login.
        return []

    cache_key = resolved.config_dir_env or ""
    cached = _probe_cache.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] < _MODEL_PROBE_TTL_SECS:
        return list(cached[1])

    argv = _resolve_claude_acp_bin()
    if not argv:
        return []

    env = {**os.environ}
    env["PATH"] = augmented_path(env.get("PATH", ""))
    if resolved.config_dir_env is not None:
        env[CLAUDE_CONFIG_DIR_ENV] = resolved.config_dir_env
    if not env.get("CLAUDE_CODE_EXECUTABLE"):
        claude_exe = _resolve_claude_code_executable()
        if claude_exe:
            env["CLAUDE_CODE_EXECUTABLE"] = claude_exe

    try:
        options = await asyncio.wait_for(
            _handshake_for_models(argv, env, str(Path.home())),
            timeout=_MODEL_PROBE_TIMEOUT_SECS,
        )
    except Exception:
        logger.debug("claude_code model probe failed", exc_info=True)
        return []

    models = _models_from_config_options(options)
    if models:
        _probe_cache[cache_key] = (now, models)
    return models


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

        model = _backend_model_id(model_override or default_model)

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
