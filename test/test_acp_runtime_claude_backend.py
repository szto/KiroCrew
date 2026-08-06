"""AcpRuntime must be able to host claude-agent-acp, not only kiro-cli.

``AcpRuntime`` multiplexes many ACP sessions onto one process and is what the
task runner uses (``SessionManager.open_task_session``). It had no backend
parameter at all, so it always spawned kiro-cli — the task runner ignored
``agent.provider`` entirely. On a claude_code-only host that is a dead end: the
run reports ``Could not generate a plan. Try rephrasing.`` while the log carries
kiro's ``The model 'claude-opus-4.8' is not available``.

Multiplexing is verified against the real adapter: two ``session/new`` calls on
one claude-agent-acp process return distinct session ids.
"""

from __future__ import annotations

import asyncio

import pytest
from spawn_test_helpers import strip_spawn_shim

import kiro_crew.acp.runtime as runtime_mod
from kiro_crew.acp.runtime import AcpRuntime
from kiro_crew.acp.types import ACP_BACKEND_CLAUDE


class _StopSpawn(Exception):
    """Abort spawn once argv/env are captured — no process is ever started."""


@pytest.fixture()
def spawn_probe(tmp_path, monkeypatch):
    """Capture what ``spawn`` would exec, without execing it."""
    seen: dict[str, object] = {}

    def capture_wrap(argv, mode, **kwargs):
        seen.update(argv=list(argv), mode=mode, wrap_kwargs=kwargs)
        return ["/usr/bin/sandbox-wrapper", *argv], None

    async def stop_spawn(*args, **kwargs):
        seen["spawn_args"] = args
        seen["spawn_env"] = kwargs.get("env") or {}
        raise _StopSpawn()

    async def resolve_kiro():
        return "/opt/kiro/kiro-cli"

    monkeypatch.setattr(runtime_mod, "_resolve_kiro_bin_for_spawn", resolve_kiro)
    monkeypatch.setattr(runtime_mod, "wrap_argv", capture_wrap)
    monkeypatch.setattr(runtime_mod, "cgroup_scope_argv", lambda argv: ["/usr/bin/cg", *argv])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", stop_spawn)
    monkeypatch.setattr(
        runtime_mod, "_resolve_claude_acp_bin", lambda: ["/usr/local/bin/claude-agent-acp"]
    )
    monkeypatch.setattr(
        runtime_mod, "_resolve_claude_code_executable", lambda: "/usr/local/bin/claude"
    )
    return seen


async def _spawn_and_capture(runtime: AcpRuntime) -> None:
    with pytest.raises(_StopSpawn):
        await runtime.spawn()


