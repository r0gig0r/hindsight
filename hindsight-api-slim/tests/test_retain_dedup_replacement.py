"""Replacement lifetime regression; DB cases opt in to an explicitly isolated clone.

No default DB/provider fixtures: HINDSIGHT_DEDUP_TEST_DB_FILE must name a JSON
credential file for localhost:19077. All synthetic rows are rolled back.
"""

import json
import os
import time
import uuid
from array import array
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse

import asyncpg
import pytest
import pytest_asyncio

from hindsight_api.config import HindsightConfig
from hindsight_api.engine.db.postgresql import PostgresConnection, PostgreSQLBackend
from hindsight_api.engine.memory_engine import MemoryEngine
from hindsight_api.engine.response_models import TokenUsage
from hindsight_api.engine.retain import orchestrator
from hindsight_api.engine.retain.deduplication import check_duplicates_batch
from hindsight_api.engine.retain.types import ChunkMetadata, ExtractedFact, ProcessedFact, RetainContent

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def fact(vector, text="unchanged"):
    return ProcessedFact(
        fact_text=text,
        fact_type="world",
        embedding=array("f", vector),
        occurred_start=None,
        occurred_end=None,
        mentioned_at=NOW,
        context="",
        metadata={},
        content_index=0,
    )


@pytest.mark.asyncio
async def test_exclusion_forwarded_without_disabling_within_batch_dedup():
    checker = AsyncMock(return_value=[False, False])
    assert await check_duplicates_batch(
        None,
        "bank",
        [fact([1.0, 0.0] + [0.0] * 382), fact([1.0, 0.0] + [0.0] * 382)],
        checker,
        exclude_document_id="outgoing",
    ) == [False, True]
    assert checker.call_args.kwargs["exclude_document_id"] == "outgoing"


@pytest_asyncio.fixture
async def isolated_db():
    filename = os.environ.get("HINDSIGHT_DEDUP_TEST_DB_FILE")
    if not filename:
        pytest.skip("requires explicit isolated PostgreSQL clone credential file")
    url = json.loads(Path(filename).read_text())["url"]
    parsed = urlparse(url)
    assert parsed.hostname in {"localhost", "127.0.0.1"} and parsed.port == 19077
    raw = await asyncpg.connect(url)
    tx = raw.transaction()
    await tx.start()
    try:
        bank = "dedup-regression-" + uuid.uuid4().hex
        await raw.execute("INSERT INTO banks(bank_id) VALUES($1)", bank)
        conn = PostgresConnection(raw)
        yield raw, conn, bank
    finally:
        await tx.rollback()
        await raw.close()


async def seed(raw, bank, doc, vector, date=NOW, text="unchanged"):
    if doc is not None:
        await raw.execute("INSERT INTO documents(id, bank_id) VALUES($1,$2) ON CONFLICT DO NOTHING", doc, bank)
    return await raw.fetchval(
        "INSERT INTO memory_units(bank_id,document_id,text,fact_type,embedding,event_date) "
        "VALUES($1,$2,$3,'world',$4::vector,$5) RETURNING id",
        bank,
        doc,
        text,
        str(vector),
        date,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [0, 7])
@pytest.mark.parametrize("survivor", ["other-document", None])
async def test_real_sql_excludes_only_outgoing_document(isolated_db, days, survivor):
    raw, conn, bank = isolated_db
    engine = MemoryEngine.__new__(MemoryEngine)
    date = NOW - timedelta(days=days)
    await seed(raw, bank, "outgoing", [1.0, 0.0] + [0.0] * 382, date)
    args = (conn, bank, ["unchanged"], [[1.0, 0.0] + [0.0] * 382], NOW)
    assert await engine._find_duplicate_facts_batch(*args) == [True]
    assert await engine._find_duplicate_facts_batch(*args, exclude_document_id="outgoing") == [False]
    await seed(raw, bank, survivor, [1.0, 0.0] + [0.0] * 382, date)
    assert await engine._find_duplicate_facts_batch(*args, exclude_document_id="outgoing") == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("chunk_count", [1, 2])
