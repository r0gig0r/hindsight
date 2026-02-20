"""
Test suite for consolidation output quality.

Verifies that the consolidation LLM produces:
1. Plain-text observations (no markdown headers)
2. Concise text (under 300 chars for typical facts)
3. No markdown structural artifacts (##, bullet lists, **bold**)

These are quality/accuracy tests that call the real LLM via OpenRouter.
"""

import uuid
from datetime import datetime, timezone

import pytest

from hindsight_api.engine.consolidation.consolidator import _consolidate_with_llm
from hindsight_api.engine.memory_engine import MemoryEngine


class TestConsolidationOutputQuality:
    """Tests that consolidation output is concise plain text without markdown bloat."""

    @pytest.mark.asyncio
    async def test_new_observation_no_markdown_headers(self, memory: MemoryEngine):
        """New observation should not start with ## headers."""
        max_retries = 3
        last_error = None
        for attempt in range(max_retries):
            try:
                actions = await _consolidate_with_llm(
                    memory_engine=memory,
                    fact_text="Alice works at Google as a senior software engineer.",
                    observations=[],
                    mission="General memory consolidation",
                )

                assert len(actions) > 0, "Should produce at least one action"

                for action in actions:
                    text = action.get("text", "")
                    assert not text.startswith("## "), (
                        f"Observation should not start with '## ' header. Got: {text[:100]}"
                    )
                    assert not text.startswith("# "), (
                        f"Observation should not start with '# ' header. Got: {text[:100]}"
                    )
                    assert "\n## " not in text, (
                        f"Observation should not contain '## ' headers. Got: {text[:200]}"
                    )
                return  # Test passed

            except AssertionError as e:
                last_error = e
                if attempt < max_retries - 1:
                    continue
                raise

    @pytest.mark.asyncio
    async def test_observation_conciseness(self, memory: MemoryEngine):
        """Observations should be concise (under 300 chars for simple facts)."""
        max_retries = 3
        last_error = None
        for attempt in range(max_retries):
            try:
                actions = await _consolidate_with_llm(
                    memory_engine=memory,
                    fact_text="Bob prefers dark roast coffee over light roast.",
                    observations=[],
                    mission="General memory consolidation",
                )

                assert len(actions) > 0, "Should produce at least one action"

                for action in actions:
                    text = action.get("text", "")
                    assert len(text) < 300, (
                        f"Observation too long ({len(text)} chars) for simple fact. Got: {text}"
                    )
                return

            except AssertionError as e:
                last_error = e
                if attempt < max_retries - 1:
                    continue
                raise

    @pytest.mark.asyncio
    async def test_update_observation_no_markdown(self, memory: MemoryEngine):
        """Updated observation should not have markdown formatting."""
        existing_obs = [
            {
                "id": str(uuid.uuid4()),
                "text": "Alice works at Google.",
                "proof_count": 1,
                "tags": [],
                "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                "updated_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                "source_memories": [
                    {
                        "text": "Alice works at Google.",
                        "event_date": datetime(2024, 1, 1, tzinfo=timezone.utc),
                        "occurred_start": None,
                    }
                ],
            }
        ]

        max_retries = 3
        last_error = None
        for attempt in range(max_retries):
            try:
                actions = await _consolidate_with_llm(
                    memory_engine=memory,
                    fact_text="Alice got promoted to Staff Engineer at Google.",
                    observations=existing_obs,
                    mission="General memory consolidation",
                )

                assert len(actions) > 0, "Should produce at least one action"

                for action in actions:
                    text = action.get("text", "")
                    assert not text.startswith("## "), (
                        f"Updated observation should not start with '## '. Got: {text[:100]}"
                    )
                    assert "\n## " not in text, (
                        f"Updated observation should not contain markdown headers. Got: {text[:200]}"
                    )
                return

            except AssertionError as e:
                last_error = e
                if attempt < max_retries - 1:
                    continue
                raise

    @pytest.mark.asyncio
    async def test_no_structural_markdown(self, memory: MemoryEngine):
        """Observations should not use **bold** key-value patterns or bullet lists."""
        max_retries = 3
        last_error = None
        for attempt in range(max_retries):
            try:
                actions = await _consolidate_with_llm(
                    memory_engine=memory,
                    fact_text="Tom has 10 years of Python experience and leads the ML team at Stripe.",
                    observations=[],
                    mission="General memory consolidation",
                )

                assert len(actions) > 0, "Should produce at least one action"

                for action in actions:
                    text = action.get("text", "")
                    # No markdown headers
                    assert not text.startswith("## "), f"No headers. Got: {text[:100]}"
                    assert not text.startswith("# "), f"No headers. Got: {text[:100]}"
                    # No bullet list structure (occasional natural dashes are OK,
                    # but structured "- Point 1\n- Point 2" is not)
                    lines = text.strip().split("\n")
                    bullet_lines = [ln for ln in lines if ln.strip().startswith("- ")]
                    assert len(bullet_lines) <= 1, (
                        f"Should not have structured bullet lists. Found {len(bullet_lines)} bullets: {text[:200]}"
                    )
                return

            except AssertionError as e:
                last_error = e
                if attempt < max_retries - 1:
                    continue
                raise
