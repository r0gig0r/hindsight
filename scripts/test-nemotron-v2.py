"""Round 2: Nemotron IS a reasoning model with reasoning_content on a side
channel. Give it enough output tokens and see if the real workload actually
works; measure how bad the reasoning tax is.
"""

import asyncio
import json
import os
import statistics
import sys
import time
from typing import Any, Literal
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

client = AsyncOpenAI(api_key=os.environ.get("LLM_KEY", "dummy"),
                    base_url=os.environ["LLM_API"], timeout=300.0, max_retries=0)
MODEL = os.environ["LLM_MODEL"]

class Entity(BaseModel):
    text: str

class FactCausalRelation(BaseModel):
    target_index: int
    relation_type: Literal["caused_by"]
    strength: float = Field(ge=0.0, le=1.0, default=1.0)

class ExtractedFact(BaseModel):
    what: str; when: str; where: str; who: str; why: str
    fact_kind: str = "conversation"
    occurred_start: str | None = None
    occurred_end: str | None = None
    fact_type: Literal["world", "assistant"]
    entities: list[Entity] | None = None
    causal_relations: list[FactCausalRelation] | None = None

class FactExtractionResponse(BaseModel):
    facts: list[ExtractedFact]

FACT_SYSTEM = """You are an information extractor. Given a conversation excerpt, extract factual statements as JSON.

For each fact produce an object with: what, when, where, who, why (strings, 'N/A' if unknown), fact_type ('world' for external facts, 'assistant' for first-person), fact_kind ('event' or 'conversation'), optional entities (list of {text}), optional causal_relations (list of {target_index, relation_type:'caused_by', strength}).

Respond ONLY with a JSON object of the shape: {"facts": [ ... ]}. No prose, no code fences."""

FACT_USER = """Conversation excerpt (2026-04-19, speaker is user "Igor"):

Igor: We just merged v0.5.3 of upstream hindsight into the r0gig0r fork. The big win is that Codex model auto-resolution now works — when OpenAI removes gpt-5.1-codex-mini, the resolver reads ~/.codex/models_cache.json and picks the next mini. We also shipped a UUID sanitizer in link_utils.py because we were seeing a weird "invalid UUID '110'" error about once a day for six weeks. Daemon went healthy at 18:18 Sofia time. Consolidation is caught up, zero failures since the fix deployed.

Extract 4-7 atomic facts."""

RESULTS: dict[str, Any] = {}


async def measure_reasoning_tax():
    """One call — extract facts, report reasoning vs content token split."""
    print("\n=== Reasoning-tax measurement (max_tokens=8000) ===")
    schema = FactExtractionResponse.model_json_schema()
    system = FACT_SYSTEM + f"\n\nJSON schema:\n{json.dumps(schema)}"
    t0 = time.time()
    r = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": FACT_USER}],
        max_tokens=8000, temperature=0.0,
    )
    dt = time.time() - t0
    m = r.choices[0].message.model_dump()
    content = r.choices[0].message.content or ""
    reasoning = m.get("reasoning_content") or ""
    print(f"  wall={dt:.2f}s  finish={r.choices[0].finish_reason}")
    print(f"  prompt_tokens={r.usage.prompt_tokens}  completion_tokens={r.usage.completion_tokens}")
    print(f"  reasoning_chars={len(reasoning)}  content_chars={len(content)}")
    print(f"  approx_reasoning_frac={len(reasoning)/(len(reasoning)+len(content)+1):.1%}")
    try:
        data = json.loads(content)
        parsed = FactExtractionResponse.model_validate(data)
        print(f"  PARSED: {len(parsed.facts)} facts")
        for f in parsed.facts:
            print(f"    - {f.fact_type}: {f.what[:100]}")
        RESULTS["retain_parsed"] = True
        RESULTS["retain_facts_count"] = len(parsed.facts)
        RESULTS["retain_latency_s"] = dt
        RESULTS["retain_out_tokens"] = r.usage.completion_tokens
        RESULTS["retain_reasoning_chars"] = len(reasoning)
        RESULTS["retain_content_chars"] = len(content)
    except (json.JSONDecodeError, ValidationError) as e:
        print(f"  STILL FAILED: {str(e)[:300]}")
        print(f"  content_preview={content[:500]!r}")
        print(f"  reasoning_tail={reasoning[-300:]!r}")
        RESULTS["retain_parsed"] = False


RECALL_TOOL = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": "Search the memory bank for facts relevant to a query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}


async def test_tools_with_budget():
    print("\n=== Tool call with adequate reasoning budget (max_tokens=4000) ===")
    t0 = time.time()
    r = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a memory agent. Call the `recall` tool to gather evidence before answering."},
            {"role": "user", "content": "What do we know about the v0.5.3 merge?"},
        ],
        tools=[RECALL_TOOL],
        tool_choice="required",
        max_tokens=4000, temperature=0.0,
    )
    dt = time.time() - t0
    msg = r.choices[0].message
    m = msg.model_dump()
    print(f"  wall={dt:.2f}s  finish={r.choices[0].finish_reason}  completion_tokens={r.usage.completion_tokens}")
    print(f"  content={msg.content!r}")
    print(f"  reasoning_len={len(m.get('reasoning_content') or '')}")
    tc = msg.tool_calls or []
    if tc:
        first = tc[0]
        print(f"  tool_call: name={first.function.name} args={first.function.arguments!r}")
        try:
            args = json.loads(first.function.arguments)
            print(f"  parsed args: {args}")
            RESULTS["tools_work"] = True
        except Exception as e:
            print(f"  args parse FAIL: {e}")
            RESULTS["tools_work"] = False
    else:
        print("  NO TOOL CALL")
        RESULTS["tools_work"] = False
    RESULTS["tools_latency_s"] = dt


async def test_concurrency_2():
    print("\n=== Concurrency: 2 parallel retain calls (small test) ===")
    schema = FactExtractionResponse.model_json_schema()
    system = FACT_SYSTEM + f"\n\nJSON schema:\n{json.dumps(schema)}"
    async def one(idx: int):
        t0 = time.time()
        try:
            r = await client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": FACT_USER}],
                max_tokens=8000, temperature=0.0,
            )
            dt = time.time() - t0
            content = r.choices[0].message.content or ""
            ok = False
            try:
                FactExtractionResponse.model_validate(json.loads(content))
                ok = True
            except Exception:
                pass
            return (dt, ok, r.usage.completion_tokens)
        except Exception as e:
            return (-1, False, str(e))

    t0 = time.time()
    results = await asyncio.gather(*[one(i) for i in range(2)])
    wall = time.time() - t0
    print(f"  wall={wall:.2f}s")
    for i, (dt, ok, toks) in enumerate(results):
        print(f"    req {i}: dt={dt:.2f}s ok={ok} tokens={toks}")
    succeeded = sum(1 for _, ok, _ in results if ok)
    RESULTS["conc_2_succeeded"] = f"{succeeded}/2"
    RESULTS["conc_2_wall"] = wall


async def main():
    print(f"Endpoint: {os.environ['LLM_API']}")
    print(f"Model:    {MODEL}")
    await measure_reasoning_tax()
    await test_tools_with_budget()
    await test_concurrency_2()
    print("\n=== RESULTS ===")
    for k, v in RESULTS.items():
        print(f"  {k} = {v}")
    out = os.environ.get("LLM_OUT", "/tmp/nemotron-test")
    with open(f"{out}/v2.json", "w") as f:
        json.dump(RESULTS, f, indent=2, default=str)


if __name__ == "__main__":
    asyncio.run(main())
