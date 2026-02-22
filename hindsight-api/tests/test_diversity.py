"""Tests for the diversity clustering module used by recall_exp."""

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from hindsight_api.engine.search.diversity import (
    ClusterRepresentative,
    cluster_and_select,
    strip_pipe_metadata,
)
from hindsight_api.engine.search.types import RetrievalResult

_UNSET = object()


def _make_result(
    text: str = "some fact",
    fact_type: str = "world",
    embedding: list[float] | None | object = _UNSET,
    occurred_start: datetime | None = None,
    mentioned_at: datetime | None = None,
    event_date: datetime | None = None,
) -> RetrievalResult:
    """Helper to create a RetrievalResult with a random embedding if none given."""
    if embedding is _UNSET:
        rng = np.random.default_rng()
        embedding = rng.standard_normal(384).tolist()
    return RetrievalResult(
        id=str(hash(text) % 10**8),
        text=text,
        fact_type=fact_type,
        embedding=embedding,
        occurred_start=occurred_start,
        mentioned_at=mentioned_at,
        event_date=event_date,
    )


# ---------- strip_pipe_metadata ----------


class TestStripPipeMetadata:
    def test_strips_when_suffix(self):
        text = "Igor is CTO | When: 2024-01-15 | Involving: Igor, OpenClaw"
        assert strip_pipe_metadata(text) == "Igor is CTO"

    def test_strips_when_only(self):
        assert strip_pipe_metadata("Fact text | When: yesterday") == "Fact text"

    def test_strips_involving_only(self):
        assert strip_pipe_metadata("Fact text | Involving: Alice, Bob") == "Fact text"

    def test_preserves_unlabelled_pipe(self):
        text = "Igor prefers functional programming | learned from code review"
        assert strip_pipe_metadata(text) == "Igor prefers functional programming | learned from code review"

    def test_no_pipe(self):
        assert strip_pipe_metadata("plain text") == "plain text"

    def test_empty(self):
        assert strip_pipe_metadata("") == ""

    def test_mixed_pipes(self):
        text = "Fact | When: 2024-01-01 | some reason"
        result = strip_pipe_metadata(text)
        assert "When:" not in result
        assert "some reason" in result


# ---------- cluster_and_select ----------


class TestClusterAndSelect:
    def test_empty_candidates(self):
        result = cluster_and_select([], [0.0] * 384)
        assert result == []

    def test_single_candidate(self):
        emb = np.random.default_rng(42).standard_normal(384).tolist()
        c = _make_result("only fact", embedding=emb)
        reps = cluster_and_select([c], emb)
        assert len(reps) == 1
        assert reps[0].result.text == "only fact"
        assert reps[0].cluster_size == 1

    def test_identical_embeddings_cluster_together(self):
        """Two candidates with identical embeddings should form one cluster."""
        emb = np.random.default_rng(42).standard_normal(384).tolist()
        c1 = _make_result("Igor is CTO", embedding=emb)
        c2 = _make_result("Igor is CTO of OpenClaw", embedding=emb)
        reps = cluster_and_select([c1, c2], emb)
        assert len(reps) == 1
        assert reps[0].cluster_size == 2

    def test_dissimilar_embeddings_stay_separate(self):
        """Two candidates with orthogonal embeddings should not cluster."""
        rng = np.random.default_rng(42)
        emb1 = rng.standard_normal(384).tolist()
        emb2 = rng.standard_normal(384).tolist()
        # Make them orthogonal
        v1 = np.array(emb1)
        v2 = np.array(emb2)
        v2 = v2 - (np.dot(v2, v1) / np.dot(v1, v1)) * v1  # Gram-Schmidt
        emb2 = v2.tolist()

        c1 = _make_result("Igor is CTO", embedding=emb1)
        c2 = _make_result("The weather is nice today", embedding=emb2)
        query = rng.standard_normal(384).tolist()
        reps = cluster_and_select([c1, c2], query)
        assert len(reps) == 2

    def test_observation_preferred_as_representative(self):
        """Observation should be preferred over world fact in same cluster."""
        emb = np.random.default_rng(42).standard_normal(384).tolist()
        world = _make_result("Igor is CTO", fact_type="world", embedding=emb)
        obs = _make_result("Igor is CTO and co-founder", fact_type="observation", embedding=emb)
        reps = cluster_and_select([world, obs], emb)
        assert len(reps) == 1
        assert reps[0].result.fact_type == "observation"

    def test_recency_influences_selection(self):
        """More recent candidate should be preferred when similarity is equal."""
        emb = np.random.default_rng(42).standard_normal(384).tolist()
        now = datetime.now(UTC)
        old = _make_result("Igor is CTO", embedding=emb, occurred_start=now - timedelta(days=300))
        recent = _make_result("Igor is CTO (confirmed)", embedding=emb, occurred_start=now - timedelta(days=1))
        reps = cluster_and_select([old, recent], emb)
        assert len(reps) == 1
        assert reps[0].result.text == "Igor is CTO (confirmed)"

    def test_candidates_without_embeddings_skipped(self):
        """Candidates without embeddings should be silently skipped."""
        emb = np.random.default_rng(42).standard_normal(384).tolist()
        c1 = _make_result("has embedding", embedding=emb)
        c2 = _make_result("no embedding", embedding=None)
        reps = cluster_and_select([c1, c2], emb)
        assert len(reps) == 1
        assert reps[0].result.text == "has embedding"

    def test_results_sorted_by_query_similarity(self):
        """Representatives should be sorted by query similarity descending."""
        rng = np.random.default_rng(42)
        query = rng.standard_normal(384).tolist()
        q_vec = np.array(query, dtype=np.float32)
        q_vec /= np.linalg.norm(q_vec)

        # Create candidates at different similarities to query
        close = (q_vec + rng.standard_normal(384) * 0.1).tolist()
        far = rng.standard_normal(384).tolist()

        c1 = _make_result("close to query", embedding=close)
        c2 = _make_result("far from query", embedding=far)
        reps = cluster_and_select([c2, c1], query)
        assert len(reps) == 2
        assert reps[0].query_similarity >= reps[1].query_similarity

    def test_date_fallback_chain(self):
        """Should use mentioned_at when occurred_start is None."""
        emb = np.random.default_rng(42).standard_normal(384).tolist()
        now = datetime.now(UTC)
        # Both have same embedding but different date sources
        c1 = _make_result(
            "fact with mentioned_at",
            embedding=emb,
            mentioned_at=now - timedelta(days=1),
        )
        c2 = _make_result(
            "fact with event_date only",
            embedding=emb,
            event_date=now - timedelta(days=300),
        )
        reps = cluster_and_select([c1, c2], emb)
        assert len(reps) == 1
        # c1 should be selected (more recent via mentioned_at)
        assert reps[0].result.text == "fact with mentioned_at"

    def test_threshold_controls_clustering(self):
        """Higher threshold should produce more clusters (less grouping)."""
        rng = np.random.default_rng(42)
        base = rng.standard_normal(384)
        base /= np.linalg.norm(base)
        # Create candidates with ~0.8 similarity to each other
        noise = rng.standard_normal(384) * 0.3
        emb1 = base.tolist()
        emb2 = (base + noise).tolist()

        c1 = _make_result("fact A", embedding=emb1)
        c2 = _make_result("fact B", embedding=emb2)
        query = rng.standard_normal(384).tolist()

        # Low threshold: should cluster together
        reps_low = cluster_and_select([c1, c2], query, similarity_threshold=0.5)
        # High threshold: should stay separate
        reps_high = cluster_and_select([c1, c2], query, similarity_threshold=0.99)

        assert len(reps_low) <= len(reps_high)
