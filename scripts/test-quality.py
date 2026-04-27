"""Quality assessment for Hindsight primary-LLM candidates.

Four retain cases probing different schema dimensions, plus a multi-turn
reflect/tool-loop simulation. Writes a JSON report plus human-readable
side-by-side transcripts so you can judge quality directly.

Usage:
    LLM_MODEL=... python scripts/test-quality.py           # single-model mode
    python scripts/test-quality.py A:nvidia/nemotron-3-nano B:gemma-4-26b-a4b-it-ara-abliterated
"""

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

# ------------------------------ Schemas (match fact_extraction.py) ----------

class Entity(BaseModel):
    text: str

class FactCausalRelation(BaseModel):
    target_index: int
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


# ------------------------------ Test corpora --------------------------------

FACT_SYSTEM = """You are an information extractor for a long-term memory system.
Given a conversation excerpt, extract ATOMIC factual statements as JSON.

For EACH fact produce an object with:
  what: the single fact in 1-2 sentences
  when: when it happened or 'N/A'
  where: location or 'N/A'
  who: people involved or 'N/A'
  why: cause/significance or 'N/A'
  fact_kind: 'event' (thing happened at a point in time) or 'conversation' (statement of belief/knowledge)
  occurred_start: ISO-8601 timestamp for events, else null
  occurred_end: ISO-8601 timestamp for event end, else null
  fact_type: 'world' (objective external fact) or 'assistant' (first-person experience/action of speaker)
  entities: list of {text: proper-noun-or-specific-identifier}, omit generic nouns
  causal_relations: list of {target_index:int<current_index, relation_type:'caused_by', strength:0..1}

Rules:
- One fact = one assertion. Don't combine "A happened AND B happened" into one fact.
- Use N/A for missing dimensions — don't invent.
- Entities must be proper nouns or specific identifiers (names, products, files), NOT generic words.
- causal_relations target_index MUST be strictly less than this fact's position in the list.

Respond ONLY with a JSON object: {"facts": [...]}. No prose. No markdown fences."""


@dataclass
class RetainCase:
    name: str
    input: str
    expected_facts_min: int
    expected_facts_max: int
    expected_entities: set[str]       # entities that SHOULD appear
    expected_causal: bool             # should at least one fact have causal_relations?
    expected_temporal: bool           # should at least one fact have occurred_start populated?
    forbidden_hallucinations: list[str]  # strings that should NOT appear in any fact


CASES: list[RetainCase] = [
    RetainCase(
        name="A_simple_conversation",
        input=(
            "User: I've been using Hindsight for six months now and it's transformed my workflow.\n"
            "User: Before it, I'd lose track of decisions from old conversations. Now my assistant "
            "remembers that we already picked Postgres over MongoDB for the catalog service.\n"
            "Assistant: Good to hear."
        ),
        expected_facts_min=2,
        expected_facts_max=4,
        expected_entities={"Hindsight", "Postgres", "MongoDB"},
        expected_causal=False,
        expected_temporal=False,
        forbidden_hallucinations=["MySQL", "Redis", "year"],
    ),
    RetainCase(
        name="B_event_with_timestamps",
        input=(
            "It's 2026-04-19 15:30 Sofia time. The daemon restart at 14:15 Sofia this afternoon "
            "brought the system back online after the 11:47 crash. Since the restart, 0 failed "
            "retains have been recorded. Tomorrow at 09:00 we're running the weekly backup drill."
        ),
        expected_facts_min=3,
        expected_facts_max=5,
        expected_entities=set(),
        expected_causal=True,
        expected_temporal=True,
        forbidden_hallucinations=["yesterday", "last week"],
    ),
    RetainCase(
        name="C_causal_chain",
        input=(
            "The disk filled up. Because of that, Postgres went read-only, which caused the "
            "retain endpoint to start returning 500s. I deleted the old WAL archives, which "
            "freed up 40GB of disk, and then restarted Postgres. The retain endpoint recovered "
            "as soon as Postgres came back up."
        ),
        expected_facts_min=4,
        expected_facts_max=7,
        expected_entities={"Postgres"},
        expected_causal=True,
        expected_temporal=False,
        forbidden_hallucinations=["MySQL", "Oracle"],
    ),
    RetainCase(
        name="D_multi_entity_dense",
        input=(
            "Alice from the Platform team shipped v2.3 of the auth-proxy on Tuesday. Bob reviewed "
            "the PR. They used Valkey instead of Redis for session storage because Valkey has a "
            "BSD-style license. The service runs on the prod-us-east-1 cluster under the auth-proxy "
            "namespace. Carol from Security signed off after the threat model review."
        ),
        expected_facts_min=4,
        expected_facts_max=7,
        expected_entities={"Alice", "Bob", "Carol", "Valkey", "Redis", "auth-proxy"},
        expected_causal=True,     # "because Valkey has BSD license" is causal
        expected_temporal=False,  # "Tuesday" is relative — may or may not be resolved
        forbidden_hallucinations=["Dave", "Memcached", "us-west"],
    ),
]

