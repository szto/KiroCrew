"""Extraction failures must leave a trace in the log.

An empty extraction result is indistinguishable from a genuinely entity-free
chunk, so it is stored as a success: the item lands with a heading-derived title,
a NULL summary and no entities, and the ingestion job reports ``completed`` with
zero failures. That silence let a broken worker run undetected while every
document ingested into an empty entity graph — the failure was only found by
reading the SQLite file. These tests pin the three paths that used to return an
empty result without saying anything.
"""

from __future__ import annotations

import json
import logging

import pytest

from kiro_crew.knowledge.extractor import EntityExtractor

_LOGGER = "kiro_crew.knowledge.extractor"

_GOOD = json.dumps(
    {
        "title": "T",
        "entities": [{"name": "Svc", "type": "service", "description": "A"}],
        "relations": [],
        "category": "design_doc",
        "summary": "s",
    }
)


class _Pool:
    """Pool stub whose responses (or failure) the test dictates."""

    def __init__(self, responses=None, error: Exception | None = None):
        self._responses = responses or []
        self._error = error

    async def send(self, prompt, timeout=60.0):
        if self._error:
            raise self._error
        return self._responses[0]

    async def send_batch(self, prompts, timeout=60.0):
        if self._error:
            raise self._error
        return list(self._responses[: len(prompts)])


class TestBatchFailureIsLogged:
    @pytest.mark.asyncio
    async def test_pool_failure_warns_and_names_the_chunk_count(self, caplog):
        ext = EntityExtractor(pool=_Pool(error=RuntimeError("pool down")))

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            results = await ext.extract_batch(["a", "b"])

        # One result per chunk is the contract callers zip against — a short
        # list would silently misalign extractions with chunks.
        assert len(results) == 2
        assert all(not r["entities"] and not r["summary"] for r in results)
        assert "all 2 chunk(s)" in caplog.text
        assert "pool down" in caplog.text  # exc_info carries the cause

    @pytest.mark.asyncio
    async def test_single_chunk_failure_warns(self, caplog):
        ext = EntityExtractor(pool=_Pool(error=RuntimeError("boom")))

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            result = await ext.extract("some text")

        assert result["entities"] == []
        assert "boom" in caplog.text


class TestUnparseableResponseIsLogged:
    def test_warns_with_length_and_never_the_body(self, caplog):
        # The body is model output over UNTRUSTED document text, so it must not
        # reach the log — only its length, which still separates "empty response"
        # from "non-JSON response".
        secret = "TOTALLY-NOT-JSON-corp-secret-xyz"
        ext = EntityExtractor()

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            result = ext._parse_response(secret)

        assert result["entities"] == []
        assert f"({len(secret)} chars)" in caplog.text
        assert secret not in caplog.text

    def test_valid_json_does_not_warn(self, caplog):
        ext = EntityExtractor()

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            result = ext._parse_response(_GOOD)

        assert result["entities"]
        assert caplog.text == ""


class TestWholeBatchEmptyIsLogged:
    """The signal that would have caught the outage on day one."""

    @pytest.mark.asyncio
    async def test_warns_when_every_chunk_extracted_nothing(self, caplog):
        # The pool answers, so no exception fires — but nothing parses, which is
        # exactly how the broken worker behaved.
        ext = EntityExtractor(pool=_Pool(responses=["not json", "also not json"]))

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            await ext.extract_batch(["a", "b"])

        assert "produced nothing for all 2 chunk(s)" in caplog.text

    @pytest.mark.asyncio
    async def test_silent_when_at_least_one_chunk_yielded_something(self, caplog):
        # A partially entity-free corpus is normal and must not warn.
        ext = EntityExtractor(pool=_Pool(responses=[_GOOD, "not json"]))

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            await ext.extract_batch(["a", "b"])

        assert "produced nothing" not in caplog.text

    @pytest.mark.asyncio
    async def test_no_chunks_is_not_reported_as_an_outage(self, caplog):
        ext = EntityExtractor(pool=_Pool(responses=[]))

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            await ext.extract_batch([])

        assert caplog.text == ""
