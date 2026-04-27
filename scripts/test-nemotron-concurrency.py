"""Find the concurrency ceiling. Burst N parallel retain calls, measure
success rate and wall time for N=1..6."""

import asyncio
import json
import os
import time
from typing import Literal
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

client = AsyncOpenAI(api_key=os.environ.get("LLM_KEY", "dummy"),
                    base_url=os.environ["LLM_API"], timeout=300.0, max_retries=0)
MODEL = os.environ["LLM_MODEL"]

class Entity(BaseModel):
    text: str

class ExtractedFact(BaseModel):
    what: str; when: str; where: str; who: str; why: str
    fact_kind: str = "conversation"
    occurred_start: str | None = None
    occurred_end: str | None = None
    fact_type: Literal["world", "assistant"]
    entities: list[Entity] | None = None

class FactExtractionResponse(BaseModel):
    facts: list[ExtractedFact]

FACT_SYSTEM = """You are an information extractor. Given a conversation excerpt, extract factual statements as JSON.

For each fact produce an object with: what, when, where, who, why (strings, 'N/A' if unknown), fact_type ('world' for external facts, 'assistant' for first-person), fact_kind ('event' or 'conversation'), optional entities (list of {text}).

Respond ONLY with a JSON object of the shape: {"facts": [ ... ]}. No prose, no code fences."""

FACT_USER = """Conversation excerpt (2026-04-19):

Igor: We just merged v0.5.3 of upstream hindsight into the r0gig0r fork. The big win is that Codex model auto-resolution now works. We also shipped a UUID sanitizer in link_utils.py because we were seeing a weird "invalid UUID '110'" error about once a day for six weeks. Daemon went healthy at 18:18 Sofia time. Consolidation is caught up.

Extract 4-7 atomic facts."""


async def one_call(idx: int):
    schema = FactExtractionResponse.model_json_schema()
    system = FACT_SYSTEM + f"\n\nJSON schema:\n{json.dumps(schema)}"
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
        try:
            FactExtractionResponse.model_validate(json.loads(content))
            return ("ok", dt, r.usage.completion_tokens)
        except Exception as e:
            return ("parse_fail", dt, str(e)[:80])
    except Exception as e:
        dt = time.time() - t0
        return ("http_fail", dt, str(e)[:150])


async def burst(n: int):
    t0 = time.time()
    results = await asyncio.gather(*[one_call(i) for i in range(n)])
    wall = time.time() - t0
    ok = sum(1 for r in results if r[0] == "ok")
    dts = [r[1] for r in results if r[0] == "ok"]
    avg = sum(dts) / len(dts) if dts else 0
    print(f"\nN={n}: wall={wall:.2f}s  succeeded={ok}/{n}  avg_call={avg:.2f}s")
    for i, (status, dt, info) in enumerate(results):
        marker = "✓" if status == "ok" else "✗"
        print(f"  {marker} req {i}: {status} dt={dt:.2f}s  {info}")
    return {"n": n, "wall": wall, "ok": ok, "avg_call": avg}


async def main():
    out = []
    for n in [4, 8, 16, 24, 32]:
        out.append(await burst(n))
        await asyncio.sleep(2)  # let KV cache drain between bursts

    print("\n=== Concurrency ceiling summary ===")
    print(f"{'N':<4}{'ok':<8}{'wall':<10}{'avg_per_call':<14}{'throughput':<14}")
    for r in out:
        thru = r["ok"] / r["wall"] if r["wall"] > 0 else 0
        print(f"{r['n']:<4}{str(r['ok'])+'/'+str(r['n']):<8}{r['wall']:<10.2f}{r['avg_call']:<14.2f}{thru:<14.3f}")


if __name__ == "__main__":
    asyncio.run(main())