# ------------------------------ Reflect multi-turn --------------------------

RECALL_TOOL = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": "Search the memory bank for facts relevant to a query. Returns ranked facts.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query."},
                "k": {"type": "integer", "description": "Max results.", "default": 10},
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
        "description": "Look up consolidated observations (entity profiles) for specific entities.",
        "parameters": {
            "type": "object",
            "properties": {
                "entities": {"type": "array", "items": {"type": "string"}, "description": "Canonical entity names"},
            },
            "required": ["entities"],
            "additionalProperties": False,
        },
    },
}

REFLECT_SYSTEM = """You are a memory agent. The user will ask questions about past events.
You have two tools: `recall` (text search over facts) and `search_observations` (entity profiles).
Gather evidence with tools, then answer.
- Start by calling `recall` with a well-scoped query.
- If the recall results mention specific named entities the user cares about,
  call `search_observations` on those names to get their full profile.
- When you have enough evidence, stop calling tools and synthesize a concise answer.
- Never fabricate. If the memory does not contain an answer, say so."""

# Canned tool responses — a tiny in-memory fake store about v0.5.3 merge
FAKE_RECALL_RESULTS = {
    "v0.5.3 merge": [
        {"id": "f1", "text": "Merged upstream hindsight v0.5.3 into r0gig0r fork (128 commits)", "fact_type": "world"},
        {"id": "f2", "text": "Codex model auto-resolution shipped in commit a5d4884b", "fact_type": "world"},
        {"id": "f3", "text": "UUID sanitizer added to link_utils.py to prevent 'invalid UUID' errors", "fact_type": "world"},
        {"id": "f4", "text": "Daemon healthy at 18:18 Sofia, consolidation caught up, 0 failed ops", "fact_type": "world"},
    ],
    "default": [
        {"id": "f5", "text": "No directly relevant facts found.", "fact_type": "world"},
    ],
}

FAKE_OBS = {
    "openclaw daemon": "openclaw daemon: Python daemon at port 9077 managing Hindsight API for OpenClaw extension. LaunchAgent: ai.openclaw.hindsight-daemon.plist. Uses Codex primary LLM with OpenRouter fallback.",
    "Codex": "Codex: OpenAI's ChatGPT-account-based coding LLM. Accessed via OAuth from ~/.codex/auth.json. Models resolved via ~/.codex/models_cache.json.",
}


def fake_tool_result(name: str, args: dict) -> str:
    if name == "recall":
        q = (args.get("query") or "").lower()
        for key, results in FAKE_RECALL_RESULTS.items():
            if key in q:
                return json.dumps(results)
        return json.dumps(FAKE_RECALL_RESULTS["default"])
    if name == "search_observations":
        ents = args.get("entities") or []
        out = {}
        for e in ents:
            for key, val in FAKE_OBS.items():
                if key.lower() in e.lower() or e.lower() in key.lower():
                    out[e] = val
                    break
            else:
                out[e] = "No observation found."
        return json.dumps(out)
    return "unknown tool"


# ------------------------------ Grader --------------------------------------

@dataclass
class RetainGrade:
    case: str
    parsed: bool
    fact_count: int
    count_in_range: bool
    expected_entities_hit: int
    expected_entities_total: int
    causal_present: bool
    causal_expected: bool
    temporal_present: bool
    temporal_expected: bool
    hallucinations: list[str]
    schema_field_coverage: dict[str, int]   # how many facts populated each optional field
    generic_entity_count: int               # entities that look like generic nouns (bad)
    atomic_score: float                     # 0..1 — avg "what" length in chars; shorter = more atomic
    raw_content: str
    parsed_json: Any | None


