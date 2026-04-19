"""[FORK] Resolve Codex model names, including auto-* magic values.

Codex removes older models periodically (e.g., `gpt-5.1-codex-mini` was
removed from ChatGPT-account Codex on ~2026-04-16). Pinning a specific
model in config means every removal requires a manual plist edit.

This resolver supports opt-in auto-selection via magic values:

    auto-latest-mini  → highest-version mini variant visible to the account
    auto-latest-codex → highest-version codex-tuned variant
    auto-latest       → highest-version model overall
    auto              → alias for auto-latest-mini

Selection reads `~/.codex/models_cache.json`, the same file the `codex`
CLI populates from the ChatGPT backend. Candidates must have:

    visibility == "list"        # user-facing on the plan
    supported_in_api == True    # can be called via the API

If the cache is missing, stale, or the magic value is unrecognized,
we fall back to the configured string as-is and log a warning.

Also exposes `suggest_replacement(model)` used by the circuit breaker
to print a human-recommended replacement in the permanent-disable log.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MODELS_CACHE = Path.home() / ".codex" / "models_cache.json"
_MAGIC_PREFIX = "auto"


def _parse_version(slug: str) -> tuple[int, ...]:
    """Extract a version tuple from a slug, e.g. 'gpt-5.4-mini' -> (5, 4)."""
    match = re.search(r"(\d+(?:\.\d+)*)", slug)
    if not match:
        return (0,)
    return tuple(int(x) for x in match.group(1).split("."))


def _load_cache() -> list[dict[str, Any]] | None:
    """Load Codex models cache. Returns None if missing/unreadable."""
    try:
        with _MODELS_CACHE.open() as f:
            data = json.load(f)
        return list(data.get("models") or [])
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Could not read Codex models cache at {_MODELS_CACHE}: {e}")
        return None


def _filter_available(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only models that are visible on the plan and supported by the API."""
    return [m for m in models if m.get("visibility") == "list" and m.get("supported_in_api")]


def _pick_latest(models: list[dict[str, Any]], category: str) -> str | None:
    """Pick the highest-version model matching a category.

    Categories:
        mini  — slug contains 'mini'
        codex — slug contains 'codex' and NOT 'mini' (codex-tuned full size)
        any   — no additional filter
    """
    if category == "mini":
        candidates = [m for m in models if "mini" in m["slug"]]
    elif category == "codex":
        candidates = [m for m in models if "codex" in m["slug"] and "mini" not in m["slug"]]
    else:
        candidates = list(models)
    if not candidates:
        return None
    candidates.sort(key=lambda m: _parse_version(m["slug"]), reverse=True)
    return candidates[0]["slug"]


def resolve_codex_model(configured: str) -> str:
    """Resolve a Codex model name, expanding `auto-*` magic values.

    If `configured` is not a magic value, returns it unchanged.
    If the cache is unavailable or no matching model exists, returns
    the magic value itself (which will surface as a clear error when
    the API rejects it).
    """
    if not configured or not configured.startswith(_MAGIC_PREFIX):
        return configured

    spec = configured.lower()
    if spec in ("auto", "auto-latest-mini"):
        category = "mini"
    elif spec == "auto-latest-codex":
        category = "codex"
    elif spec == "auto-latest":
        category = "any"
    else:
        logger.warning(f"Unknown Codex auto-* magic value: {configured!r} — passing through")
        return configured

    models = _load_cache()
    if not models:
        logger.warning(
            f"Codex models cache unavailable — cannot resolve {configured!r}. "
            f"Run the 'codex' CLI at least once to populate {_MODELS_CACHE}, "
            f"or set HINDSIGHT_API_PRIMARY_LLM_MODEL to an explicit model name."
        )
        return configured

    available = _filter_available(models)
    resolved = _pick_latest(available, category)
    if resolved is None:
        logger.warning(
            f"No Codex model matched category={category!r} in cache "
            f"({len(available)} available) — passing {configured!r} through"
        )
        return configured

    logger.info(f"Resolved Codex model {configured!r} → {resolved!r} (latest {category} on this account)")
    return resolved


def suggest_replacement(current_model: str) -> str | None:
    """Return a recommended replacement slug for a no-longer-supported model.

    Picks the most similar category (mini if current contains 'mini',
    codex if current contains 'codex', else latest overall).
    Returns None if the cache is unavailable or no replacement is found.
    """
    models = _load_cache()
    if not models:
        return None
    available = _filter_available(models)
    if not available:
        return None

    current_lower = (current_model or "").lower()
    if "mini" in current_lower:
        category = "mini"
    elif "codex" in current_lower:
        category = "codex"
    else:
        category = "any"

    return _pick_latest(available, category)
