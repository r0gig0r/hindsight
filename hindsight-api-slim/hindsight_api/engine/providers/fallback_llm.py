"""
Fallback LLM provider with circuit breaker pattern.

Wraps a primary LLMProvider (e.g., claude-code) with automatic failover
to a fallback LLMProvider (e.g., OpenRouter) when the primary is unavailable.

The circuit breaker prevents repeated slow failures by short-circuiting
to the fallback after a configurable number of consecutive failures.
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Env vars that corrupt the Claude Agent SDK JSON-over-stdio protocol when
# inherited by the spawned CLI subprocess.
# - DEBUG=* (SDK issue #347): Node.js debug output mixed into JSON stream
# - ANTHROPIC_LOG=debug: May cause verbose logging on stdio
_TOXIC_ENV_VARS = ("DEBUG", "ANTHROPIC_LOG")
_env_sanitized = False


def _sanitize_env_for_claude_sdk() -> None:
    """Permanently remove env vars that break the Claude Agent SDK subprocess.

    Called once at FallbackLLMProvider init. The LaunchAgent plist sets DEBUG=*
    and ANTHROPIC_LOG=debug for the gateway. These get inherited by the daemon
    and then by every Claude CLI subprocess spawned by the SDK. DEBUG=* causes
    debug output to corrupt the JSON-over-stdio protocol, leading to
    'Control request timeout: initialize' errors.

    Also sets CLAUDE_CODE_STREAM_CLOSE_TIMEOUT for macOS EINTR fix (SDK #541).
    """
    global _env_sanitized  # noqa: PLW0603
    if _env_sanitized:
        return
    for var in _TOXIC_ENV_VARS:
        old = os.environ.pop(var, None)
        if old is not None:
            logger.info(f"Removed toxic env var {var}={old!r} (breaks Claude Agent SDK)")
    # macOS EINTR workaround (SDK #541)
    if "CLAUDE_CODE_STREAM_CLOSE_TIMEOUT" not in os.environ:
        os.environ["CLAUDE_CODE_STREAM_CLOSE_TIMEOUT"] = "180000"
        logger.info("Set CLAUDE_CODE_STREAM_CLOSE_TIMEOUT=180000 (macOS EINTR fix)")
    _env_sanitized = True


# Error substrings that indicate auth failures — these won't recover without
# manual intervention, so we immediately open the circuit.
_AUTH_ERROR_MARKERS = frozenset({"auth", "login", "credential", "unauthorized", "forbidden", "401", "403"})

# [FORK] Error substrings indicating the primary is permanently misconfigured
# (e.g., configured model was removed/deprecated). These will NEVER recover
# without operator action, so probing every 5 min wastes calls and latency.
# On match we permanently disable the primary until daemon restart.
_PERMANENT_ERROR_MARKERS = frozenset(
    {
        "model is not supported",
        "model not supported",
        "model_not_found",
        "model not found",
        "does not exist",
        "invalid model",
        "model_deprecated",
        "deprecated model",
    }
)

# Max seconds to wait for a primary call before treating it as failed.
# Successful calls average ~21s (retain) and ~17s (consolidation), but can
# spike to 40s+ under load.  30s caused ~50% false-positive timeouts;
# 60s gives headroom while still failing faster than the SDK's own timeout.
_PRIMARY_CALL_TIMEOUT = 60.0


@dataclass
class CircuitBreaker:
    """
    Simple circuit breaker for LLM failover.

    States:
        closed   — primary is healthy, use it for every call
        open     — primary is broken, skip it entirely
        half_open — cooldown elapsed, probe primary with one call
    """

    failure_threshold: int = 3
    cooldown_seconds: float = 300.0  # 5 minutes

    # Mutable state (not constructor args)
    state: str = field(default="closed", init=False)
    failure_count: int = field(default=0, init=False)
    last_failure_time: float = field(default=0.0, init=False)
    # [FORK] Permanently disabled until daemon restart (e.g., model was removed)
    permanently_disabled: bool = field(default=False, init=False)
    permanent_reason: str = field(default="", init=False)

    def record_failure(self) -> None:
        """Record a primary provider failure."""
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            if self.state != "open":
                logger.warning(
                    f"Circuit breaker OPEN after {self.failure_count} failures (threshold={self.failure_threshold})"
                )
            self.state = "open"

    def record_success(self) -> None:
        """Record a primary provider success — reset to closed."""
        if self.state != "closed":
            logger.info(f"Circuit breaker CLOSED (was {self.state}) — primary recovered")
        self.state = "closed"
        self.failure_count = 0
        # Restore default cooldown if it was overridden by a rate limit
        if hasattr(self, "_original_cooldown"):
            self.cooldown_seconds = self._original_cooldown
            del self._original_cooldown

    def force_permanent_disable(self, reason: str) -> None:
        """[FORK] Permanently disable the primary provider until daemon restart.

        Use for errors that will never recover on their own — e.g., the
        configured model was removed from the upstream service. Probing
        under these conditions is a guaranteed wasted call every cooldown.
        """
        self.state = "open"
        self.failure_count = self.failure_threshold
        self.last_failure_time = time.monotonic()
        self.permanently_disabled = True
        self.permanent_reason = reason
        logger.error(
            f"Circuit breaker PERMANENTLY DISABLED: {reason}. "
            f"Primary LLM will not be retried until daemon restart. "
            f"Check HINDSIGHT_API_PRIMARY_LLM_MODEL in gateway plist."
        )

    def force_open(self, reason: str = "", cooldown_override: float | None = None) -> None:
        """Immediately open the circuit (e.g., on auth or rate limit errors).

        Args:
            reason: Human-readable reason for opening.
            cooldown_override: If set, temporarily override cooldown_seconds
                (e.g., to match a rate limit reset window). Resets to default
                on next record_success().
        """
        self.state = "open"
        self.failure_count = self.failure_threshold
        self.last_failure_time = time.monotonic()
        if cooldown_override is not None and cooldown_override > self.cooldown_seconds:
            self._original_cooldown = self.cooldown_seconds
            self.cooldown_seconds = cooldown_override
            logger.warning(f"Circuit breaker FORCE-OPENED: {reason} (cooldown={cooldown_override:.0f}s)")
        else:
            logger.warning(f"Circuit breaker FORCE-OPENED: {reason}")

    def should_try_primary(self) -> bool:
        """Return True if we should attempt the primary provider."""
        # [FORK] Permanent disable short-circuits all probing
        if self.permanently_disabled:
            return False
        if self.state == "closed":
            return True
        if self.state == "open":
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= self.cooldown_seconds:
                self.state = "half_open"
                logger.info(f"Circuit breaker HALF-OPEN after {elapsed:.0f}s cooldown — probing primary")
                return True
            return False
        # half_open — probe once
        return True


def _is_auth_error(exc: BaseException) -> bool:
    """Check if an exception looks like an authentication/authorization error."""
    error_str = str(exc).lower()
    return any(marker in error_str for marker in _AUTH_ERROR_MARKERS)


def _is_permanent_error(exc: BaseException) -> bool:
    """[FORK] Check if an exception indicates the primary is permanently misconfigured.

    Permanent errors (e.g., "model is not supported") will never recover
    without operator action. Repeated probing wastes calls and latency.
    """
    error_str = str(exc).lower()
    return any(marker in error_str for marker in _PERMANENT_ERROR_MARKERS)


def _is_rate_limit_error(exc: BaseException) -> float | None:
    """Check if an exception is a rate limit error with a known reset time.

    Returns the reset duration in seconds, or None if not a rate limit error.
    Detects rate limits via string matching (upstream removed CodexRateLimitError).
    """
    exc_str = str(exc)
    if "429" in exc_str or "usage_limit_reached" in exc_str or "rate_limit" in exc_str.lower():
        # Try to extract reset time from exception message
        import re

        match = re.search(r"resets?\s*(?:in\s*)?(\d+(?:\.\d+)?)\s*s", exc_str, re.IGNORECASE)
        return float(match.group(1)) if match else 300.0  # Default 5 min cooldown
    return None


class FallbackLLMProvider:
    """
    LLM provider wrapper that tries a primary provider first, falling back
    to a secondary provider on failure, governed by a circuit breaker.

    Duck-types the LLMProvider interface (call, call_with_tools, verify_connection,
    provider, model, api_key, base_url properties).
    """

    def __init__(
        self,
        primary: Any,  # LLMProvider / LLMConfig
        fallback: Any,  # LLMProvider / LLMConfig
        circuit_breaker: CircuitBreaker,
    ):
        self._primary = primary
        self._fallback = fallback
        self._circuit_breaker = circuit_breaker
        # Permanently clean env for Claude Agent SDK compatibility
        _sanitize_env_for_claude_sdk()

    # --- Properties delegated to the active provider ---

    @property
    def provider(self) -> str:
        if self._circuit_breaker.state == "closed":
            return self._primary.provider
        return self._fallback.provider

    @property
    def model(self) -> str:
        if self._circuit_breaker.state == "closed":
            return self._primary.model
        return self._fallback.model

    @property
    def api_key(self) -> str:
        if self._circuit_breaker.state == "closed":
            return self._primary.api_key
        return self._fallback.api_key

    @property
    def base_url(self) -> str:
        if self._circuit_breaker.state == "closed":
            return self._primary.base_url
        return self._fallback.base_url

    @property
    def _client(self) -> Any:
        """Backward compat: delegate to fallback (primary may not have _client)."""
        return self._fallback._client

    @property
    def reasoning_effort(self) -> str:
        return self._fallback.reasoning_effort

    @property
    def is_fallback_active(self) -> bool:
        """True when calls will definitely use the fallback provider (circuit open, not yet cooled down)."""
        return self._circuit_breaker.state == "open"

    def _build_permanent_reason(self, exc: BaseException) -> str:
        """[FORK] Compose the permanent-disable reason, including a
        replacement suggestion when we can determine one.
        """
        base = f"Primary {self._primary.provider}/{self._primary.model}: {exc}"
        if self._primary.provider.lower() != "openai-codex":
            return base
        try:
            from .codex_model_resolver import suggest_replacement

            replacement = suggest_replacement(self._primary.model)
        except Exception:  # resolver must never break the error path
            replacement = None
        if replacement and replacement != self._primary.model:
            return (
                f"{base}. Suggested replacement from ~/.codex/models_cache.json: "
                f"{replacement!r} (or set HINDSIGHT_API_PRIMARY_LLM_MODEL=auto-latest-mini)"
            )
        return base

    def with_config(self, config: Any) -> Any:
        """Return a ConfiguredLLMProvider wrapping this fallback provider."""
        from ..llm_wrapper import ConfiguredLLMProvider

        gemini_settings = getattr(config, "llm_gemini_safety_settings", None)
        return ConfiguredLLMProvider(self, gemini_settings)

    # --- Core LLM methods with failover ---

    async def call(
        self,
        messages: list[dict[str, str]],
        response_format: Any | None = None,
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        scope: str = "memory",
        max_retries: int = 10,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
        skip_validation: bool = False,
        strict_schema: bool = False,
        return_usage: bool = False,
    ) -> Any:
        if self._circuit_breaker.should_try_primary():
            try:
                result = await asyncio.wait_for(
                    self._primary.call(
                        messages=messages,
                        response_format=response_format,
                        max_completion_tokens=max_completion_tokens,
                        temperature=temperature,
                        scope=scope,
                        max_retries=0,  # Single attempt — fail fast
                        initial_backoff=initial_backoff,
                        max_backoff=max_backoff,
                        skip_validation=skip_validation,
                        strict_schema=strict_schema,
                        return_usage=return_usage,
                    ),
                    timeout=_PRIMARY_CALL_TIMEOUT,
                )
                self._circuit_breaker.record_success()
                return result
            except asyncio.TimeoutError:
                self._circuit_breaker.record_failure()
                logger.warning(
                    f"Primary LLM timed out after {_PRIMARY_CALL_TIMEOUT}s (scope={scope}) "
                    f"— falling back to {self._fallback.provider}/{self._fallback.model}"
                )
            except Exception as e:
                if _is_permanent_error(e):
                    self._circuit_breaker.force_permanent_disable(
                        self._build_permanent_reason(e),
                    )
                elif _is_auth_error(e):
                    self._circuit_breaker.force_open(f"Auth error: {e}")
                else:
                    reset_seconds = _is_rate_limit_error(e)
                    if reset_seconds is not None:
                        self._circuit_breaker.force_open(
                            f"Rate limit: resets in {reset_seconds:.0f}s",
                            cooldown_override=reset_seconds,
                        )
                    else:
                        self._circuit_breaker.record_failure()
                logger.warning(
                    f"Primary LLM failed (scope={scope}): {e!r} — falling back to "
                    f"{self._fallback.provider}/{self._fallback.model}"
                )

        # Use fallback with original retry params
        return await self._fallback.call(
            messages=messages,
            response_format=response_format,
            max_completion_tokens=max_completion_tokens,
            temperature=temperature,
            scope=scope,
            max_retries=max_retries,
            initial_backoff=initial_backoff,
            max_backoff=max_backoff,
            skip_validation=skip_validation,
            strict_schema=strict_schema,
            return_usage=return_usage,
        )

    async def call_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        scope: str = "tools",
        max_retries: int = 5,
        initial_backoff: float = 1.0,
        max_backoff: float = 30.0,
        tool_choice: str | dict[str, Any] = "auto",
    ) -> Any:
        if self._circuit_breaker.should_try_primary():
            try:
                result = await asyncio.wait_for(
                    self._primary.call_with_tools(
                        messages=messages,
                        tools=tools,
                        max_completion_tokens=max_completion_tokens,
                        temperature=temperature,
                        scope=scope,
                        max_retries=0,  # Single attempt — fail fast
                        initial_backoff=initial_backoff,
                        max_backoff=max_backoff,
                        tool_choice=tool_choice,
                    ),
                    timeout=_PRIMARY_CALL_TIMEOUT,
                )
                self._circuit_breaker.record_success()
                return result
            except asyncio.TimeoutError:
                self._circuit_breaker.record_failure()
                logger.warning(
                    f"Primary LLM tool call timed out after {_PRIMARY_CALL_TIMEOUT}s (scope={scope}) "
                    f"— falling back to {self._fallback.provider}/{self._fallback.model}"
                )
            except Exception as e:
                if _is_permanent_error(e):
                    self._circuit_breaker.force_permanent_disable(
                        self._build_permanent_reason(e),
                    )
                elif _is_auth_error(e):
                    self._circuit_breaker.force_open(f"Auth error: {e}")
                else:
                    reset_seconds = _is_rate_limit_error(e)
                    if reset_seconds is not None:
                        self._circuit_breaker.force_open(
                            f"Rate limit: resets in {reset_seconds:.0f}s",
                            cooldown_override=reset_seconds,
                        )
                    else:
                        self._circuit_breaker.record_failure()
                logger.warning(
                    f"Primary LLM tool call failed (scope={scope}): {e!r} — falling back to "
                    f"{self._fallback.provider}/{self._fallback.model}"
                )

        return await self._fallback.call_with_tools(
            messages=messages,
            tools=tools,
            max_completion_tokens=max_completion_tokens,
            temperature=temperature,
            scope=scope,
            max_retries=max_retries,
            initial_backoff=initial_backoff,
            max_backoff=max_backoff,
            tool_choice=tool_choice,
        )

    async def verify_connection(self) -> None:
        """Verify fallback always; try primary but don't penalize circuit on verification failure.

        The Claude Agent SDK initialization can be slow (60s+ timeout) in daemon contexts.
        We don't want a slow verification to open the circuit before any real calls happen.
        Let actual call() failures determine circuit state instead.
        """
        # Always verify fallback first — it must work
        await self._fallback.verify_connection()
        logger.info(f"Fallback LLM verified: {self._fallback.provider}/{self._fallback.model}")

        # Try primary verification but DON'T record failure in circuit breaker.
        # Verification timeouts (e.g., Claude Agent SDK init) are transient —
        # actual calls may work fine once the SDK warms up.
        try:
            await asyncio.wait_for(self._primary.verify_connection(), timeout=_PRIMARY_CALL_TIMEOUT)
            logger.info(f"Primary LLM verified: {self._primary.provider}/{self._primary.model}")
        except asyncio.TimeoutError:
            logger.warning(
                f"Primary LLM verification timed out after {_PRIMARY_CALL_TIMEOUT}s (will retry on first real call)"
            )
        except Exception as e:
            logger.warning(f"Primary LLM verification failed (will retry on first real call): {e!r}")

    async def cleanup(self) -> None:
        """Clean up both providers."""
        await self._primary.cleanup()
        await self._fallback.cleanup()