GENERIC_WORDS = {
    "team", "user", "assistant", "project", "service", "system", "code",
    "file", "commit", "issue", "branch", "feature", "test", "review",
    "thing", "data", "server", "cluster", "release", "version", "meeting",
}


def grade_retain(case: RetainCase, content: str) -> RetainGrade:
    hallucinations_found = []
    parsed_ok = False
    parsed_obj = None
    fact_count = 0
    ent_hit = 0
    ent_total = len(case.expected_entities)
    causal_present = False
    temporal_present = False
    field_cov = {"entities": 0, "causal_relations": 0, "occurred_start": 0,
                 "occurred_end": 0, "when_specific": 0, "where_specific": 0, "who_specific": 0}
    generic_entities = 0
    atomic_lengths: list[int] = []

    stripped = content
    if "```" in stripped:
        try:
            if "```json" in stripped:
                stripped = stripped.split("```json", 1)[1].split("```", 1)[0].strip()
            else:
                stripped = stripped.split("```", 1)[1].split("```", 1)[0].strip()
        except Exception:
            pass

    try:
        data = json.loads(stripped)
        fe = FactExtractionResponse.model_validate(data)
        parsed_ok = True
        parsed_obj = data
        fact_count = len(fe.facts)

        all_fact_text = " ".join(f.what.lower() + " " + (f.where or "") + " " + (f.who or "") for f in fe.facts)
        for word in case.forbidden_hallucinations:
            if word.lower() in all_fact_text and word.lower() not in case.input.lower():
                hallucinations_found.append(word)

        seen_entities: set[str] = set()
        for f in fe.facts:
            if f.entities:
                field_cov["entities"] += 1
                for e in f.entities:
                    seen_entities.add(e.text)
                    if e.text.lower() in GENERIC_WORDS:
                        generic_entities += 1
            if f.causal_relations:
                field_cov["causal_relations"] += 1
                causal_present = True
            if f.occurred_start:
                field_cov["occurred_start"] += 1
                temporal_present = True
            if f.occurred_end:
                field_cov["occurred_end"] += 1
            if f.when and f.when.strip().upper() != "N/A":
                field_cov["when_specific"] += 1
            if f.where and f.where.strip().upper() != "N/A":
                field_cov["where_specific"] += 1
            if f.who and f.who.strip().upper() != "N/A":
                field_cov["who_specific"] += 1
            atomic_lengths.append(len(f.what))

        for expected in case.expected_entities:
            if any(expected.lower() in s.lower() for s in seen_entities):
                ent_hit += 1
    except (json.JSONDecodeError, ValidationError) as e:
        pass

    avg_len = sum(atomic_lengths) / len(atomic_lengths) if atomic_lengths else 0
    atomic_score = max(0, 1 - (avg_len / 300))  # 300-char fact = 0 score, 0-char = 1

    return RetainGrade(
        case=case.name,
        parsed=parsed_ok,
        fact_count=fact_count,
        count_in_range=case.expected_facts_min <= fact_count <= case.expected_facts_max,
        expected_entities_hit=ent_hit,
        expected_entities_total=ent_total,
        causal_present=causal_present,
        causal_expected=case.expected_causal,
        temporal_present=temporal_present,
        temporal_expected=case.expected_temporal,
        hallucinations=hallucinations_found,
        schema_field_coverage=field_cov,
        generic_entity_count=generic_entities,
        atomic_score=atomic_score,
        raw_content=content,
        parsed_json=parsed_obj,
    )


# ------------------------------ Runner --------------------------------------

def make_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=os.environ.get("LLM_KEY", "dummy"),
        base_url=os.environ["LLM_API"],
        timeout=300.0, max_retries=0,
    )


