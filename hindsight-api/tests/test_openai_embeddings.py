"""
Tests for OpenAI embeddings provider with retain/recall operations.

Uses an isolated PostgreSQL schema to test with OpenAI's larger embedding dimensions
(1536 for text-embedding-3-small) without affecting other tests.
"""

import asyncio
import os
import pytest
from datetime import datetime
from sqlalchemy import create_engine, text

from hindsight_api import MemoryEngine, RequestContext
from hindsight_api.engine.embeddings import OpenAIEmbeddings
from hindsight_api.engine.cross_encoder import LocalSTCrossEncoder
from hindsight_api.engine.query_analyzer import DateparserQueryAnalyzer
from hindsight_api.engine.memory_engine import _current_schema
from hindsight_api.migrations import run_migrations, ensure_embedding_dimension


def get_test_schema(worker_id: str) -> str:
    """Get unique schema name per xdist worker."""
    if worker_id == "master" or not worker_id:
        return "test_openai_embeddings"
    return f"test_openai_embeddings_{worker_id}"


def has_openai_api_key() -> bool:
    """Check if OpenAI API key is available."""
    return bool(os.environ.get("HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY"))


def get_openai_api_key() -> str:
    """Get OpenAI API key from environment."""
    return os.environ.get("HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY", "")


# Skip all tests in this module if no OpenAI API key
pytestmark = pytest.mark.skipif(
    not has_openai_api_key(),
    reason="OpenAI API key not available (set OPENAI_API_KEY or HINDSIGHT_API_LLM_API_KEY)",
)


@pytest.fixture(scope="module")
def openai_embeddings():
    """Create OpenAI embeddings instance."""
    embeddings = OpenAIEmbeddings(
        api_key=get_openai_api_key(),
        model="text-embedding-3-small",
    )
    # Initialize synchronously
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(embeddings.initialize())
    finally:
        loop.close()
    return embeddings


