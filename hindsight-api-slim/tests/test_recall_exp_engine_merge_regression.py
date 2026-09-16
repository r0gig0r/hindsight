"""Direct merge regressions for ``MemoryEngine.recall_exp_async``."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from hindsight_api.engine.memory_engine import MemoryEngine
from hindsight_api.engine.response_models import MemoryFact, RecallResult


class _RequestContext:
    def __init__(self) -> None:
        self.cancel_checks = 0

    def raise_if_cancelled(self) -> None:
        self.cancel_checks += 1


class _Acquire:
    def __init__(self, conn: "_Conn") -> None:
        self.conn = conn

    async def __aenter__(self) -> "_Conn":
        return self.conn

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _Conn:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row
        self.fetches: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetches.append((sql, args))
        return [self.row]


@pytest.mark.asyncio
async def test_recall_exp_uses_async_query_embedding_and_skips_entity_enrichment_for_tagged_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fact_id = str(uuid.uuid4())
    fact = MemoryFact(id=fact_id, text="Project Hindsight merge retained fork recall.", fact_type="world")
    row = {
        "id": uuid.UUID(fact_id),
        "text": "Project Hindsight merge retained fork recall. | source:sql",
        "fact_type": "world",
        "context": None,
        "event_date": None,
        "occurred_start": None,
        "occurred_end": None,
        "mentioned_at": None,
        "document_id": None,
        "chunk_id": None,
        "tags": ["project:hindsight"],
        "metadata": {},
        "embedding": [0.9, 0.1],
        "proof_count": None,
        "trust_score": None,
        "helpful_count": None,
        "unhelpful_count": None,
        "source_label": None,
        "similarity": 0.8,
        "bm25_score": None,
        "activation": None,
        "temporal_score": None,
        "temporal_proximity": None,
    }
    conn = _Conn(row)
    engine = MemoryEngine.__new__(MemoryEngine)
    engine.embeddings = object()

    async def _recall_async(**kwargs: Any) -> RecallResult:
        assert kwargs["tags"] == ["project:hindsight"]
        return RecallResult(results=[fact])

    async def _read_backend() -> object:
        return object()

    async def _embed(embeddings: object, texts: list[str], *, input_type: str) -> list[list[float]]:
        assert embeddings is engine.embeddings
        assert texts == ["merge recall"]
        assert input_type == "query"
        return [[1.0, 0.0]]

    monkeypatch.setattr(engine, "recall_async", _recall_async)
    monkeypatch.setattr(engine, "_get_read_backend", _read_backend)
    monkeypatch.setattr("hindsight_api.engine.memory_engine.acquire_with_retry", lambda _backend: _Acquire(conn))
    monkeypatch.setattr(
        "hindsight_api.engine.memories.get_memories",
        lambda: SimpleNamespace(store_owned_for=lambda _bank_id: False),
    )
    monkeypatch.setattr(
        "hindsight_api.engine.retain.embedding_utils.generate_embeddings_batch",
        _embed,
    )
    monkeypatch.setattr(
        "hindsight_api.engine.search.diversity.cluster_and_select",
        lambda candidates, _query_embedding, _threshold: [SimpleNamespace(result=candidates[0])],
    )

    request_context = _RequestContext()
    result = await engine.recall_exp_async(
        bank_id="merge-bank",
        query="merge recall",
        max_tokens=100,
        request_context=request_context,
        tags=["project:hindsight"],
    )

    assert [item.id for item in result.results] == [fact_id]
    assert result.results[0].text == "Project Hindsight merge retained fork recall."
    assert request_context.cancel_checks >= 2
    assert len(conn.fetches) == 1
    assert "JOIN" not in conn.fetches[0][0]
