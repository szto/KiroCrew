"""Tests for re-extracting entities over already-stored items.

Motivation: a failed extraction batch still stores the item (with a
heading-derived title and no entities) and reports the ingestion job
``completed``, so an extraction outage leaves a corpus that looks ingested but
has an empty entity graph. Sync cannot repair it — sync re-reads the origin and
skips files whose content is unchanged, which is every file when the text is
fine and only the extraction was lost.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kiro_crew.knowledge.chunker import HeadingAwareChunker
from kiro_crew.knowledge.ingestion import IngestionPipeline
from kiro_crew.knowledge.readers import FileReader
from kiro_crew.knowledge.store import KnowledgeStore


@pytest.fixture()
def kstore(tmp_path):
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    yield store
    store.close()


def _pipeline(kstore, extraction: dict) -> IngestionPipeline:
    extractor = MagicMock()
    extractor._pool = None
    extractor.extract_batch = AsyncMock(
        side_effect=lambda contents: [dict(extraction) for _ in contents]
    )
    return IngestionPipeline(
        store=kstore,
        extractor=extractor,
        chunker=HeadingAwareChunker(),
        reader=FileReader(),
        embedder=None,
        dedup_enabled=False,
    )


def _seed_source_with_items(kstore, count: int = 2) -> str:
    source_id = kstore.add_source(name="docs", source_type="local_folder", uri="/tmp/docs")
    for i in range(count):
        kstore.add_item(
            title=f"chunk {i}",
            content=f"Centrifugo is written in Go. Chunk {i}.",
            item_type="document",
            source_id=source_id,
        )
    return source_id


_EXTRACTION = {
    "title": "Centrifugo",
    "category": "reference",
    "summary": "A real-time messaging server.",
    "entities": [
        {"name": "Centrifugo", "type": "technology", "description": "messaging server"},
        {"name": "Go", "type": "technology", "description": "language"},
    ],
    "relations": [
        {"source": "Centrifugo", "target": "Go", "type": "uses", "description": "written in"}
    ],
}


class TestRebuildEntities:
    @pytest.mark.asyncio
    async def test_populates_entities_for_items_that_had_none(self, kstore):
        source_id = _seed_source_with_items(kstore)
        pipeline = _pipeline(kstore, _EXTRACTION)
        assert kstore.db.execute("SELECT COUNT(*) c FROM entities").fetchone()["c"] == 0

        job_id = await pipeline.rebuild_entities(source_id)

        assert kstore.db.execute("SELECT COUNT(*) c FROM entities").fetchone()["c"] == 2
        assert kstore.db.execute("SELECT COUNT(*) c FROM entity_relations").fetchone()["c"] == 2
        job = pipeline.get_job_status(job_id)
        assert job["status"] == "completed"
        assert job["items_processed"] == 2
        assert job["items_failed"] == 0

    @pytest.mark.asyncio
    async def test_content_is_never_rewritten(self, kstore):
        """Only entities and the summary are refreshed — the text is the input."""
        source_id = _seed_source_with_items(kstore, count=1)
        before = kstore.db.execute("SELECT content FROM items").fetchone()["content"]
        pipeline = _pipeline(kstore, _EXTRACTION)

        await pipeline.rebuild_entities(source_id)

        row = kstore.db.execute("SELECT content, summary FROM items").fetchone()
        assert row["content"] == before
        assert row["summary"] == "A real-time messaging server."

    @pytest.mark.asyncio
    async def test_running_twice_does_not_duplicate_mentions(self, kstore):
        """Rebuild is idempotent, not additive.

        Without dropping the item's old rows first, a second run doubles every
        mention and inflates the degree ranking the graph view sorts on.
        """
        source_id = _seed_source_with_items(kstore, count=1)
        pipeline = _pipeline(kstore, _EXTRACTION)

        await pipeline.rebuild_entities(source_id)
        first = kstore.db.execute("SELECT COUNT(*) c FROM mentions").fetchone()["c"]
        await pipeline.rebuild_entities(source_id)
        second = kstore.db.execute("SELECT COUNT(*) c FROM mentions").fetchone()["c"]

        assert first == 2
        assert second == first

    @pytest.mark.asyncio
    async def test_a_relation_dropped_by_re_extraction_leaves_the_graph(self, kstore):
        """Deleting relation ROWS does not touch the in-memory graph, so the
        rebuild has to reload it or the stale edge keeps being served."""
        source_id = _seed_source_with_items(kstore, count=1)
        await _pipeline(kstore, _EXTRACTION).rebuild_entities(source_id)
        assert len(list(kstore.graph.edges(data=True))) == 1

        thinner = {**_EXTRACTION, "relations": []}
        await _pipeline(kstore, thinner).rebuild_entities(source_id)

        assert list(kstore.graph.edges(data=True)) == []

    @pytest.mark.asyncio
    async def test_never_runs_dedup(self, kstore):
        """Rebuild must not invoke cross-source dedup.

        Dedup collapses a NEWLY INGESTED document against the corpus and
        hard-deletes the loser through ``delete_source_cascade``. A rebuild adds
        no document and does not touch content, so there is nothing to judge —
        and running it destroyed a real 7-item folder source, taking every item,
        mention and relation with it. A button labelled "rebuild graph" must
        never be able to delete the source.
        """
        source_id = _seed_source_with_items(kstore, count=1)
        pipeline = _pipeline(kstore, _EXTRACTION)
        pipeline._dedup_enabled = True
        pipeline._maybe_dedup = MagicMock()

        await pipeline.rebuild_entities(source_id)

        pipeline._maybe_dedup.assert_not_called()
        assert kstore.db.execute(
            "SELECT COUNT(*) c FROM sources WHERE id = ?", (source_id,)
        ).fetchone()["c"] == 1
        assert kstore.db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 1

    @pytest.mark.asyncio
    async def test_empty_source_completes_without_calling_the_extractor(self, kstore):
        source_id = kstore.add_source(name="empty", source_type="local_folder", uri="/tmp/e")
        pipeline = _pipeline(kstore, _EXTRACTION)

        job_id = await pipeline.rebuild_entities(source_id)

        pipeline.extractor.extract_batch.assert_awaited_once_with([])
        job = pipeline.get_job_status(job_id)
        assert job["status"] == "completed"
        assert job["items_total"] == 0

    @pytest.mark.asyncio
    async def test_extractor_failure_marks_the_job_failed(self, kstore):
        """The silent-empty-result path is what hid the original outage, so a
        hard failure here must be recorded rather than reported as completed."""
        source_id = _seed_source_with_items(kstore, count=1)
        pipeline = _pipeline(kstore, _EXTRACTION)
        pipeline.extractor.extract_batch = AsyncMock(side_effect=RuntimeError("pool down"))

        with pytest.raises(RuntimeError):
            await pipeline.rebuild_entities(source_id)

        job = kstore.db.execute(
            "SELECT status, error FROM ingestion_jobs WHERE source_id = ?", (source_id,)
        ).fetchone()
        assert job["status"] == "failed"
        assert "pool down" in job["error"]
