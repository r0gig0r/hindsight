from __future__ import annotations

import uuid
import os
from datetime import datetime, timezone

import pytest

from hindsight_api.engine.memory_engine import Budget
from hindsight_api.engine.search.reranking import apply_combined_scoring
from hindsight_api.engine.search.structural import encode_roles
from hindsight_api.engine.search.types import MergedCandidate, RetrievalResult, ScoredResult
from tests.poc.run_holographic_memory_eval import load_cases, seed_case

os.environ.setdefault("HINDSIGHT_API_LLM_PROVIDER", "none")


def _bank(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_holographic_feedback_disabled_preserves_trust(memory, request_context):
    case = load_cases()[0]
    bank_id = _bank("holo_feedback_disabled")
    id_map = await seed_case(memory, bank_id, case)
    stale_id = id_map["h4_stale"]

    try:
        result = await memory.add_memory_feedback(
            bank_id,
            stale_id,
            "unhelpful",
            "eval",
            "disabled path should not mutate",
            request_context=request_context,
        )
        assert result["enabled"] is False

        pool = await memory._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT trust_score, helpful_count, unhelpful_count FROM memory_units WHERE id = $1",
                uuid.UUID(stale_id),
            )
            event_count = await conn.fetchval(
                "SELECT COUNT(*) FROM memory_feedback_events WHERE memory_unit_id = $1",
                uuid.UUID(stale_id),
            )
        assert float(row["trust_score"]) == 0.5
        assert row["helpful_count"] == 0
        assert row["unhelpful_count"] == 0
        assert event_count == 0
    finally:
        await memory.delete_bank(bank_id, request_context=request_context)


@pytest.mark.asyncio
async def test_holographic_feedback_enabled_updates_trust_and_event(memory, request_context):
    case = load_cases()[0]
    bank_id = _bank("holo_feedback_enabled")
    id_map = await seed_case(memory, bank_id, case)
    stale_id = id_map["h4_stale"]

    try:
        await memory._config_resolver.update_bank_config(
            bank_id,
            {"experimental_memory_feedback_enabled": True},
            request_context,
        )
        result = await memory.add_memory_feedback(
            bank_id,
            stale_id,
            "unhelpful",
            "eval",
            "stale Python version",
            request_context=request_context,
        )
        assert result["enabled"] is True
        assert result["unhelpful_count"] == 1
        assert result["trust_score"] < 0.5

        pool = await memory._get_pool()
        async with pool.acquire() as conn:
            event_count = await conn.fetchval(
                "SELECT COUNT(*) FROM memory_feedback_events WHERE memory_unit_id = $1",
                uuid.UUID(stale_id),
            )
        assert event_count == 1
    finally:
        await memory.delete_bank(bank_id, request_context=request_context)


def test_holographic_trust_rerank_only_when_enabled():
    low = RetrievalResult(id="a", text="stale", fact_type="world", trust_score=0.05)
    high = RetrievalResult(id="b", text="trusted", fact_type="world", trust_score=0.95)
    disabled = [
        ScoredResult(MergedCandidate(low, rrf_score=1.0), cross_encoder_score_normalized=0.8),
        ScoredResult(MergedCandidate(high, rrf_score=1.0), cross_encoder_score_normalized=0.8),
    ]
    apply_combined_scoring(disabled, now=datetime.now(timezone.utc))
    assert disabled[0].weight == disabled[1].weight

    enabled = [
        ScoredResult(MergedCandidate(low, rrf_score=1.0), cross_encoder_score_normalized=0.8),
        ScoredResult(MergedCandidate(high, rrf_score=1.0), cross_encoder_score_normalized=0.8),
    ]
    apply_combined_scoring(
        enabled,
        now=datetime.now(timezone.utc),
        trust_rerank_enabled=True,
    )
    assert enabled[1].weight > enabled[0].weight


@pytest.mark.asyncio
async def test_holographic_entity_probe_and_reason(memory, request_context):
    case = load_cases()[4]
    bank_id = _bank("holo_entity")
    await seed_case(memory, bank_id, case)

    try:
        disabled = await memory.entity_probe(bank_id, "Hindsight", request_context=request_context)
        assert disabled["enabled"] is False

        await memory._config_resolver.update_bank_config(
            bank_id,
            {"experimental_entity_tools_enabled": True},
            request_context,
        )
        probe = await memory.entity_probe(bank_id, "Hindsight", request_context=request_context)
        assert probe["matched"] is True
        assert any("Hindsight" in item["text"] for item in probe["results"])

        reason = await memory.entity_reason(
            bank_id,
            ["Hindsight", "OpenClaw"],
            request_context=request_context,
            limit=5,
        )
        assert reason["matched"] is True
        assert all("Qubino" not in item["text"] for item in reason["results"])
        assert any("OpenClaw" in item["text"] for item in reason["results"])
    finally:
        await memory.delete_bank(bank_id, request_context=request_context)


@pytest.mark.asyncio
async def test_holographic_conflict_detection_finds_python_versions(memory, request_context):
    case = load_cases()[0]
    bank_id = _bank("holo_conflicts")
    await seed_case(memory, bank_id, case)

    try:
        disabled = await memory.memory_conflicts(bank_id, request_context=request_context)
        assert disabled["enabled"] is False

        await memory._config_resolver.update_bank_config(
            bank_id,
            {"experimental_conflict_detection_enabled": True},
            request_context,
        )
        conflicts = await memory.memory_conflicts(bank_id, request_context=request_context)
        joined = "\n".join(c["text_a"] + "\n" + c["text_b"] for c in conflicts["conflicts"])
        assert "Python 3.13" in joined
        assert "Python 3.14" in joined
    finally:
        await memory.delete_bank(bank_id, request_context=request_context)


@pytest.mark.asyncio
async def test_holographic_structural_deterministic_and_bypassed(memory, request_context, monkeypatch):
    assert encode_roles({"entities": ["Hindsight"], "fact_type": "world"}) == encode_roles(
        {"fact_type": "world", "entities": ["Hindsight"]}
    )

    case = load_cases()[4]
    bank_id = _bank("holo_structural_bypass")
    await seed_case(memory, bank_id, case)

    async def explode(*args, **kwargs):
        raise AssertionError("structural retrieval should be bypassed when disabled")

    monkeypatch.setattr(memory, "_retrieve_structural_candidates", explode)
    try:
        result = await memory.recall_async(
            bank_id=bank_id,
            query=case["query"],
            fact_type=["world", "experience"],
            budget=Budget.LOW,
            max_tokens=case["max_injected_tokens"],
            request_context=request_context,
        )
        assert result.results
    finally:
        await memory.delete_bank(bank_id, request_context=request_context)
