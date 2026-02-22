"""
Diversity-based result selection for recall_exp.

Clusters semantically similar candidates via KNN connected components,
then selects one representative per cluster to eliminate redundancy.
"""

import math
import re
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np

from .types import RetrievalResult

# Strips "| When: ..." and "| Involving: ..." pipe metadata from fact text.
# Preserves trailing "| <reason>" segments (no label prefix).
_PIPE_METADATA_RE = re.compile(r"\s*\|\s*(?:When|Involving):\s*[^|]*")


@dataclass
class ClusterRepresentative:
    """A selected representative from a cluster of similar results."""

    result: RetrievalResult
    cluster_id: int
    cluster_size: int
    query_similarity: float


def strip_pipe_metadata(text: str) -> str:
    """Remove ``| When: ...`` and ``| Involving: ...`` suffixes from fact text."""
    return _PIPE_METADATA_RE.sub("", text).strip()


def _best_date(result: RetrievalResult) -> datetime | None:
    """Return the best available date using fallback chain: occurred_start → mentioned_at → event_date."""
    return result.occurred_start or result.mentioned_at or result.event_date


def _connected_components(adjacency: np.ndarray) -> list[list[int]]:
    """Find connected components via BFS on a boolean adjacency matrix."""
    n = adjacency.shape[0]
    visited = [False] * n
    components: list[list[int]] = []
    for start in range(n):
        if visited[start]:
            continue
        component: list[int] = []
        queue = deque([start])
        visited[start] = True
        while queue:
            node = queue.popleft()
            component.append(node)
            for neighbor in range(n):
                if not visited[neighbor] and adjacency[node, neighbor]:
                    visited[neighbor] = True
                    queue.append(neighbor)
        components.append(component)
    return components


def cluster_and_select(
    candidates: list[RetrievalResult],
    query_embedding: list[float],
    similarity_threshold: float = 0.75,
) -> list[ClusterRepresentative]:
    """
    Cluster candidates by cosine similarity and pick one representative per cluster.

    Args:
        candidates: Retrieval results with embeddings.
        query_embedding: The query's embedding vector.
        similarity_threshold: Cosine similarity above which two candidates are
            considered redundant (connected in the KNN graph).

    Returns:
        Representatives sorted by query similarity descending.
    """
    if not candidates:
        return []

    # Filter out candidates without embeddings
    valid = [(i, c) for i, c in enumerate(candidates) if c.embedding is not None]
    if not valid:
        return []

    indices, valid_candidates = zip(*valid, strict=True)

    # Build embedding matrix and L2-normalize
    # pgvector returns embeddings as strings like "[0.1,0.2,...]" — parse if needed
    def _parse_embedding(emb: Any) -> list[float]:
        if isinstance(emb, str):
            return [float(x) for x in emb.strip("[]").split(",")]
        return emb

    emb_matrix = np.array([_parse_embedding(c.embedding) for c in valid_candidates], dtype=np.float32)
    norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-10)
    emb_matrix /= norms

    # Pairwise cosine similarity and threshold
    sim_matrix = emb_matrix @ emb_matrix.T
    adjacency = sim_matrix >= similarity_threshold

    # Query similarity for each candidate
    q_vec = np.array(query_embedding, dtype=np.float32)
    q_norm = np.linalg.norm(q_vec)
    if q_norm > 1e-10:
        q_vec /= q_norm
    query_sims = emb_matrix @ q_vec

    # Connected components
    components = _connected_components(adjacency)

    now = datetime.now(UTC)
    representatives: list[ClusterRepresentative] = []

    for cluster_id, component in enumerate(components):
        best_idx = -1
        best_score = -float("inf")

        for idx in component:
            candidate = valid_candidates[idx]
            q_sim = float(query_sims[idx])

            # Fact type bonus
            ft = candidate.fact_type
            type_bonus = 0.3 if ft == "observation" else (0.2 if ft == "experience" else 0.0)

            # Text length bonus
            length_bonus = min(0.1, math.log1p(len(candidate.text)) / 70)

            # Recency bonus
            dt = _best_date(candidate)
            if dt is not None:
                days_ago = max(0.0, (now - dt).total_seconds() / 86400)
                recency = max(0.05, 1.0 - days_ago / 365)
            else:
                recency = 0.05

            score = q_sim + type_bonus + length_bonus + recency

            if score > best_score:
                best_score = score
                best_idx = idx

        representatives.append(
            ClusterRepresentative(
                result=valid_candidates[best_idx],
                cluster_id=cluster_id,
                cluster_size=len(component),
                query_similarity=float(query_sims[best_idx]),
            )
        )

    # Sort by query similarity descending
    representatives.sort(key=lambda r: r.query_similarity, reverse=True)
    return representatives
