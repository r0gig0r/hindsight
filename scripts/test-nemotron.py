"""
Probe nvidia/nemotron-3-nano at an OpenAI-compatible LM Studio endpoint
against the actual workloads Hindsight puts on its primary LLM:

  1. Basic chat completion + warm-up latency
  2. Retain: fact extraction against the real ExtractedFact schema
     (JSON-mode, soft schema in prompt — how openai_compatible_llm.py
      calls the 'lmstudio' provider)
  3. Reflect: tool/function-calling (recall / search_observations)
  4. Concurrent load (5 parallel retain calls)

Env vars required (set by ./env.sh):
  LLM_API       full OpenAI-compat base URL including /v1
  LLM_MODEL     model id
  LLM_KEY       any non-empty string

Exit code: 0 if all tests pass hard invariants, 1 otherwise.
"""

import asyncio
import json
import os
import statistics
import sys
import time
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError
from typing import Literal

BASE_URL = os.environ["LLM_API"]
MODEL = os.environ["LLM_MODEL"]
API_KEY = os.environ.get("LLM_KEY", "dummy")

# Match hindsight-api-slim/hindsight_api/engine/retain/fact_extraction.py ExtractedFact
class Entity(BaseModel):
    text: str = Field(description="The specific, named entity as it appears in the fact.")

class FactCausalRelation(BaseModel):
    target_index: int = Field(description="Index of a PREVIOUS fact (must be < this fact's position).")
    relation_type: Literal["caused_by"]
    strength: float = Field(ge=0.0, le=1.0, default=1.0)

class ExtractedFact(BaseModel):
    what: str
    when: str
    where: str
    who: str
    why: str
    fact_kind: str = "conversation"
    occurred_start: str | None = None
    occurred_end: str | None = None
    fact_type: Literal["world", "assistant"]
    entities: list[Entity] | None = None
    causal_relations: list[FactCausalRelation] | None = None

class FactExtractionResponse(BaseModel):
    facts: list[ExtractedFact]

client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=300.0, max_retries=0)

RESULTS: dict[str, Any] = {}
PASS: list[str] = []
FAIL: list[str] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {name}{': ' + detail if detail else ''}")


async def t_basic():
    print("\n=== 1. Basic chat completion (warm-up) ===")
    latencies = []
    for i in range(3):
        t0 = time.time()
        r = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "Reply with exactly this word: OK"}],
            max_tokens=10,
            temperature=0.0,
        )
        dt = time.time() - t0
        latencies.append(dt)
        content = (r.choices[0].message.content or "").strip()
        print(f"  call {i+1}: {dt:.2f}s  content={content!r}  "
              f"in/out={r.usage.prompt_tokens}/{r.usage.completion_tokens}")
    RESULTS["basic_latencies"] = latencies
    _record("basic.responds", latencies[-1] < 60.0, f"last={latencies[-1]:.2f}s")
    _record("basic.warm_under_10s", min(latencies) < 10.0, f"min={min(latencies):.2f}s")


FACT_SYSTEM = """You are an information extractor. Given a conversation excerpt, extract factual statements as JSON.

For each fact produce an object with: what, when, where, who, why (strings, 'N/A' if unknown), fact_type ('world' for external facts, 'assistant' for first-person), fact_kind ('event' or 'conversation'), optional entities (list of {text}), optional causal_relations (list of {target_index, relation_type:'caused_by', strength}).

Respond ONLY with a JSON object of the shape: {"facts": [ ... ]}. No prose, no code fences."""

FACT_USER = """Conversation excerpt (2026-04-19, speaker is the assistant's user "Igor"):

Igor: We just merged v0.5.3 of upstream hindsight into the r0gig0r fork. The big win is that Codex model auto-resolution now works — when OpenAI removes gpt-5.1-codex-mini, the resolver reads ~/.codex/models_cache.json and picks the next mini. We also shipped a UUID sanitizer in link_utils.py because we were seeing a weird "invalid UUID '110'" error about once a day for six weeks. Daemon went healthy at 18:18 Sofia time. Consolidation is caught up, zero failures since the fix deployed.

Extract 4-7 atomic facts."""


async def t_retain():
    print("\n=== 2. Retain-style fact extraction ===")
    schema = FactExtractionResponse.model_json_schema()
    schema_msg = f"\n\nYou must respond with valid JSON matching this schema:\n{json.dumps(schema, indent=2)}"
    system = FACT_SYSTEM + schema_msg
    latencies, valid_count = [], 0
    last_err = None
    for i in range(3):
        t0 = time.time()
        try:
            r = await client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": FACT_USER}],
                max_tokens=1500,
                temperature=0.0,
            )
            dt = time.time() - t0
            latencies.append(dt)
            content = r.choices[0].message.content or ""
            # Strip ```json fences if present (matches _strip_code_fences)
            if "```" in content:
                try:
                    if "```json" in content:
                        content = content.split("```json", 1)[1].split("```", 1)[0].strip()
                    else:
                        content = content.split("```", 1)[1].split("```", 1)[0].strip()
                except Exception:
                    pass
            try:
                data = json.loads(content)
                parsed = FactExtractionResponse.model_validate(data)
                n = len(parsed.facts)
                valid_count += 1
                print(f"  call {i+1}: {dt:.2f}s  facts={n}  "
                      f"in/out={r.usage.prompt_tokens}/{r.usage.completion_tokens}")
                if i == 0:
                    RESULTS["retain_sample"] = [f.model_dump() for f in parsed.facts]
            except (json.JSONDecodeError, ValidationError) as e:
                last_err = str(e)
                print(f"  call {i+1}: {dt:.2f}s  PARSE/VALIDATION FAIL: {str(e)[:200]}")
                RESULTS.setdefault("retain_bad_content", content[:500])
        except Exception as e:
            last_err = repr(e)
            print(f"  call {i+1}: EXCEPTION: {e!r}")
    RESULTS["retain_latencies"] = latencies
    RESULTS["retain_valid_rate"] = f"{valid_count}/3"
    _record("retain.valid_json_3_of_3", valid_count == 3, last_err or f"{valid_count}/3")
    if latencies:
        _record("retain.p50_under_30s", statistics.median(latencies) < 30.0,
                f"p50={statistics.median(latencies):.2f}s")


