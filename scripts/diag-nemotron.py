"""Diagnostic: what IS Nemotron actually returning?

Dumps the full raw message (including reasoning_content if present) to see
whether the empty `content` is due to reasoning mode, think tags, or the
server dropping text entirely.
"""

import asyncio
import json
import os
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key=os.environ.get("LLM_KEY", "dummy"),
    base_url=os.environ["LLM_API"],
    timeout=300.0,
    max_retries=0,
)
MODEL = os.environ["LLM_MODEL"]


def dump(tag, r):
    m = r.choices[0].message
    print(f"--- {tag} ---")
    print(f"  finish={r.choices[0].finish_reason}")
    print(f"  usage: in={r.usage.prompt_tokens} out={r.usage.completion_tokens}")
    print(f"  role={m.role}")
    print(f"  content_len={len(m.content or '')}  content={(m.content or '')[:400]!r}")
    # Surface any extra fields LM Studio / Nemotron puts on the message:
    # - reasoning_content (DeepSeek, some Nvidia)
    # - reasoning (OpenRouter convention)
    # - thinking (Claude)
    raw = m.model_dump()
    for key in ("reasoning_content", "reasoning", "thinking"):
        v = raw.get(key)
        if v:
            print(f"  {key}_len={len(v)}  {key}={v[:400]!r}")
    # Tool calls
    if raw.get("tool_calls"):
        print(f"  tool_calls={raw['tool_calls']}")
    # Anything else we haven't accounted for
    extras = {k: v for k, v in raw.items() if k not in ("role", "content", "tool_calls",
                                                        "reasoning_content", "reasoning",
                                                        "thinking", "function_call",
                                                        "refusal", "audio", "annotations")}
    if extras:
        print(f"  extras_keys={list(extras.keys())}")


async def main():
    # Probe A: tiny prompt, large token budget, see if content or reasoning appears
    print("== Probe A: 'say OK', max_tokens=2000 ==")
    r = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "Reply with exactly the word OK."}],
        max_tokens=2000, temperature=0.0,
    )
    dump("A", r)

    # Probe B: ask for JSON with a huge budget
    print("\n== Probe B: JSON output, max_tokens=4000 ==")
    r = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a JSON emitter. Reply with {\"status\":\"ok\"} and nothing else."},
            {"role": "user", "content": "Go."},
        ],
        max_tokens=4000, temperature=0.0,
    )
    dump("B", r)

    # Probe C: explicitly disable thinking via extra_body if LM Studio supports it
    print("\n== Probe C: try extra_body={'thinking': False} ==")
    try:
        r = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "Reply OK."}],
            max_tokens=100, temperature=0.0,
            extra_body={"thinking": False, "enable_thinking": False},
        )
        dump("C", r)
    except Exception as e:
        print(f"  exception: {e!r}")

    # Probe D: try /no_think system directive (Qwen / Nemotron convention)
    print("\n== Probe D: /no_think system directive ==")
    r = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "/no_think\nYou answer in one word."},
            {"role": "user", "content": "Reply OK."},
        ],
        max_tokens=200, temperature=0.0,
    )
    dump("D", r)

    # Probe E: try response_format json_object
    print("\n== Probe E: response_format={'type':'json_object'} ==")
    try:
        r = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "Return {\"status\":\"ok\"}."},
                {"role": "user", "content": "Go."},
            ],
            max_tokens=4000, temperature=0.0,
            response_format={"type": "json_object"},
        )
        dump("E", r)
    except Exception as e:
        print(f"  exception: {e!r}")

    # Probe F: LM Studio model info
    print("\n== Probe F: GET /models/{id} ==")
    try:
        info = await client.models.retrieve(MODEL)
        print(f"  {info.model_dump()}")
    except Exception as e:
        print(f"  exception: {e!r}")


if __name__ == "__main__":
    asyncio.run(main())