async def run_retain_case(client: AsyncOpenAI, model: str, case: RetainCase) -> tuple[RetainGrade, float]:
    schema = FactExtractionResponse.model_json_schema()
    system = FACT_SYSTEM + f"\n\nJSON schema for the response:\n{json.dumps(schema)}"
    t0 = time.time()
    try:
        r = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": case.input}],
            max_tokens=4000, temperature=0.0,
        )
        dt = time.time() - t0
        content = r.choices[0].message.content or ""
    except Exception as e:
        dt = time.time() - t0
        content = f"<<EXCEPTION: {e!r}>>"
    grade = grade_retain(case, content)
    return grade, dt


async def run_reflect(client: AsyncOpenAI, model: str, question: str) -> dict:
    """Simulate a multi-turn reflect loop. Return trace + final answer."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": REFLECT_SYSTEM},
        {"role": "user", "content": question},
    ]
    tools = [RECALL_TOOL, SEARCH_OBS_TOOL]
    trace: list[dict] = []
    t0 = time.time()
    final_answer = None
    for turn in range(6):
        try:
            r = await client.chat.completions.create(
                model=model, messages=messages, tools=tools, tool_choice="auto",
                max_tokens=1000, temperature=0.0,
            )
        except Exception as e:
            trace.append({"turn": turn, "error": repr(e)})
            break

        msg = r.choices[0].message
        finish = r.choices[0].finish_reason
        tc = msg.tool_calls or []

        if tc:
            calls = []
            messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
                {"id": t.id, "type": "function",
                 "function": {"name": t.function.name, "arguments": t.function.arguments}} for t in tc
            ]})
            for t in tc:
                try:
                    args = json.loads(t.function.arguments) if t.function.arguments else {}
                except Exception:
                    args = {"_raw": t.function.arguments}
                result = fake_tool_result(t.function.name, args)
                messages.append({"role": "tool", "tool_call_id": t.id, "content": result})
                calls.append({"tool": t.function.name, "args": args, "result_preview": result[:200]})
            trace.append({"turn": turn, "finish": finish, "tool_calls": calls})
            continue

        trace.append({"turn": turn, "finish": finish, "content": (msg.content or "")[:600]})
        final_answer = msg.content or ""
        break
    dt = time.time() - t0
    return {"answer": final_answer, "turns": len(trace), "wall_s": dt, "trace": trace}


async def run_model(model: str) -> dict:
    client = make_client()
    print(f"\n{'='*70}\nMODEL: {model}\n{'='*70}")

    retain_grades = []
    for case in CASES:
        print(f"\n  --- retain case: {case.name} ---")
        grade, dt = await run_retain_case(client, model, case)
        retain_grades.append({"case": case.name, "wall_s": dt, "grade": grade})
        print(f"    wall={dt:.2f}s parsed={grade.parsed} facts={grade.fact_count} "
              f"(expected {case.expected_facts_min}-{case.expected_facts_max}) "
              f"ent_hit={grade.expected_entities_hit}/{grade.expected_entities_total} "
              f"causal={'✓' if grade.causal_present == grade.causal_expected else '✗'} "
              f"temporal={'✓' if grade.temporal_present == grade.temporal_expected else '✗'} "
              f"hallucinations={grade.hallucinations} generic_ents={grade.generic_entity_count}")

    print(f"\n  --- reflect multi-turn ---")
    reflect_result = await run_reflect(client, model, "What happened with the v0.5.3 merge? Include details about the openclaw daemon.")
    print(f"    turns={reflect_result['turns']} wall={reflect_result['wall_s']:.2f}s")
    for t in reflect_result["trace"]:
        if "tool_calls" in t:
            for c in t["tool_calls"]:
                print(f"    turn {t['turn']}: {c['tool']}({json.dumps(c['args'])[:80]})")
        elif "content" in t:
            print(f"    turn {t['turn']} [final]: {(t['content'] or '')[:200]}")
        elif "error" in t:
            print(f"    turn {t['turn']} [ERROR]: {t['error']}")

    await client.close()
    return {"model": model, "retain": retain_grades, "reflect": reflect_result}


def format_score(r: dict) -> str:
    """Produce a short human-readable score summary for one model."""
    retain = r["retain"]
    total = len(retain)
    parsed = sum(1 for rg in retain if rg["grade"].parsed)
    in_range = sum(1 for rg in retain if rg["grade"].count_in_range)
    ent_hit = sum(rg["grade"].expected_entities_hit for rg in retain)
    ent_total = sum(rg["grade"].expected_entities_total for rg in retain)
    causal_match = sum(1 for rg in retain if rg["grade"].causal_present == rg["grade"].causal_expected)
    temporal_match = sum(1 for rg in retain if rg["grade"].temporal_present == rg["grade"].temporal_expected)
    total_halluc = sum(len(rg["grade"].hallucinations) for rg in retain)
    total_generic = sum(rg["grade"].generic_entity_count for rg in retain)
    avg_atomic = sum(rg["grade"].atomic_score for rg in retain) / total if total else 0
    avg_latency = sum(rg["wall_s"] for rg in retain) / total if total else 0
    reflect_answer_len = len((r["reflect"]["answer"] or ""))
    return (
        f"  JSON parse: {parsed}/{total}\n"
        f"  Fact count in expected range: {in_range}/{total}\n"
        f"  Entities recalled: {ent_hit}/{ent_total}\n"
        f"  Causal field used correctly: {causal_match}/{total}\n"
        f"  Temporal field used correctly: {temporal_match}/{total}\n"
        f"  Hallucinations: {total_halluc}\n"
        f"  Generic-word entities (bad): {total_generic}\n"
        f"  Atomic-fact score (higher = better): {avg_atomic:.2f}\n"
        f"  Avg retain latency: {avg_latency:.2f}s\n"
        f"  Reflect: {r['reflect']['turns']} turns, answer_chars={reflect_answer_len}, wall={r['reflect']['wall_s']:.2f}s"
    )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("models", nargs="*",
                        help="Optional model specs like name:model_id. If empty, uses LLM_MODEL env.")
    args = parser.parse_args()

    if args.models:
        targets = []
        for spec in args.models:
            label, _, mid = spec.partition(":")
            targets.append((label or mid, mid or label))
    else:
        targets = [(os.environ["LLM_MODEL"], os.environ["LLM_MODEL"])]

    all_results = []
    for label, mid in targets:
        result = await run_model(mid)
        result["label"] = label
        all_results.append(result)

    # Save everything
    out = os.environ.get("LLM_OUT", "/tmp/nemotron-test")
    os.makedirs(out, exist_ok=True)
    raw_path = f"{out}/quality-raw.json"
    # Convert dataclasses / pydantic to json-able
    serializable = []
    for r in all_results:
        serializable.append({
            "model": r["model"],
            "label": r["label"],
            "retain": [
                {"case": rg["case"], "wall_s": rg["wall_s"],
                 "grade": {**rg["grade"].__dict__, "raw_content_preview": rg["grade"].raw_content[:800]}}
                for rg in r["retain"]
            ],
            "reflect": r["reflect"],
        })
    with open(raw_path, "w") as f:
        json.dump(serializable, f, indent=2, default=str)
    print(f"\n\nRaw details: {raw_path}")

    # Write a human-readable transcript
    md_path = f"{out}/quality-report.md"
    with open(md_path, "w") as f:
        f.write("# Quality report\n\n")
        for r in all_results:
            f.write(f"## {r['label']} (`{r['model']}`)\n\n")
            f.write("```\n" + format_score(r) + "\n```\n\n")
            f.write("### Retain samples\n\n")
            for rg in r["retain"]:
                g = rg["grade"]
                f.write(f"#### {g.case}\n")
                f.write(f"Parsed: {g.parsed} | Facts: {g.fact_count} | Hallucinations: {g.hallucinations} | Generic entities: {g.generic_entity_count}\n\n")
                if g.parsed_json:
                    f.write("```json\n" + json.dumps(g.parsed_json, indent=2) + "\n```\n\n")
                else:
                    f.write("```\n" + g.raw_content[:2000] + "\n```\n\n")
            f.write("### Reflect trace\n\n```\n")
            for t in r["reflect"]["trace"]:
                f.write(json.dumps(t, default=str)[:1000] + "\n")
            f.write("```\n\n")
            f.write(f"### Final answer\n\n> {r['reflect']['answer']}\n\n")
    print(f"Human-readable: {md_path}")

    print("\n\n=== COMPARISON ===")
    for r in all_results:
        print(f"\n{r['label']}:")
        print(format_score(r))


if __name__ == "__main__":
    asyncio.run(main())
