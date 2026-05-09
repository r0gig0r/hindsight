"""Deterministic structural vectors for experimental holographic recall.

This is intentionally isolated from semantic embeddings. It encodes coarse
memory structure into a small HRR-style vector using only existing facts:
entities, fact type, tags, temporal bucket, and source document id.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

DIMENSIONS = 64
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{1,}")


def tokenize_structure_text(text: str, *, limit: int = 12) -> list[str]:
    """Return stable, low-cardinality tokens for structural query encoding."""
    seen: set[str] = set()
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text.lower()):
        token = match.group(0).strip("._:-")
        if len(token) < 3 or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= limit:
            break
    return tokens


def temporal_bucket(value: datetime | str | None) -> str | None:
    """Bucket a datetime into a month-level structural role."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return f"{value.year:04d}-{value.month:02d}"


def _atom(label: str) -> list[float]:
    digest = hashlib.sha256(label.encode("utf-8")).digest()
    values: list[float] = []
    seed = digest
    while len(values) < DIMENSIONS:
        for byte in seed:
            angle = (byte / 255.0) * math.tau
            values.append(math.cos(angle))
            if len(values) >= DIMENSIONS:
                break
        seed = hashlib.sha256(seed).digest()
    return _normalize(values)


def _normalize(values: Iterable[float]) -> list[float]:
    result = [float(v) for v in values]
    norm = math.sqrt(sum(v * v for v in result))
    if norm == 0.0:
        return [0.0 for _ in result]
    return [v / norm for v in result]


def encode_roles(roles: Mapping[str, Any]) -> list[float]:
    """Encode structural roles into a deterministic HRR-style phase vector."""
    bundled = [0.0] * DIMENSIONS
    for role, raw_values in sorted(roles.items()):
        if raw_values is None:
            continue
        if isinstance(raw_values, str):
            values = [raw_values]
        else:
            values = list(raw_values)
        role_vec = _atom(f"role:{role}")
        for value in values:
            if value is None:
                continue
            value_text = str(value).strip().lower()
            if not value_text:
                continue
            value_vec = _atom(f"value:{value_text}")
            for idx in range(DIMENSIONS):
                bundled[idx] += role_vec[idx] * value_vec[idx]
    return _normalize(bundled)


def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    left_values = [float(v) for v in left]
    right_values = [float(v) for v in right]
    if not left_values or not right_values or len(left_values) != len(right_values):
        return 0.0
    left_norm = math.sqrt(sum(v * v for v in left_values))
    right_norm = math.sqrt(sum(v * v for v in right_values))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(left_values, right_values)) / (left_norm * right_norm)