@pytest.fixture(scope="module")
def isolated_schema(pg0_db_url, worker_id, openai_embeddings):
    """
    Create an isolated schema for OpenAI embedding tests.

    Creates a unique schema per worker, runs migrations, adjusts embedding dimension,
    and cleans up after all tests.
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

    # Adjust embedding dimension for OpenAI (1536 for text-embedding-3-small)
    ensure_embedding_dimension(pg0_db_url, openai_embeddings.dimension, schema=schema_name)

    yield pg0_db_url, schema_name

    # Cleanup: drop the test schema
    with engine.connect() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
        conn.commit()


@pytest.fixture
def cross_encoder():
    """Provide a cross encoder for tests."""
    return LocalSTCrossEncoder()


@pytest.fixture
def query_analyzer():
    """Provide a query analyzer for tests."""
    return DateparserQueryAnalyzer()


@pytest.fixture
def test_bank_id():
    """Provide a unique bank ID for this test run."""
    return f"openai_test_{datetime.now().timestamp()}"


@pytest.fixture
def request_context():
    """Provide a default RequestContext for tests."""
    return RequestContext()


@pytest.mark.asyncio
async def test_openai_embeddings_initialization(openai_embeddings):
    """Test that OpenAI embeddings initializes correctly."""
    assert openai_embeddings.dimension == 1536
    assert openai_embeddings.provider_name == "openai"


@pytest.mark.asyncio
async def test_openai_embeddings_encode(openai_embeddings):
    """Test that OpenAI embeddings can encode text."""
    texts = ["Hello, world!", "This is a test."]
    embeddings = openai_embeddings.encode(texts)

    assert len(embeddings) == 2
    assert len(embeddings[0]) == 1536
    assert len(embeddings[1]) == 1536
    assert all(isinstance(x, float) for x in embeddings[0])


@pytest.mark.asyncio
async def test_openai_embeddings_retain_recall(
    isolated_schema,
    openai_embeddings,
    cross_encoder,
    query_analyzer,
    test_bank_id,
    request_context,
):
    """
    Test retain and recall operations with OpenAI embeddings.

    This test verifies that:
    1. Memories can be stored with OpenAI embeddings (1536 dimensions)
    2. Memories can be recalled using semantic search
    """
    db_url, schema_name = isolated_schema

    # Set the schema context variable for this test
    _current_schema.set(schema_name)

    # Create memory engine with OpenAI embeddings
    memory = MemoryEngine(
        db_url=db_url,
        memory_llm_provider=os.getenv("HINDSIGHT_API_LLM_PROVIDER", "groq"),
        memory_llm_api_key=os.getenv("HINDSIGHT_API_LLM_API_KEY"),
        memory_llm_model=os.getenv("HINDSIGHT_API_LLM_MODEL", "openai/gpt-oss-120b"),
        memory_llm_base_url=os.getenv("HINDSIGHT_API_LLM_BASE_URL") or None,
        embeddings=openai_embeddings,
        cross_encoder=cross_encoder,
        query_analyzer=query_analyzer,
        pool_min_size=1,
        pool_max_size=3,
        run_migrations=False,  # Already run in fixture
    )

    try:
        await memory.initialize()

        # Store some memories
        await memory.retain_async(
            bank_id=test_bank_id,
            content="Alice works as a software engineer at Google.",
            context="career discussion",
            request_context=request_context,
        )

        await memory.retain_async(
            bank_id=test_bank_id,
            content="Bob is a data scientist specializing in machine learning.",
            context="team introductions",
            request_context=request_context,
        )

        await memory.retain_async(
            bank_id=test_bank_id,
            content="The project deadline is next Friday.",
            context="project meeting",
            request_context=request_context,
        )

        # Recall memories related to people's jobs
        result = await memory.recall_async(
            bank_id=test_bank_id,
            query="Who works in technology?",
            request_context=request_context,
        )

        # Verify we got relevant results
        assert result is not None
        assert len(result.memories) > 0

        # Check that we found at least one relevant memory
        memory_texts = [m.text for m in result.memories]
        assert any(
            "Alice" in text or "Bob" in text or "software" in text or "data scientist" in text
            for text in memory_texts
        ), f"Expected to find relevant memories, got: {memory_texts}"

    finally:
        try:
            if memory._pool and not memory._pool._closing:
                await memory.close()
        except Exception:
            pass
        # Reset schema to default
        _current_schema.set("public")


@pytest.mark.asyncio
async def test_openai_embeddings_batch_retain(
    isolated_schema,
    openai_embeddings,
    cross_encoder,
    query_analyzer,
    test_bank_id,
    request_context,
):
    """Test batch retain with OpenAI embeddings."""
    db_url, schema_name = isolated_schema

    # Set the schema context variable for this test
    _current_schema.set(schema_name)

    memory = MemoryEngine(
        db_url=db_url,
        memory_llm_provider=os.getenv("HINDSIGHT_API_LLM_PROVIDER", "groq"),
        memory_llm_api_key=os.getenv("HINDSIGHT_API_LLM_API_KEY"),
        memory_llm_model=os.getenv("HINDSIGHT_API_LLM_MODEL", "openai/gpt-oss-120b"),
        memory_llm_base_url=os.getenv("HINDSIGHT_API_LLM_BASE_URL") or None,
        embeddings=openai_embeddings,
        cross_encoder=cross_encoder,
        query_analyzer=query_analyzer,
        pool_min_size=1,
        pool_max_size=3,
        run_migrations=False,
    )

    try:
        await memory.initialize()

        # Batch retain multiple memories
        items = [
            {"content": "Python is my favorite programming language.", "context": "preferences"},
            {"content": "I prefer dark mode for all my applications.", "context": "preferences"},
            {"content": "Coffee is essential for morning productivity.", "context": "habits"},
        ]

        result = await memory.retain_batch_async(
            bank_id=test_bank_id,
            items=items,
            request_context=request_context,
        )

        assert result.success is True
        assert result.items_count == 3

        # Recall and verify
        recall_result = await memory.recall_async(
            bank_id=test_bank_id,
            query="What are my preferences?",
            request_context=request_context,
        )

        assert recall_result is not None
        assert len(recall_result.memories) > 0

    finally:
        try:
            if memory._pool and not memory._pool._closing:
                await memory.close()
        except Exception:
            pass
        # Reset schema to default
        _current_schema.set("public")
