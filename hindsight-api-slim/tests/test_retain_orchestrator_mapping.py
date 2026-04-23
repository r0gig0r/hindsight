"""Unit tests for retain orchestrator mapping and embeddings length guarantee.

Regression coverage for issue #1037: a silent length mismatch between the
extracted facts and the generated embeddings caused
`_map_results_to_contents` to raise IndexError during batch_retain.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from hindsight_api.engine.retain import embedding_utils
from hindsight_api.engine.retain.orchestrator import _map_results_to_contents, _remap_phase1_results
from hindsight_api.engine.retain.types import ProcessedFact, RetainContent


def _make_processed_fact(content_index: int, text: str = "fact") -> ProcessedFact:
    return ProcessedFact(
        fact_text=text,
        fact_type="world",
        embedding=[0.0, 0.0, 0.0],
        occurred_start=None,
        occurred_end=None,
        mentioned_at=datetime(2026, 1, 1),
        context="",
        metadata={},
        content_index=content_index,
    )


def _make_content(text: str = "x") -> RetainContent:
    return RetainContent(content=text)


class TestMapResultsToContents:
    def test_groups_unit_ids_by_content_index(self):
        contents = [_make_content("a"), _make_content("b"), _make_content("c")]
        processed = [
            _make_processed_fact(0, "a1"),
            _make_processed_fact(0, "a2"),
            _make_processed_fact(2, "c1"),
        ]
        unit_ids = ["u-a1", "u-a2", "u-c1"]

        result = _map_results_to_contents(contents, processed, unit_ids)

        assert result == [["u-a1", "u-a2"], [], ["u-c1"]]

    def test_handles_out_of_range_content_index(self):
        contents = [_make_content("a"), _make_content("b")]
        processed = [
            _make_processed_fact(-1, "f1"),
            _make_processed_fact(99, "f2"),
        ]
        unit_ids = ["u1", "u2"]

        result = _map_results_to_contents(contents, processed, unit_ids)

        assert result == [["u1"], ["u2"]]

    def test_empty_inputs(self):
        assert _map_results_to_contents([], [], []) == []

    def test_length_mismatch_raises(self):
        # Regression for #1037: previously the function silently overran unit_ids.
        contents = [_make_content("a")]
        processed = [_make_processed_fact(0), _make_processed_fact(0)]
        unit_ids = ["u1"]  # one fewer than processed_facts

        with pytest.raises(ValueError, match="length mismatch"):
            _map_results_to_contents(contents, processed, unit_ids)

    def test_unit_ids_assigned_by_processed_fact_position(self):
        # Even if processed_facts are interleaved across contents, each unit_id
        # must follow its corresponding processed_fact (positional alignment).
        contents = [_make_content("a"), _make_content("b")]
        processed = [
            _make_processed_fact(1, "b1"),
            _make_processed_fact(0, "a1"),
            _make_processed_fact(1, "b2"),
        ]
        unit_ids = ["u-b1", "u-a1", "u-b2"]

        result = _map_results_to_contents(contents, processed, unit_ids)

        assert result == [["u-a1"], ["u-b1", "u-b2"]]


class TestEmbeddingsBatchLengthGuarantee:
    def test_raises_when_backend_returns_fewer_embeddings(self):
        # Regression for #1037: backends that silently truncate must not pass
        # through — `zip(extracted_facts, embeddings)` would otherwise drop
        # facts and break unit_id alignment downstream.
        backend = MagicMock()
        backend.encode.return_value = [[0.1, 0.2]]  # only 1 vector for 3 inputs

        with pytest.raises(RuntimeError, match="returned 1 vectors for 3 input texts"):
            asyncio.run(embedding_utils.generate_embeddings_batch(backend, ["a", "b", "c"]))

    def test_raises_when_backend_returns_more_embeddings(self):
        backend = MagicMock()
        backend.encode.return_value = [[0.1], [0.2], [0.3]]

        with pytest.raises(RuntimeError, match="returned 3 vectors for 2 input texts"):
            asyncio.run(embedding_utils.generate_embeddings_batch(backend, ["a", "b"]))

    def test_passes_through_aligned_embeddings(self):
        backend = MagicMock()
        backend.encode.return_value = [[0.1], [0.2]]

        result = asyncio.run(embedding_utils.generate_embeddings_batch(backend, ["a", "b"]))

        assert result == [[0.1], [0.2]]


class TestRemapPhase1Results:
    """Regression coverage for orphaned-placeholder → `invalid UUID '4'` crash.

    Reproduces the prod crash seen 2026-04-20 → 2026-04-22: the fork's
    Phase 1.5 dedup step removes facts from the processed list AFTER Phase 1
    has resolved entities for all original facts. Phase 2's `insert_facts_batch`
    then returns fewer UUIDs than there were placeholders. The previous
    `.get(id, id)` fallback let the raw placeholder strings (e.g. '4') leak
    into `uuid[]` bind arguments, breaking every retain since the cutover.
    """

    _uuid_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    _uuid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    _uuid_c = "cccccccc-cccc-cccc-cccc-cccccccccccc"

    def test_drops_orphaned_placeholders_after_dedup(self):
        # Phase 1 resolved entities for 5 facts ('0'..'4') and linked each to
        # some entities. After dedup, only facts at original indices 0, 2, 3
        # survive (corresponding to actual UUIDs a, b, c).
        resolved_entity_ids = ["e1", "e2", "e3", "e4", "e5"]
        # entity_to_unit: (placeholder_unit_id, local_idx, fact_date)
        entity_to_unit = [
            ("0", 0, None),  # survives → a
            ("1", 1, None),  # DEDUPED (placeholder '1' has no mapping) → drop
            ("2", 2, None),  # survives → b
            ("3", 3, None),  # survives → c
            ("4", 4, None),  # DEDUPED → drop
        ]
        unit_to_entity_ids = {"0": ["e1"], "1": ["e2"], "2": ["e3"], "3": ["e4"], "4": ["e5"]}
        semantic_ann_links = [
            ("0", "target-1", 0.9, None, None),
            ("4", "target-2", 0.8, None, None),  # DEDUPED from
        ]
        # insert_facts_batch returned only 3 UUIDs (deduped indices 1 and 4 dropped)
        actual_unit_ids = [self._uuid_a, self._uuid_b, self._uuid_c]

        remapped_e2u, remapped_u2e, remapped_semantic = _remap_phase1_results(
            resolved_entity_ids, entity_to_unit, unit_to_entity_ids, semantic_ann_links, actual_unit_ids
        )

        # Only the 3 surviving facts should appear, all with real UUIDs
        assert len(remapped_e2u) == 3
        assert {t[0] for t in remapped_e2u} == {self._uuid_a, self._uuid_b, self._uuid_c}
        # No raw placeholder strings anywhere — this is the critical invariant
        all_unit_ids = {t[0] for t in remapped_e2u}
        assert not any(uid.isdigit() for uid in all_unit_ids), (
            f"Raw placeholder leaked into unit_id list: {all_unit_ids}"
        )

        # unit_to_entity_ids keys should be real UUIDs only
        assert set(remapped_u2e.keys()) == {self._uuid_a, self._uuid_b, self._uuid_c}
        assert not any(k.isdigit() for k in remapped_u2e.keys())

        # Semantic link with orphaned placeholder should be dropped
        assert len(remapped_semantic) == 1
        assert remapped_semantic[0][0] == self._uuid_a

    def test_no_dedup_keeps_all_entries(self):
        # Happy path: no dedup, N placeholders map to N actual UUIDs → nothing dropped
        entity_to_unit = [("0", 0, None), ("1", 1, None)]
        unit_to_entity_ids = {"0": ["e1"], "1": ["e2"]}
        semantic_ann_links = [("0", "t", 0.9, None, None), ("1", "t2", 0.8, None, None)]
        actual_unit_ids = [self._uuid_a, self._uuid_b]

        remapped_e2u, remapped_u2e, remapped_semantic = _remap_phase1_results(
            [], entity_to_unit, unit_to_entity_ids, semantic_ann_links, actual_unit_ids
        )

        assert len(remapped_e2u) == 2
        assert len(remapped_u2e) == 2
        assert len(remapped_semantic) == 2

    def test_empty_phase1_noop(self):
        remapped_e2u, remapped_u2e, remapped_semantic = _remap_phase1_results([], [], {}, [], [])
        assert remapped_e2u == []
        assert remapped_u2e == {}
        assert remapped_semantic == []