RECALL_TOOL = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": "Search the memory bank for facts relevant to a query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query."},
                "k": {"type": "integer", "description": "Max results to return.", "default": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

SEARCH_OBS_TOOL = {
    "type": "function",
    "function": {
        "name": "search_observations",
        "description": "Look up consolidated observations about specific entities.",
        "parameters": {
            "type": "object",
            "properties": {
                "entities": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["entities"],
            "additionalProperties": False,
        },
    },
}


async def t_tools():
    print("\n=== 3. Tool / function calling (reflect path) ===")
    tools = [RECALL_TOOL, SEARCH_OBS_TOOL]
    latencies = []
    tool_call_ok = 0
    args_ok = 0
    for i, (user, forced) in enumerate([
        ("What do we know about the v0.5.3 merge?", "recall"),
        ("Summarise everything about the Codex model resolver.", "recall"),
        ("Tell me about the openclaw daemon.", "search_observations"),
    ]):
        # lmstudio / some local providers reject {"type":"function","function":{"name":...}}
        # so Hindsight normalizes that shape to tool_choice="required" + filtered tool list.
        filtered = [t for t in tools if t["function"]["name"] == forced]
        t0 = time.time()
        try:
            r = await client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": "You are a memory agent. Call the available tool to gather evidence before answering."},
                    {"role": "user", "content": user},
                ],
                tools=filtered,
                tool_choice="required",
                max_tokens=400,
                temperature=0.0,
            )
            dt = time.time() - t0
            latencies.append(dt)
            msg = r.choices[0].message
            tc = msg.tool_calls or []
            finish = r.choices[0].finish_reason
            if tc:
                tool_call_ok += 1
                first = tc[0]
                print(f"  call {i+1}: {dt:.2f}s  finish={finish}  tool={first.function.name}")
                try:
                    args = json.loads(first.function.arguments)
                    if isinstance(args, dict) and (
                        "query" in args or "entities" in args
                    ):
                        args_ok += 1
                    print(f"    args: {json.dumps(args)[:180]}")
                except Exception as e:
                    print(f"    args PARSE FAIL: {e}; raw={first.function.arguments[:200]!r}")
            else:
                print(f"  call {i+1}: {dt:.2f}s  finish={finish}  NO TOOL CALL; content={msg.content!r}")
        except Exception as e:
            print(f"  call {i+1}: EXCEPTION: {e!r}")
    RESULTS["tools_latencies"] = latencies
    RESULTS["tools_call_rate"] = f"{tool_call_ok}/3"
    RESULTS["tools_args_valid_rate"] = f"{args_ok}/3"
    _record("tools.emits_tool_call_3_of_3", tool_call_ok == 3, f"{tool_call_ok}/3")
    _record("tools.args_parse_3_of_3", args_ok == 3, f"{args_ok}/3")


async def t_concurrency():
    print("\n=== 4. Concurrent load (5 parallel retain calls) ===")
    schema = FactExtractionResponse.model_json_schema()
    system = FACT_SYSTEM + f"\n\nYou must respond with valid JSON matching this schema:\n{json.dumps(schema, indent=2)}"

    async def one(idx: int) -> float:
        t0 = time.time()
        try:
            await client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": FACT_USER}],
                max_tokens=1500,
                temperature=0.0,
            )
            return time.time() - t0
        except Exception as e:
            print(f"  req {idx} FAIL: {e!r}")
            return -1.0

    t0 = time.time()
    results = await asyncio.gather(*[one(i) for i in range(5)])
    wall = time.time() - t0
    ok = [r for r in results if r > 0]
    print(f"  wall_time={wall:.2f}s  succeeded={len(ok)}/5  "
          f"per_call: {[f'{r:.2f}s' for r in results]}")
    RESULTS["concurrency_wall"] = wall
    RESULTS["concurrency_per_call"] = results
    RESULTS["concurrency_succeeded"] = len(ok)
    _record("concurrency.5_of_5", len(ok) == 5)
    if ok:
        _record("concurrency.p95_under_60s", max(ok) < 60.0, f"max={max(ok):.2f}s")


async def main() -> int:
    print(f"Endpoint: {BASE_URL}")
    print(f"Model:    {MODEL}")
    await t_basic()
    await t_retain()
    await t_tools()
    await t_concurrency()
    print("\n=== Summary ===")
    print(f"  PASS ({len(PASS)}): {PASS}")
    print(f"  FAIL ({len(FAIL)}): {FAIL}")
    out = os.environ.get("LLM_OUT", "/tmp/nemotron-test")
    os.makedirs(out, exist_ok=True)
    with open(f"{out}/results.json", "w") as f:
        json.dump({"pass": PASS, "fail": FAIL, "details": RESULTS}, f, indent=2, default=str)
    print(f"  wrote {out}/results.json")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
