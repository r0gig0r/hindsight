"""Merge regressions for the fork recall_exp HTTP routing."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hindsight_api.api import create_app
from hindsight_api.config import clear_config_cache
from hindsight_api.engine.response_models import RecallResult


class _FakeMemory:
    audit_logger = None
    _operation_validator = None

    def __init__(self) -> None:
        self.recall_async_calls: list[dict[str, Any]] = []
        self.recall_exp_async_calls: list[dict[str, Any]] = []

    async def recall_async(self, **kwargs: Any) -> RecallResult:
        self.recall_async_calls.append(kwargs)
        return RecallResult(results=[])

    async def recall_exp_async(self, **kwargs: Any) -> RecallResult:
        self.recall_exp_async_calls.append(kwargs)
        return RecallResult(results=[])

    async def resolve_attachments(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []


@pytest.mark.asyncio
async def test_http_recall_uses_exp_route_and_forwards_native_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HINDSIGHT_API_RECALL_USES_EXP", "true")
    clear_config_cache()
    memory = _FakeMemory()
    app = create_app(memory, initialize_memory=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/default/banks/merge-bank/memories/recall",
            json={
                "query": "what changed last quarter",
                "types": ["world", "experience"],
                "prefer_observations": True,
                "budget": "low",
                "max_tokens": 321,
                "trace": True,
                "query_timestamp": "2026-09-16T12:00:00Z",
                "include": {
                    "entities": {"max_tokens": 17},
                    "chunks": {"max_tokens": 19},
                    "source_facts": {"max_tokens": 23, "max_tokens_per_observation": 5},
                },
                "tags": ["project:hindsight"],
                "tags_match": "all_strict",
                "min_scores": {"reranker": 0.7, "final": 0.25},
                "temporal_window": {
                    "start": "2026-07-01T00:00:00Z",
                    "end": "2026-09-30T23:59:59Z",
                },
            },
        )

    clear_config_cache()

    assert response.status_code == 200, response.text
    assert memory.recall_async_calls == []
    assert len(memory.recall_exp_async_calls) == 1
    captured = memory.recall_exp_async_calls[0]
    assert captured["bank_id"] == "merge-bank"
    assert captured["query"] == "what changed last quarter"
    assert captured["fact_type"] == ["world", "experience"]
    assert captured["prefer_observations"] is True
    assert captured["max_tokens"] == 321
    assert captured["enable_trace"] is True
    assert captured["include_entities"] is True
    assert captured["max_entity_tokens"] == 17
    assert captured["include_chunks"] is True
    assert captured["max_chunk_tokens"] == 19
    assert captured["include_source_facts"] is True
    assert captured["max_source_facts_tokens"] == 23
    assert captured["max_source_facts_tokens_per_observation"] == 5
    assert captured["tags"] == ["project:hindsight"]
    assert captured["tags_match"] == "all_strict"
    assert captured["tag_groups"] is None
    assert captured["min_scores"].reranker == 0.7
    assert captured["min_scores"].final == 0.25
    assert captured["temporal_window"].start.isoformat() == "2026-07-01T00:00:00+00:00"
    assert captured["question_date"].isoformat() == "2026-09-16T12:00:00+00:00"
    assert captured["request_context"] is not None


@pytest.mark.asyncio
async def test_http_recall_exp_route_forwards_compound_tag_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HINDSIGHT_API_RECALL_USES_EXP", "true")
    clear_config_cache()
    memory = _FakeMemory()
    app = create_app(memory, initialize_memory=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/default/banks/merge-bank/memories/recall",
            json={
                "query": "compound tag query",
                "tag_groups": [{"tags": ["merge"], "match": "any"}],
            },
        )

    clear_config_cache()

    assert response.status_code == 200, response.text
    assert memory.recall_async_calls == []
    assert len(memory.recall_exp_async_calls) == 1
    captured = memory.recall_exp_async_calls[0]
    assert captured["tags"] is None
    assert captured["tags_match"] == "any"
    assert captured["tag_groups"][0].tags == ["merge"]


@pytest.mark.asyncio
async def test_explicit_recall_exp_endpoint_uses_entity_tools_variant_with_full_request_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_config_cache()
    memory = _FakeMemory()
    app = create_app(memory, initialize_memory=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/default/banks/merge-bank/memories/recall_exp",
            json={
                "query": "what changed last quarter",
                "types": ["world"],
                "prefer_observations": True,
                "budget": "low",
                "max_tokens": 321,
                "trace": True,
                "query_timestamp": "2026-09-16T12:00:00Z",
                "include": {"chunks": {"max_tokens": 19}},
                "tags": ["project:hindsight"],
                "tags_match": "all_strict",
                "min_scores": {"final": 0.25},
                "temporal_window": {
                    "start": "2026-07-01T00:00:00Z",
                    "end": "2026-09-30T23:59:59Z",
                },
                "experimental_variant": "entity_tools",
                "cluster_threshold": 0.72,
            },
        )

    clear_config_cache()

    assert response.status_code == 200, response.text
    assert memory.recall_async_calls == []
    assert len(memory.recall_exp_async_calls) == 1
    captured = memory.recall_exp_async_calls[0]
    assert captured["budget"].value == "low"
    assert captured["enable_trace"] is True
    assert captured["fact_type"] == ["world"]
    assert captured["prefer_observations"] is True
    assert captured["include_chunks"] is True
    assert captured["max_chunk_tokens"] == 19
    assert captured["tags"] == ["project:hindsight"]
    assert captured["tags_match"] == "all_strict"
    assert captured["min_scores"].final == 0.25
    assert captured["temporal_window"].start.isoformat() == "2026-07-01T00:00:00+00:00"
    assert captured["question_date"].isoformat() == "2026-09-16T12:00:00+00:00"
    assert captured["cluster_threshold"] == 0.72
