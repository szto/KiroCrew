"""Tests for the claude_code model list assembled by /api/models.

The dropdown is scoped to what the account can actually use: the backend's
advertised set is authoritative when present and the static registry is filtered
down to it, so a free-tier account is not offered flagship models it cannot run.
When nothing is advertised (no session yet) the registry is shown unfiltered,
since an empty advertised set cannot be told apart from "entitled to nothing".
"auto" always leads and is never filtered -- it is the configured-default
sentinel, not a served model.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kiro_crew import model_registry
from kiro_crew.dashboard.handlers import agents
from kiro_crew.dashboard.handlers.agents import (
    _advertised_cc_models,
    _cc_models,
    api_models,
)

# Canonical registry rows now lead the dropdown (replaces _CC_CURATED_MODELS).
_REGISTRY_NAMES = [r["model_name"] for r in model_registry.display_list("claude_code")]


def _request_with_providers(providers: dict) -> MagicMock:
    """Fake aiohttp request whose sessions.active_providers() yields `providers`.

    Mirrors the real SessionManager API (active_providers()) so the test can't
    pass against an attribute the production object doesn't have.
    """
    sessions = SimpleNamespace(active_providers=lambda: list(providers.values()))
    state = SimpleNamespace(sessions=sessions)
    req = MagicMock()
    req.app.__getitem__.return_value = state
    return req


class _FakeProvider:
    def __init__(self, models):
        self._models = models

    def available_models(self):
        return self._models


class TestAdvertisedCcModels:
    def test_maps_modelid_name_description(self):
        # An unknown provider id (not in the registry) passes through unchanged.
        prov = _FakeProvider(
            [
                {"modelId": "claude-sonnet-4-6", "name": "Sonnet 4.6", "description": "Everyday"},
            ]
        )
        out = _advertised_cc_models(_request_with_providers({"s": prov}))
        assert out == [
            {
                "model_name": "claude-sonnet-4-6",
                "display_name": "Sonnet 4.6",
                "description": "Everyday",
            }
        ]

    def test_provider_id_is_never_folded_onto_a_registry_key(self):
        """Even a modelId the registry knows stays verbatim.

        It is the value that goes back on the wire to select the model, and the
        registry's aliases are versionless words pinned to whatever was current
        when the row was written — folding ``opus`` onto ``opus-4.8-1m`` renames
        the live model and then sends an id the backend rejects.
        """
        prov = _FakeProvider(
            [
                {
                    "modelId": "global.anthropic.claude-opus-4-8[1m]",
                    "name": "Opus 4.8",
                    "description": "",
                },
                {"modelId": "opus", "name": "Opus", "description": ""},
            ]
        )
        out = _advertised_cc_models(_request_with_providers({"s": prov}))
        assert [e["model_name"] for e in out] == [
            "global.anthropic.claude-opus-4-8[1m]",
            "opus",
        ]

    def test_empty_when_no_active_sessions(self):
        assert _advertised_cc_models(_request_with_providers({})) == []

    def test_skips_provider_without_accessor(self):
        out = _advertised_cc_models(_request_with_providers({"s": object()}))
        assert out == []


class TestCcModelsMerge:
    def test_registry_set_always_present_even_without_session(self):
        # No live provider → nothing is advertised, so entitlement is UNKNOWN and
        # the full canonical registry is shown unfiltered. An empty advertised set
        # cannot be distinguished from "this account gets nothing", and an empty
        # picker on a cold dashboard is worse than a superset.
        out = _cc_models(_request_with_providers({}))
        names = [m["model_name"] for m in out]
        assert "opus-4.8-1m" in names
        assert "opus-4.8" in names
        assert set(_REGISTRY_NAMES) <= set(names)
        # "auto" leads, not the registry's default-flagged flagship. The flag used
        # to sort a specific paid model to the top and present it as the default.
        assert names[0] == "auto"

    def test_advertised_set_replaces_the_registry(self):
        """What the backend reports IS the offer; the registry does not add to it.

        This is the free-tier case. The registry used to lead unconditionally and
        the adapter could only ADD, so an account served two models was still
        offered the full flagship list and only found out at prompt time.
        """
        prov = _FakeProvider([{"modelId": "sonnet", "name": "Sonnet"}])
        out = _cc_models(_request_with_providers({"s": prov}))
        names = [m["model_name"] for m in out]
        assert names == ["auto", "sonnet"]

    def test_the_adapter_supplies_its_own_display_name(self):
        """No registry row survives to relabel an advertised model.

        The registry's label names a specific version ("Sonnet 4.6"); the
        adapter's names the tier it is actually serving, which is the honest one.
        """
        prov = _FakeProvider([{"modelId": "sonnet", "name": "Sonnet", "description": "Sonnet 5"}])
        out = _cc_models(_request_with_providers({"s": prov}))
        row = next(m for m in out if m["model_name"] == "sonnet")
        assert row["display_name"] == "Sonnet"
        assert row["description"] == "Sonnet 5"

    def test_unknown_advertised_models_still_pass_through(self):
        # Forward-compat: a model the registry does not list is still offered when
        # the backend advertises it, otherwise a newly-served model is unreachable.
        prov = _FakeProvider(
            [
                {"modelId": "claude-opus-4-1", "name": "Opus 4.1", "description": ""},
                {"modelId": "claude-sonnet-4-5", "name": "Sonnet 4.5", "description": ""},
            ]
        )
        out = _cc_models(_request_with_providers({"s": prov}))
        names = [m["model_name"] for m in out]
        assert "claude-opus-4-1" in names
        assert "claude-sonnet-4-5" in names
        # And the unentitled registry flagship is gone.
        assert "opus-4.8-1m" not in names
        # "auto" still leads and is never filtered by entitlement -- it is the
        # configured-default sentinel, not a model the backend serves.
        assert names[0] == "auto"

    def test_configured_default_is_not_resurrected_when_unentitled(self):
        """A stale config pick must not outlive the entitlement.

        Force-including it would reintroduce exactly the unusable option the
        filter removes.
        """
        prov = _FakeProvider(
            [{"modelId": "global.anthropic.claude-sonnet-4-6[1m]", "name": "Sonnet 4.6"}]
        )
        out = _cc_models(_request_with_providers({"s": prov}), configured_default="opus-4.8-1m")
        names = [m["model_name"] for m in out]
        assert "opus-4.8-1m" not in names
        assert names[0] == "auto"

    def test_configured_default_still_included_when_nothing_advertised(self):
        # Entitlement unknown → trust the operator's config rather than dropping
        # their selected model from the picker.
        out = _cc_models(_request_with_providers({}), configured_default="some-custom-model")
        names = [m["model_name"] for m in out]
        assert "some-custom-model" in names
        assert names[0] == "auto"  # still after nothing, before everything else

    def test_each_advertised_model_appears_once(self):
        # Spelling variants of one id must not produce two rows: the dropdown is
        # a pick list, and two entries for the same model read as two models.
        prov = _FakeProvider(
            [
                {"modelId": "opus", "name": "Opus", "description": ""},
                {"modelId": "Opus", "name": "Opus again", "description": ""},
                {"modelId": "sonnet", "name": "Sonnet", "description": ""},
            ]
        )
        out = _cc_models(_request_with_providers({"s": prov}))
        names = [m["model_name"] for m in out]
        assert names.count("opus") == 1
        assert names.count("sonnet") == 1
        assert "Opus" not in names

    def test_the_backends_own_default_choice_folds_onto_auto(self):
        # claude-agent-acp offers a literal "default" ("let me pick"), which is
        # what "auto" already means. Two rows for one behaviour is a false choice.
        prov = _FakeProvider(
            [
                {"modelId": "default", "name": "Default (recommended)", "description": ""},
                {"modelId": "opus", "name": "Opus", "description": ""},
            ]
        )
        out = _cc_models(_request_with_providers({"s": prov}))
        names = [m["model_name"] for m in out]
        assert names == ["auto", "opus"]

    def test_configured_default_force_included(self):
        out = _cc_models(_request_with_providers({}), configured_default="custom-model-xyz")
        names = [m["model_name"] for m in out]
        assert "custom-model-xyz" in names

    def test_configured_default_not_duplicated_if_already_present(self):
        out = _cc_models(
            _request_with_providers({}),
            configured_default="opus-4.8-1m",
        )
        names = [m["model_name"] for m in out]
        assert names.count("opus-4.8-1m") == 1

    def test_configured_default_auto_does_not_insert_blank_row(self):
        # cc_model="auto" round-trips to "" (auto's provider id is empty), which
        # must NOT be inserted as a blank-named row at the top of the dropdown —
        # the "auto" registry row already covers it.
        out = _cc_models(_request_with_providers({}), configured_default="auto")
        names = [m["model_name"] for m in out]
        assert "" not in names
        assert all(m["model_name"] for m in out)
        # the canonical "auto" row is still present, exactly once.
        assert names.count("auto") == 1


def _cc_cfg(model: str = "auto") -> SimpleNamespace:
    return SimpleNamespace(agent=SimpleNamespace(provider="claude_code", model=model))


def _no_probe(*_args, **_kwargs):
    """Stand in for the cold-start probe so a unit test never spawns an adapter.

    Returning ``[]`` is the "probe found nothing" path, which is what makes the
    static registry the answer.
    """

    async def _empty() -> list[dict]:
        return []

    return _empty()


class TestApiModelsClaudeCodeDispatch:
    """On the claude_code provider /api/models serves _cc_models and never
    touches kiro: no readiness gate, no kiro-cli subprocess. Those paths gate on
    kiro login state, which a claude_code gateway does not have."""

    @pytest.mark.asyncio
    async def test_serves_cc_rows_without_kiro_gate_or_spawn(self):
        with (
            patch.object(agents.KiroCrewConfig, "load", return_value=_cc_cfg()),
            patch(
                "kiro_crew.providers.claude_code_factory.probe_available_models",
                _no_probe,
            ),
            patch.object(
                agents,
                "reject_if_kiro_unverified",
                side_effect=AssertionError("kiro readiness gate must not run on claude_code"),
            ),
        ):
            resp = await api_models(_request_with_providers({}))
        assert resp.status == 200
        body = json.loads(resp.text)
        names = [e["model_name"] for e in body]
        # "auto" leads; with the probe empty the claude_code registry is the offer.
        assert names[0] == "auto"
        assert set(_REGISTRY_NAMES) <= set(names)
        # Every row carries the context_window the frontend picker reads.
        assert all("context_window" in e for e in body)

    @pytest.mark.asyncio
    async def test_configured_default_flows_into_dropdown(self):
        with (
            patch.object(agents.KiroCrewConfig, "load", return_value=_cc_cfg("custom-model-xyz")),
            patch(
                "kiro_crew.providers.claude_code_factory.probe_available_models",
                _no_probe,
            ),
            patch.object(
                agents,
                "reject_if_kiro_unverified",
                side_effect=AssertionError("kiro readiness gate must not run on claude_code"),
            ),
        ):
            resp = await api_models(_request_with_providers({}))
        body = json.loads(resp.text)
        assert "custom-model-xyz" in [e["model_name"] for e in body]

    @pytest.mark.asyncio
    async def test_probe_result_replaces_the_static_registry(self):
        """A cold gateway serves what the adapter reports, not the registry.

        This is the whole point of the probe: claude-agent-acp's vocabulary
        (``opus``, ``sonnet``, ``haiku``, …) is versionless and moves with Claude
        Code releases, while the registry's rows are a snapshot that goes stale.
        """

        def _probe(*_args, **_kwargs):
            async def _rows() -> list[dict]:
                return [
                    {"modelId": "opus", "name": "Opus", "description": "Opus 5"},
                    {"modelId": "haiku", "name": "Haiku", "description": "Haiku 4.5"},
                ]

            return _rows()

        with (
            patch.object(agents.KiroCrewConfig, "load", return_value=_cc_cfg()),
            patch("kiro_crew.providers.claude_code_factory.probe_available_models", _probe),
            patch.object(
                agents,
                "reject_if_kiro_unverified",
                side_effect=AssertionError("kiro readiness gate must not run on claude_code"),
            ),
        ):
            resp = await api_models(_request_with_providers({}))
        names = [e["model_name"] for e in json.loads(resp.text)]
        assert names[0] == "auto"
        assert "opus" in names
        assert "haiku" in names
        # Registry rows the adapter did NOT report are filtered out — offering
        # them is what made a prompt fail at turn time instead of at pick time.
        assert "opus-4.8-1m" not in names
        assert "sonnet-4.6-1m" not in names
