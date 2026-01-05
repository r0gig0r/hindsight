"""
Tests for automatic embedding dimension detection and database schema adjustment.

Uses an isolated PostgreSQL schema to avoid affecting other tests.
These tests must run sequentially (not in parallel) due to schema modifications.
"""

import asyncio
import os
import pytest
from sqlalchemy import create_engine, text

from hindsight_api.migrations import ensure_embedding_dimension, run_migrations
from hindsight_api.engine.embeddings import LocalSTEmbeddings


def get_test_schema(worker_id: str) -> str:
    """Get unique schema name per xdist worker."""
    if worker_id == "master" or not worker_id:
        return "test_embedding_dim"
    # For parallel workers, use worker-specific schema
    return f"test_embedding_dim_{worker_id}"


@pytest.fixture(scope="class")
def isolated_schema(pg0_db_url, worker_id):
    """
    Create an isolated schema for dimension tests.

    Creates a unique schema per worker, runs migrations, and cleans up after all tests.
    """
    schema_name = get_test_schema(worker_id)
    engine = create_engine(pg0_db_url)

    # Create schema (drop first if exists from previous failed run)
    with engine.connect() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {schema_name}"))
        conn.commit()

    # Run migrations in the isolated schema
    run_migrations(pg0_db_url, schema=schema_name)

    yield pg0_db_url, schema_name

    # Cleanup: drop the test schema
    with engine.connect() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
        conn.commit()


def get_column_dimension(db_url: str, schema: str = "public") -> int | None:
    """Get the current embedding column dimension from the database."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT atttypmod
                FROM pg_attribute a
                JOIN pg_class c ON a.attrelid = c.oid
                JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE n.nspname = :schema
                  AND c.relname = 'memory_units'
                  AND a.attname = 'embedding'
            """),
            {"schema": schema},
        ).scalar()
        return result


def get_row_count(db_url: str, schema: str = "public") -> int:
    """Get the number of rows with embeddings in memory_units."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT COUNT(*) FROM {schema}.memory_units WHERE embedding IS NOT NULL")
        ).scalar()


def insert_test_embedding(db_url: str, schema: str, dimension: int):
    """Insert a test row with a dummy embedding."""
    engine = create_engine(db_url)
    # Create a dummy embedding of the specified dimension
    embedding = [0.1] * dimension
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

    with engine.connect() as conn:
        # Use string formatting for the vector since SQLAlchemy has issues with ::vector cast
        conn.execute(
            text(f"""
                INSERT INTO {schema}.memory_units (bank_id, text, embedding, event_date, fact_type)
                VALUES ('test-bank', 'test text', '{embedding_str}'::vector, NOW(), 'world')
            """)
        )
        conn.commit()


def clear_embeddings(db_url: str, schema: str):
    """Clear all rows from memory_units."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        conn.execute(text(f"DELETE FROM {schema}.memory_units"))
        conn.commit()


class TestEmbeddingDimension:
    """Tests for embedding dimension detection and adjustment."""

    def test_dimension_matches_no_change(self, isolated_schema):
        """When dimension matches, no changes should be made."""
        db_url, schema = isolated_schema

        # Get initial dimension (should be 384 from migration)
        initial_dim = get_column_dimension(db_url, schema)
        assert initial_dim == 384, f"Expected 384, got {initial_dim}"

        # Call ensure_embedding_dimension with matching dimension
        ensure_embedding_dimension(db_url, 384, schema=schema)

        # Dimension should still be 384
        assert get_column_dimension(db_url, schema) == 384

    def test_dimension_change_empty_table(self, isolated_schema):
        """When table is empty, dimension can be changed."""
        db_url, schema = isolated_schema

        # Ensure table is empty
        clear_embeddings(db_url, schema)
        assert get_row_count(db_url, schema) == 0

        # Change dimension to 768
        ensure_embedding_dimension(db_url, 768, schema=schema)

        # Verify dimension changed
        new_dim = get_column_dimension(db_url, schema)
        assert new_dim == 768, f"Expected 768, got {new_dim}"

        # Change back to 384 for other tests
        ensure_embedding_dimension(db_url, 384, schema=schema)
        assert get_column_dimension(db_url, schema) == 384

    def test_dimension_change_blocked_with_data(self, isolated_schema):
        """When table has data, dimension change should be blocked."""
        db_url, schema = isolated_schema

        # Ensure table is empty first
        clear_embeddings(db_url, schema)

        # Insert a test row with 384-dim embedding
        insert_test_embedding(db_url, schema, 384)
        assert get_row_count(db_url, schema) == 1

        # Try to change dimension - should raise error
        with pytest.raises(RuntimeError) as exc_info:
            ensure_embedding_dimension(db_url, 768, schema=schema)

        assert "Cannot change embedding dimension" in str(exc_info.value)
        assert "1 rows with embeddings" in str(exc_info.value)

        # Dimension should be unchanged
        assert get_column_dimension(db_url, schema) == 384

        # Cleanup
        clear_embeddings(db_url, schema)

    def test_embeddings_provider_dimension_detection(self, embeddings):
        """Test that LocalSTEmbeddings correctly detects dimension."""
        # Initialize embeddings if not already done
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(embeddings.initialize())
        finally:
            loop.close()

        # bge-small-en-v1.5 produces 384-dim embeddings
        assert embeddings.dimension == 384

        # Verify by generating an actual embedding
        result = embeddings.encode(["test"])
        assert len(result) == 1
        assert len(result[0]) == 384
