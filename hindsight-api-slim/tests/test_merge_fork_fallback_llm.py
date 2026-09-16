"""Merge regressions for the fork fallback LLM wrapper."""

from __future__ import annotations

from typing import Any

import pytest

from hindsight_api.engine.providers.fallback_llm import CircuitBreaker, FallbackLLMProvider


class _FakeProvider:
    def __init__(self, *, provider: str, model: str, error: Exception | None = None) -> None:
        self.provider = provider
        self.model = model
        self.api_key = "test"
        self.base_url = "http://127.0.0.1:1/v1"
        self.reasoning_effort = "low"
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []

    async def call(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return f"{self.provider}:ok"

    async def call_with_tools(self, **kwargs: Any) -> str:
        self.tool_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return f"{self.provider}:tools-ok"

    def supports_prompt_caching(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_failed_primary_does_not_forward_primary_cached_prefix_to_fallback() -> None:
    primary = _FakeProvider(provider="primary", model="deprecated-model", error=RuntimeError("temporary outage"))
    fallback = _FakeProvider(provider="fallback", model="healthy")
    breaker = CircuitBreaker(failure_threshold=1)
    provider = FallbackLLMProvider(primary, fallback, breaker)

    result = await provider.call(messages=[{"role": "user", "content": "hi"}], cached_prefix="primary-cache")

    assert result == "fallback:ok"
    assert primary.calls[0]["cached_prefix"] == "primary-cache"
    assert fallback.calls[0]["cached_prefix"] is None


@pytest.mark.asyncio
async def test_open_circuit_keeps_fallback_cached_prefix_for_fallback_leg() -> None:
    primary = _FakeProvider(provider="primary", model="down")
    fallback = _FakeProvider(provider="fallback", model="healthy")
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.force_open("already routed to fallback")
    provider = FallbackLLMProvider(primary, fallback, breaker)

    result = await provider.call(messages=[{"role": "user", "content": "hi"}], cached_prefix="fallback-cache")

    assert result == "fallback:ok"
    assert primary.calls == []
    assert fallback.calls[0]["cached_prefix"] == "fallback-cache"


@pytest.mark.asyncio
async def test_failed_primary_tool_call_clears_cached_prefix_and_count_for_fallback() -> None:
    primary = _FakeProvider(provider="primary", model="deprecated-model", error=RuntimeError("temporary outage"))
    fallback = _FakeProvider(provider="fallback", model="healthy")
    provider = FallbackLLMProvider(primary, fallback, CircuitBreaker(failure_threshold=1))

    result = await provider.call_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        cached_prefix="primary-tool-cache",
        cached_prefix_message_count=3,
    )

    assert result == "fallback:tools-ok"
    assert primary.tool_calls[0]["cached_prefix"] == "primary-tool-cache"
    assert primary.tool_calls[0]["cached_prefix_message_count"] == 3
    assert fallback.tool_calls[0]["cached_prefix"] is None
    assert fallback.tool_calls[0]["cached_prefix_message_count"] == 0


def test_permanent_disable_survives_record_success() -> None:
    breaker = CircuitBreaker(failure_threshold=1)

    breaker.force_permanent_disable("model was deprecated")
    breaker.record_success()

    assert breaker.permanently_disabled is True
    assert breaker.should_try_primary() is False
    assert breaker.state == "open"