class TestClaudeBackendArgv:
    @pytest.mark.asyncio
    async def test_spawns_the_claude_adapter_not_kiro(self, spawn_probe, tmp_path):
        runtime = AcpRuntime(work_dir=tmp_path / "ws", acp_backend=ACP_BACKEND_CLAUDE)

        await _spawn_and_capture(runtime)

        assert spawn_probe["argv"] == ["/usr/local/bin/claude-agent-acp"]
        assert strip_spawn_shim(spawn_probe["spawn_args"]) == (
            "/usr/bin/cg",
            "/usr/bin/sandbox-wrapper",
            "/usr/local/bin/claude-agent-acp",
        )

    @pytest.mark.asyncio
    async def test_no_agent_or_model_flags_are_passed(self, spawn_probe, tmp_path):
        """``--agent`` and ``--model`` are kiro-cli flags.

        The claude adapter takes neither: the agent is expressed through its own
        config and the model through ``session/set_config_option``. Passing them
        makes the adapter exit before the handshake.
        """
        runtime = AcpRuntime(
            work_dir=tmp_path / "ws",
            agent="kirocrew",
            model="opus[1m]",
            acp_backend=ACP_BACKEND_CLAUDE,
        )

        await _spawn_and_capture(runtime)

        assert "--agent" not in spawn_probe["argv"]
        assert "--model" not in spawn_probe["argv"]

    @pytest.mark.asyncio
    async def test_sandbox_is_told_this_is_not_kiro(self, spawn_probe, tmp_path):
        """``is_kiro_cli`` drives kiro-specific sandbox exemptions."""
        runtime = AcpRuntime(work_dir=tmp_path / "ws", acp_backend=ACP_BACKEND_CLAUDE)

        await _spawn_and_capture(runtime)

        assert spawn_probe["wrap_kwargs"]["is_kiro_cli"] is False

    @pytest.mark.asyncio
    async def test_points_the_adapter_at_a_claude_binary(self, spawn_probe, tmp_path):
        """The adapter's SDK does not search PATH for ``claude`` itself."""
        runtime = AcpRuntime(work_dir=tmp_path / "ws", acp_backend=ACP_BACKEND_CLAUDE)

        await _spawn_and_capture(runtime)

        assert spawn_probe["spawn_env"]["CLAUDE_CODE_EXECUTABLE"] == "/usr/local/bin/claude"

    @pytest.mark.asyncio
    async def test_an_operator_override_of_the_binary_wins(
        self, spawn_probe, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("CLAUDE_CODE_EXECUTABLE", "/custom/claude")
        runtime = AcpRuntime(work_dir=tmp_path / "ws", acp_backend=ACP_BACKEND_CLAUDE)

        await _spawn_and_capture(runtime)

        assert spawn_probe["spawn_env"]["CLAUDE_CODE_EXECUTABLE"] == "/custom/claude"

    @pytest.mark.asyncio
    async def test_the_account_config_dir_reaches_the_adapter(self, spawn_probe, tmp_path):
        """CLAUDE_CONFIG_DIR is what binds a session to an account profile, so it
        has to survive into the runtime the same way it does for a chat session."""
        runtime = AcpRuntime(
            work_dir=tmp_path / "ws",
            acp_backend=ACP_BACKEND_CLAUDE,
            extra_env={"CLAUDE_CONFIG_DIR": "/home/u/.claude-work"},
        )

        await _spawn_and_capture(runtime)

        assert spawn_probe["spawn_env"]["CLAUDE_CONFIG_DIR"] == "/home/u/.claude-work"


class TestKiroBackendIsUnchanged:
    """The default must stay byte-identical — this seam is additive."""

    @pytest.mark.asyncio
    async def test_default_still_spawns_kiro_with_its_flags(self, spawn_probe, tmp_path):
        runtime = AcpRuntime(work_dir=tmp_path / "ws", agent="kirocrew", model="claude-opus-4.8")

        await _spawn_and_capture(runtime)

        assert spawn_probe["argv"] == [
            "/opt/kiro/kiro-cli",
            "acp",
            "--agent",
            "kirocrew",
            "--model",
            "claude-opus-4.8",
        ]
        assert spawn_probe["wrap_kwargs"]["is_kiro_cli"] is True

    @pytest.mark.asyncio
    async def test_kiro_never_gets_the_claude_executable_hint(self, spawn_probe, tmp_path):
        runtime = AcpRuntime(work_dir=tmp_path / "ws")

        await _spawn_and_capture(runtime)

        assert "CLAUDE_CODE_EXECUTABLE" not in spawn_probe["spawn_env"]


class TestMissingAdapterIsActionable:
    @pytest.mark.asyncio
    async def test_absent_adapter_names_the_install_command(
        self, spawn_probe, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(runtime_mod, "_resolve_claude_acp_bin", lambda: None)
        runtime = AcpRuntime(work_dir=tmp_path / "ws", acp_backend=ACP_BACKEND_CLAUDE)

        with pytest.raises(runtime_mod.AcpRuntimeError) as exc:
            await runtime.spawn()

        assert "claude-agent-acp" in str(exc.value)
        assert "npm i -g" in str(exc.value)


class TestModelIdentifierAcceptsBackendSuffixes:
    """The claude vocabulary carries a context-window suffix.

    ``opus[1m]`` and ``claude-fable-5[1m]`` are what the adapter advertises and
    accepts, so a runtime that refuses them refuses the picker's own options.
    The guard's real job — a value can never look like a CLI flag — is unchanged.
    """

    def test_bracketed_ids_are_accepted(self):
        assert AcpRuntime(model="opus[1m]")._model == "opus[1m]"
        assert AcpRuntime(model="claude-fable-5[1m]")._model == "claude-fable-5[1m]"

    def test_plain_ids_still_work(self):
        assert AcpRuntime(model="claude-sonnet-4.6")._model == "claude-sonnet-4.6"

    @pytest.mark.parametrize(
        "bad", ["--trust-all-tools", "-model", "gpt 5.6", "gpt;rm", "a/b", "", "x" * 200]
    )
    def test_injection_shaped_values_are_still_refused(self, bad):
        with pytest.raises(ValueError, match="Invalid model identifier"):
            AcpRuntime(model=bad)


class TestSessionManagerChoosesTheBackend:
    """The task runner must follow ``agent.provider``, not default to kiro."""

    def test_configured_provider_maps_to_the_backend_id(self, monkeypatch):
        from types import SimpleNamespace

        import kiro_crew.session as session_mod
        from kiro_crew.config.loader import KiroCrewConfig

        for provider, want in (("claude_code", ACP_BACKEND_CLAUDE), ("acp", "")):
            monkeypatch.setattr(
                KiroCrewConfig,
                "load",
                staticmethod(lambda p=provider: SimpleNamespace(agent=SimpleNamespace(provider=p))),
            )
            assert session_mod._configured_acp_backend() == want

    def test_an_unreadable_config_falls_back_to_kiro(self, monkeypatch):
        """Historical behaviour, and the safe one: never guess claude."""
        import kiro_crew.session as session_mod
        from kiro_crew.config.loader import KiroCrewConfig

        def _boom():
            raise OSError("config gone")

        monkeypatch.setattr(KiroCrewConfig, "load", staticmethod(_boom))
        assert session_mod._configured_acp_backend() == ""

    def test_a_parent_provider_backend_is_inherited(self):
        """A subagent runtime rides its parent's backend, not the global config —
        the parent may have been started on a different one."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        import kiro_crew.session as session_mod

        mgr = MagicMock(spec=session_mod.SessionManager)
        client = SimpleNamespace(backend=ACP_BACKEND_CLAUDE, _sandbox_mode="strict")
        mgr.get_provider.return_value = SimpleNamespace(client=client)

        kwargs = session_mod.SessionManager._parent_runtime_kwargs(mgr, "parent")

        assert kwargs["acp_backend"] == ACP_BACKEND_CLAUDE
        assert kwargs["sandbox_mode"] == "strict"

    def test_a_kiro_parent_contributes_no_backend_override(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        import kiro_crew.session as session_mod

        mgr = MagicMock(spec=session_mod.SessionManager)
        mgr.get_provider.return_value = SimpleNamespace(
            client=SimpleNamespace(backend="", _sandbox_mode="auto")
        )

        kwargs = session_mod.SessionManager._parent_runtime_kwargs(mgr, "parent")

        assert "acp_backend" not in kwargs


class TestHandshakeProtocolVersion:
    """The two backends disagree on the TYPE of ``protocolVersion``.

    kiro-cli takes its date string; claude-agent-acp takes an integer and
    rejects a string outright ("expected number, received string") before any
    session exists, so the runtime dies during initialize rather than at first
    use. ``spawn`` sends ``_protocol_version`` verbatim.
    """

    def test_claude_gets_an_integer(self):
        version = AcpRuntime(acp_backend=ACP_BACKEND_CLAUDE)._protocol_version
        assert version == runtime_mod.PROTOCOL_VERSION_CLAUDE
        assert isinstance(version, int)

    def test_kiro_keeps_its_date_string(self):
        version = AcpRuntime()._protocol_version
        assert version == runtime_mod.PROTOCOL_VERSION
        assert isinstance(version, str)

    def test_spawn_sends_exactly_that_value(self, spawn_probe, tmp_path, monkeypatch):
        """Guards the wiring, not just the property: an initialize built from a
        literal would pass the two tests above and still ship a string."""
        import inspect

        source = inspect.getsource(AcpRuntime.spawn)
        assert '"protocolVersion": self._protocol_version' in source