async def test_forced_streaming_replacement_preserves_unchanged_facts(isolated_db, monkeypatch, mixed, chunk_count):
    """Real dedup SQL, document replacement, observation sweep and chunk writes.

    Extraction/entity/ANN work is stubbed; the insert stub writes real rows using
    the pipeline's document/chunk assignments. No model or provider is contacted.
    """
    raw, conn, bank = isolated_db
    old = await seed(raw, bank, "outgoing", [1.0, 0.0] + [0.0] * 382)
    obs = await raw.fetchval(
        "INSERT INTO memory_units(bank_id,text,fact_type,source_memory_ids) "
        "VALUES($1,'old observation','observation',$2) RETURNING id",
        bank,
        [old],
    )
    engine = MemoryEngine.__new__(MemoryEngine)

    class Pool:
        _wraps_backend = True
        ops = PostgreSQLBackend().ops

        @asynccontextmanager
        async def acquire(self):
            yield conn

    vectors = (
        [[1.0, 0.0] + [0.0] * 382, [0.0, 1.0] + [0.0] * 382]
        if mixed
        else [[1.0, 0.0] + [0.0] * 382, [1.0, 0.0] + [0.0] * 382]
    )
    texts = ["unchanged", "new" if mixed else "unchanged"]

    async def extract(*args, **kwargs):
        return orchestrator._EmbeddedExtraction(
            [ExtractedFact(fact_text=t, fact_type="world", chunk_index=0, mentioned_at=NOW) for t in texts],
            [fact(v, t) for v, t in zip(vectors, texts)],
            [ChunkMetadata("replacement body", len(texts), 0, 0)],
            TokenUsage(),
        )

    async def insert(conn, resolver, bank_id, contents, extracted, processed, *args, **kwargs):
        ids = []
        for item in processed:
            uid = await conn.fetchval(
                "INSERT INTO memory_units(bank_id,document_id,chunk_id,text,fact_type,embedding,event_date) "
                "VALUES($1,$2,$3,$4,'world',$5::vector,$6) RETURNING id",
                bank_id,
                item.document_id,
                item.chunk_id,
                item.fact_text,
                str(list(item.embedding)),
                NOW,
            )
            ids.append(str(uid))
        return [ids]

    monkeypatch.setattr(orchestrator, "_extract_and_embed", extract)
    monkeypatch.setattr(
        orchestrator,
        "_pre_resolve_phase1",
        AsyncMock(
            return_value=SimpleNamespace(
                entities=SimpleNamespace(resolved_entities={}, entity_to_unit={}, unit_to_entity_ids={})
            )
        ),
    )
    monkeypatch.setattr(orchestrator, "_insert_facts_and_links", insert)
    monkeypatch.setattr(orchestrator, "_run_final_semantic_ann", AsyncMock())
    config = HindsightConfig.from_env()
    config.retain_extraction_mode = "concise"
    config.store_document_text = True
    resolver = MagicMock()
    resolver.flush_pending_stats = AsyncMock()
    checker = AsyncMock(wraps=engine._find_duplicate_facts_batch)
    await orchestrator._streaming_retain_batch(
        pool=Pool(),
        embeddings_model=MagicMock(),
        llm_config=MagicMock(),
        entity_resolver=resolver,
        format_date_fn=str,
        bank_id=bank,
        contents_dicts=[{"content": "replacement body"}],
        contents=[RetainContent(content="replacement body")],
        config=config,
        document_id="outgoing",
        is_first_batch=True,
        fact_type_override=None,
        document_tags=None,
        log_buffer=[],
        start_time=time.time(),
        all_pre_chunks=["replacement body"] * chunk_count,
        chunk_to_content=[0] * chunk_count,
        chunk_batch_size=1,
        duplicate_checker_fn=checker,
        force_reextract=True,
    )
    assert checker.call_args_list[0].kwargs["exclude_document_id"] == "outgoing"
    if chunk_count == 2:
        # The first replacement has committed: newly written same-doc facts
        # must be visible to the next consumer batch's dedup check.
        assert "exclude_document_id" not in checker.call_args_list[1].kwargs
    rows = await raw.fetch("SELECT id,text,document_id,chunk_id FROM memory_units WHERE bank_id=$1", bank)
    assert {r["text"] for r in rows} == ({"unchanged", "new"} if mixed else {"unchanged"})
    assert len(rows) == (2 if mixed else 1)
    assert all(r["id"] not in {old, obs} and r["document_id"] == "outgoing" and r["chunk_id"] for r in rows)
    chunks = await raw.fetch("SELECT chunk_id,document_id FROM chunks WHERE bank_id=$1", bank)
    assert len(chunks) == 1 and chunks[0]["document_id"] == "outgoing"
    assert {r["chunk_id"] for r in rows} == {chunks[0]["chunk_id"]}
    assert (
        await raw.fetchval("SELECT original_text FROM documents WHERE bank_id=$1 AND id='outgoing'", bank)
        == "replacement body"
    )
