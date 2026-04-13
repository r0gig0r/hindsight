"""
Deduplication logic for retain pipeline.

Checks for duplicate facts using semantic similarity and temporal proximity,
including within-batch deduplication to prevent the same fact from being
inserted multiple times when extracted from a single content item.
"""

import logging
from collections import defaultdict
from datetime import UTC

import numpy as np

from .types import ProcessedFact

logger = logging.getLogger(__name__)

# Similarity threshold for within-batch dedup (same as DB dedup default)
WITHIN_BATCH_SIMILARITY_THRESHOLD = 0.92


def _check_within_batch_duplicates(
    facts: list[ProcessedFact],
    is_duplicate_flags: list[bool],
    similarity_threshold: float = WITHIN_BATCH_SIMILARITY_THRESHOLD,
) -> list[bool]:
    """
    Check for duplicates within the same batch of facts.

    For each fact that passed the DB dedup check, compare it against all
    previously approved facts in the batch. If similarity exceeds threshold,
    mark the later fact as duplicate.

    Args:
        facts: List of ProcessedFact objects
        is_duplicate_flags: Current duplicate flags (from DB check)
        similarity_threshold: Cosine similarity threshold

    Returns:
        Updated list of boolean flags with within-batch duplicates marked
    """
    if len(facts) <= 1:
        return is_duplicate_flags

    result = list(is_duplicate_flags)
    # Track embeddings of approved (non-duplicate) facts
    approved_embeddings = []
    approved_indices = []

    for i, fact in enumerate(facts):
        # Skip facts already marked as DB duplicates
        if result[i]:
            continue

        emb = np.array(fact.embedding, dtype=np.float32)

        if approved_embeddings:
            # Compare against all previously approved facts in this batch
            approved_matrix = np.vstack(approved_embeddings)
            similarities = np.dot(approved_matrix, emb)
            max_sim = np.max(similarities)

            if max_sim > similarity_threshold:
                result[i] = True
                dup_of_idx = approved_indices[int(np.argmax(similarities))]
                logger.debug(
                    f"Within-batch duplicate: fact[{i}] is duplicate of fact[{dup_of_idx}] (similarity={max_sim:.4f})"
                )
                continue

        # This fact is approved - add to approved list
        approved_embeddings.append(emb.reshape(1, -1))
        approved_indices.append(i)

    within_batch_count = sum(result) - sum(is_duplicate_flags)
    if within_batch_count > 0:
        logger.info(f"Within-batch dedup caught {within_batch_count} additional duplicates")

    return result


async def check_duplicates_batch(conn, bank_id: str, facts: list[ProcessedFact], duplicate_checker_fn) -> list[bool]:
    """
    Check which facts are duplicates using batched time-window queries
    and within-batch pairwise comparison.

    Groups facts by 12-hour time buckets to efficiently check for duplicates
    within a 24-hour window, then performs within-batch dedup to catch
    semantically identical facts extracted from the same content.

    Args:
        conn: Database connection
        bank_id: Bank identifier
        facts: List of ProcessedFact objects to check
        duplicate_checker_fn: Async function(conn, bank_id, texts, embeddings, date, time_window_hours)
                              that returns List[bool] indicating duplicates

    Returns:
        List of boolean flags (same length as facts) indicating if each fact is a duplicate
    """
    if not facts:
        return []

    # Group facts by event_date (rounded to 12-hour buckets) for efficient batching
    time_buckets = defaultdict(list)
    for idx, fact in enumerate(facts):
        # Use occurred_start if available, otherwise use mentioned_at
        # For deduplication purposes, we need a time reference
        fact_date = fact.occurred_start if fact.occurred_start is not None else fact.mentioned_at

        # Defensive: if both are None (shouldn't happen), use now()
        if fact_date is None:
            from datetime import datetime

            fact_date = datetime.now(UTC)

        # Round to 12-hour bucket to group similar times
        bucket_key = fact_date.replace(hour=(fact_date.hour // 12) * 12, minute=0, second=0, microsecond=0)
        time_buckets[bucket_key].append((idx, fact))

    # Process each bucket in batch (DB dedup)
    all_is_duplicate = [False] * len(facts)

    for bucket_date, bucket_items in time_buckets.items():
        indices = [item[0] for item in bucket_items]
        texts = [item[1].fact_text for item in bucket_items]
        embeddings = [item[1].embedding for item in bucket_items]

        # Check duplicates for this time bucket
        dup_flags = await duplicate_checker_fn(conn, bank_id, texts, embeddings, bucket_date, time_window_hours=24)

        # Map results back to original indices
        for idx, is_dup in zip(indices, dup_flags):
            all_is_duplicate[idx] = is_dup

    # Within-batch dedup: catch facts that are duplicates of each other in the same batch
    all_is_duplicate = _check_within_batch_duplicates(facts, all_is_duplicate)

    return all_is_duplicate


def filter_duplicates(facts: list[ProcessedFact], is_duplicate_flags: list[bool]) -> list[ProcessedFact]:
    """
    Filter out duplicate facts based on duplicate flags.

    Args:
        facts: List of ProcessedFact objects
        is_duplicate_flags: Boolean flags indicating which facts are duplicates

    Returns:
        List of non-duplicate facts
    """
    if len(facts) != len(is_duplicate_flags):
        raise ValueError(f"Mismatch between facts ({len(facts)}) and flags ({len(is_duplicate_flags)})")

    return [fact for fact, is_dup in zip(facts, is_duplicate_flags) if not is_dup]
