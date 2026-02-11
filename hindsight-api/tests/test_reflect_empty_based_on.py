"""
Test reflect endpoint with empty based_on (no memories scenario).

This test reproduces the issue where reflect API might return based_on=[]
causing client validation errors.
"""

import pytest
import pytest_asyncio
import httpx
from hindsight_api.api import create_app
import sys
sys.path.insert(0, str(__file__).replace("hindsight-api/tests/test_reflect_empty_based_on.py", "hindsight-clients/python"))
from hindsight_client_api.models.reflect_response import ReflectResponse


@pytest_asyncio.fixture
async def api_client(memory):
    """Create an async test client for the FastAPI app."""
    app = create_app(memory, initialize_memory=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_reflect_with_no_memories_empty_bank(api_client):
    """Test reflect on an empty bank (no memories) with include.facts enabled."""
    bank_id = "test_empty_bank"

    # Reflect on empty bank with facts requested
    response = await api_client.post(
        f"/v1/default/banks/{bank_id}/reflect",
        json={
            "query": "What do you know about machine learning?",
            "budget": "low",
            "include": {
                "facts": {}  # Request facts but bank is empty
            }
        }
    )

    assert response.status_code == 200
    data = response.json()

    # DEBUG: Print what the API actually returned
    import json
    print("\n" + "="*80)
    print("API Response:")
    print(json.dumps(data, indent=2))
    print("="*80 + "\n")

    # Verify response structure
    assert "text" in data
    assert "based_on" in data

    # The API should return based_on as either:
    # 1. null/None (if include.facts not set)
    # 2. {"memories": [], "mental_models": [], "directives": []} (if include.facts set but empty)
    # It should NEVER return based_on: []

    based_on = data.get("based_on")
    if based_on is not None:
        assert isinstance(based_on, dict), f"based_on should be dict or null, got {type(based_on)}: {based_on}"
        assert not isinstance(based_on, list), f"based_on should NEVER be a list! Got: {based_on}"
        assert "memories" in based_on
        assert "mental_models" in based_on
        assert "directives" in based_on
        # All should be empty lists
        assert based_on["memories"] == []
        assert based_on["mental_models"] == []
        assert based_on["directives"] == []

    # Verify client can parse the response
    try:
        reflect_response = ReflectResponse.from_dict(data)
        assert reflect_response is not None
        assert reflect_response.text is not None
        if reflect_response.based_on is not None:
            # These should be lists (even if empty), not None
            assert isinstance(reflect_response.based_on.memories, (list, type(None)))
            assert isinstance(reflect_response.based_on.mental_models, (list, type(None)))
            assert isinstance(reflect_response.based_on.directives, (list, type(None)))
    except Exception as e:
        pytest.fail(f"Client failed to parse reflect response: {e}")


@pytest.mark.asyncio
async def test_reflect_without_include_facts(api_client):
    """Test reflect without requesting facts (based_on should be None)."""
    bank_id = "test_no_facts"

    response = await api_client.post(
        f"/v1/default/banks/{bank_id}/reflect",
        json={
            "query": "Hello world",
            "budget": "low"
            # No include.facts
        }
    )

    assert response.status_code == 200
    data = response.json()

    # When include.facts is not set, based_on should not be in response (or be null)
    based_on = data.get("based_on")
    assert based_on is None, f"based_on should be None when not requested, got {type(based_on)}: {based_on}"

    # Client should handle this fine
    reflect_response = ReflectResponse.from_dict(data)
    assert reflect_response.based_on is None


def test_client_defensive_fix_handles_empty_list():
    """Test that the client's defensive fix handles based_on=[] gracefully."""
    # Simulate the buggy response that was reported in production
    buggy_response_data = {
        "text": "I don't have any information about that.",
        "based_on": []  # BUG: Should be null or proper object, not empty list
    }

    # The defensive fix should handle this by treating [] as None
    reflect_response = ReflectResponse.from_dict(buggy_response_data)
    assert reflect_response is not None
    assert reflect_response.text == "I don't have any information about that."
    assert reflect_response.based_on is None  # Defensive fix converts [] to None
