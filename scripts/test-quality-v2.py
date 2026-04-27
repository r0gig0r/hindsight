"""Quality v2 — fixes applied:

1. Retain: max_tokens=8000 so Nemotron's reasoning doesn't truncate JSON.
2. Reflect: forced tool sequence matching Hindsight's agent.py (search_observations
   then recall), then auto from iteration 2 onward. This is how production flows.
"""

import argparse
import asyncio
import json
import os
import time
from typing import Any, Literal
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError


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


FACT_SYSTEM = """You are an information extractor for a long-term memory system.
Given a conversation excerpt, extract ATOMIC factual statements as JSON.

For EACH fact:
  what: single fact, 1-2 sentences
  when: when it happened or 'N/A'
  where: location or 'N/A'
  who: people involved or 'N/A'
  why: cause/significance or 'N/A'
  fact_kind: 'event' or 'conversation'
  occurred_start/occurred_end: ISO-8601 for events, else null
  fact_type: 'world' (external) or 'assistant' (first-person)
  entities: [{text: proper-noun-or-identifier}]
  causal_relations: [{target_index, relation_type:'caused_by', strength:0..1}]

Rules:
- One fact = one assertion.
- Entities must be proper nouns / specific identifiers.
- causal_relations target_index < current index.

Respond ONLY with {"facts": [...]}. No prose. No fences."""


CASES = [
    ("A_simple_conversation",
     "User: I've been using Hindsight for six months now and it's transformed my workflow.\n"
     "User: Before it, I'd lose track of decisions from old conversations. Now my assistant "
     "remembers that we already picked Postgres over MongoDB for the catalog service.\n"
     "Assistant: Good to hear.",
     {"entities": ["Hindsight", "Postgres", "MongoDB"], "expect_causal": False,
      "expect_temporal": False, "forbidden": ["MySQL", "Redis"]}),
    ("B_event_with_timestamps",
     "It's 2026-04-19 15:30 Sofia time. The daemon restart at 14:15 Sofia this afternoon "
     "brought the system back online after the 11:47 crash. Since the restart, 0 failed "
     "retains have been recorded. Tomorrow at 09:00 we're running the weekly backup drill.",
     {"entities": [], "expect_causal": True, "expect_temporal": True, "forbidden": ["yesterday"]}),
    ("C_causal_chain",
     "The disk filled up. Because of that, Postgres went read-only, which caused the "
     "retain endpoint to start returning 500s. I deleted the old WAL archives, which "
     "freed up 40GB of disk, and then restarted Postgres. The retain endpoint recovered.",
     {"entities": ["Postgres"], "expect_causal": True, "expect_temporal": False, "forbidden": ["MySQL"]}),
    ("D_multi_entity_dense",
     "Alice from the Platform team shipped v2.3 of the auth-proxy on Tuesday. Bob reviewed "
     "the PR. They used Valkey instead of Redis for session storage because Valkey has a "
     "BSD-style license. The service runs on prod-us-east-1 under the auth-proxy namespace. "
     "Carol from Security signed off after the threat model review.",
     {"entities": ["Alice", "Bob", "Carol", "Valkey", "Redis", "auth-proxy"],
      "expect_causal": True, "expect_temporal": False, "forbidden": ["Dave", "Memcached"]}),
]


RECALL_TOOL = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": "Search memory bank for facts relevant to a query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 10},
            },
            "required": ["query"], "additionalProperties": False,
        },
    },
}

SEARCH_OBS_TOOL = {
    "type": "function",
    "function": {
        "name": "search_observations",
        "description": "Look up consolidated observations for specific entities.",
        "parameters": {
            "type": "object",
            "properties": {"entities": {"type": "array", "items": {"type": "string"}}},
            "required": ["entities"], "additionalProperties": False,
        },
    },
}

REFLECT_SYSTEM = """You are a memory agent. The user asks about past events.
You have two tools: recall and search_observations.
When you have enough evidence, STOP calling tools and write a concise final answer.
Do NOT repeat similar recall queries. Synthesize and answer."""


FAKE_RECALL_RESULTS = {
    "v0.5.3": [
        {"id": "f1", "text": "Merged upstream hindsight v0.5.3 into r0gig0r fork (128 commits) on 2026-04-19"},
        {"id": "f2", "text": "Codex model auto-resolution shipped in commit a5d4884b"},
        {"id": "f3", "text": "UUID sanitizer added to link_utils.py to prevent 'invalid UUID' errors"},
        {"id": "f4", "text": "Daemon healthy at 18:18 Sofia, consolidation caught up, 0 failed ops"},
    ],
    "default": [{"id": "f0", "text": "No directly relevant facts found."}],
}

FAKE_OBS = {
    "openclaw daemon": "openclaw daemon: Python daemon at port 9077 managing Hindsight API. LaunchAgent ai.openclaw.hindsight-daemon.plist. Uses Codex primary with OpenRouter fallback.",
    "hindsight": "hindsight: agent memory system with biomimetic data structures. Fork of vectorize-io/hindsight.",
}


def fake_tool(name: str, args: dict) -> str:
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
            matched = None
            for key, val in FAKE_OBS.items():
                if key.lower() in e.lower() or e.lower() in key.lower():
                    matched = val; break
            out[e] = matched or "No observation found."
        return json.dumps(out)
    return "unknown tool"


def strip_fences(s: str) -> str:
    if "```" not in s:
        return s
    try:
        if "```json" in s:
            return s.split("```json", 1)[1].split("```", 1)[0].strip()
        return s.split("```", 1)[1].split("```", 1)[0].strip()
    except Exception:
        return s


def grade_retain(content: str, expect: dict) -> dict:
    result = {"parsed": False, "fact_count": 0, "entities_hit": 0,
              "entities_total": len(expect["entities"]), "causal_ok": None,
              "temporal_ok": None, "hallucinations": [], "avg_what_len": 0,
              "generic_entities": 0, "fields_used": {}}
    GENERIC = {"team", "user", "system", "service", "code", "project", "data", "file"}
    content = strip_fences(content)
    try:
        fe = FactExtractionResponse.model_validate(json.loads(content))
        result["parsed"] = True
        result["fact_count"] = len(fe.facts)
        seen_ents: set[str] = set()
        causal_seen = False
        temporal_seen = False
        what_lens: list[int] = []
        fields = {"entities": 0, "causal_relations": 0, "occurred_start": 0,
                  "where_specific": 0, "who_specific": 0}
        all_text = " ".join(f.what.lower() + " " + (f.where or "") for f in fe.facts)
        for w in expect["forbidden"]:
            if w.lower() in all_text and w.lower() not in content.lower().split("facts")[0]:
                # naive: forbidden word appeared in fact text but wasn't in input
                pass
        for f in fe.facts:
            what_lens.append(len(f.what))
            if f.entities:
                fields["entities"] += 1
                for e in f.entities:
                    seen_ents.add(e.text)
                    if e.text.lower() in GENERIC:
                        result["generic_entities"] += 1
            if f.causal_relations:
                fields["causal_relations"] += 1
                causal_seen = True
            if f.occurred_start:
                fields["occurred_start"] += 1
                temporal_seen = True
            if f.where and f.where.upper() != "N/A":
                fields["where_specific"] += 1
            if f.who and f.who.upper() != "N/A":
                fields["who_specific"] += 1
        result["causal_ok"] = (causal_seen == expect["expect_causal"])
        result["temporal_ok"] = (temporal_seen == expect["expect_temporal"])
        result["avg_what_len"] = sum(what_lens) / len(what_lens) if what_lens else 0
        result["fields_used"] = fields
        for expected in expect["entities"]:
            if any(expected.lower() in s.lower() for s in seen_ents):
                result["entities_hit"] += 1
    except (json.JSONDecodeError, ValidationError) as e:
        result["parse_error"] = str(e)[:200]
        result["content_len"] = len(content)
        result["content_tail"] = content[-400:]
    return result


async def run_retain(client: AsyncOpenAI, model: str, name: str, text: str, expect: dict) -> dict:
    schema = FactExtractionResponse.model_json_schema()
    system = FACT_SYSTEM + f"\n\nJSON schema:\n{json.dumps(schema)}"
    t0 = time.time()
    try:
        r = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": text}],
            max_tokens=8000, temperature=0.0,
        )
        dt = time.time() - t0
        content = r.choices[0].message.content or ""
        reasoning = r.choices[0].message.model_dump().get("reasoning_content") or ""
        grade = grade_retain(content, expect)
        grade["wall_s"] = dt
        grade["reasoning_chars"] = len(reasoning)
        grade["completion_tokens"] = r.usage.completion_tokens
        grade["raw_content"] = content
        grade["case"] = name
        return grade
    except Exception as e:
        return {"case": name, "parsed": False, "wall_s": time.time() - t0, "exception": repr(e)}


async def run_reflect_forced(client: AsyncOpenAI, model: str, question: str) -> dict:
    """Mirror agent.py forced-tool sequence: search_observations → recall → auto."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": REFLECT_SYSTEM},
        {"role": "user", "content": question},
    ]
    tools = [RECALL_TOOL, SEARCH_OBS_TOOL]
    trace: list[dict] = []

    async def one_turn(tc_mode):
        if isinstance(tc_mode, str) and tc_mode in ("auto", "required"):
            choice = tc_mode
            filtered_tools = tools
        else:
            # forced name — Hindsight normalizes this to required + filtered
            forced = tc_mode
            filtered_tools = [t for t in tools if t["function"]["name"] == forced]
            choice = "required"
        r = await client.chat.completions.create(
            model=model, messages=messages, tools=filtered_tools, tool_choice=choice,
            max_tokens=2000, temperature=0.0,
        )
        return r

    forced_seq = ["search_observations", "recall"]
    t0 = time.time()
    final = None
    for iteration in range(6):
        if iteration < len(forced_seq):
            choice = forced_seq[iteration]
        else:
            choice = "auto"
        try:
            r = await one_turn(choice)
        except Exception as e:
            trace.append({"iter": iteration, "choice": choice, "error": repr(e)})
            break

        msg = r.choices[0].message
        tc = msg.tool_calls or []

        if tc:
            asst_entry = {"role": "assistant", "content": msg.content,
                          "tool_calls": [{"id": t.id, "type": "function",
                                          "function": {"name": t.function.name,
                                                       "arguments": t.function.arguments}} for t in tc]}
            messages.append(asst_entry)
            calls = []
            for t in tc:
                try:
                    args = json.loads(t.function.arguments) if t.function.arguments else {}
                except Exception:
                    args = {}
                result = fake_tool(t.function.name, args)
                messages.append({"role": "tool", "tool_call_id": t.id, "content": result})
                calls.append({"tool": t.function.name, "args": args})
            trace.append({"iter": iteration, "choice": choice, "tool_calls": calls})
            continue

        # No tool call — final synthesis
        trace.append({"iter": iteration, "choice": choice, "finish": "synthesis",
                      "content_chars": len(msg.content or "")})
        final = msg.content or ""
        break

    return {"answer": final, "turns": len(trace), "wall_s": time.time() - t0, "trace": trace}


async def run_model(label: str, model: str) -> dict:
    client = AsyncOpenAI(api_key=os.environ.get("LLM_KEY", "dummy"),
                        base_url=os.environ["LLM_API"], timeout=300, max_retries=0)
    print(f"\n{'='*70}\n{label}: {model}\n{'='*70}")

    retain_results = []
    for name, text, expect in CASES:
        print(f"  retain {name} ...")
        g = await run_retain(client, model, name, text, expect)
        retain_results.append(g)
        mark = "✓" if g.get("parsed") else "✗"
        print(f"    {mark} wall={g.get('wall_s',0):.1f}s "
              f"parsed={g.get('parsed')} facts={g.get('fact_count',0)} "
              f"ent={g.get('entities_hit',0)}/{g.get('entities_total',0)} "
              f"reasoning={g.get('reasoning_chars',0)}c "
              f"tokens={g.get('completion_tokens','?')}")
        if not g.get("parsed"):
            print(f"    err: {g.get('parse_error') or g.get('exception')}")

    print(f"  reflect (forced search_observations → recall → auto) ...")
    reflect = await run_reflect_forced(client, model, "What happened with the v0.5.3 merge? Mention the openclaw daemon if relevant.")
    print(f"    turns={reflect['turns']}  answer_chars={len(reflect['answer'] or '')}  wall={reflect['wall_s']:.1f}s")
    for t in reflect["trace"]:
        if "tool_calls" in t:
            print(f"    iter {t['iter']} [{t['choice']}]: " +
                  ", ".join(f"{c['tool']}({json.dumps(c['args'])[:50]})" for c in t["tool_calls"]))
        elif "finish" in t:
            print(f"    iter {t['iter']} [{t['choice']}]: FINAL synthesis ({t['content_chars']} chars)")
        elif "error" in t:
            print(f"    iter {t['iter']} ERROR: {t['error']}")

    await client.close()
    return {"label": label, "model": model, "retain": retain_results, "reflect": reflect}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("models", nargs="*")
    args = parser.parse_args()
    targets = []
    for spec in args.models:
        label, _, mid = spec.partition(":")
        targets.append((label or mid, mid or label))
    if not targets:
        targets = [(os.environ["LLM_MODEL"], os.environ["LLM_MODEL"])]

    all_results = [await run_model(lab, mid) for lab, mid in targets]

    out = os.environ.get("LLM_OUT", "/tmp/nemotron-test")
    os.makedirs(out, exist_ok=True)
    with open(f"{out}/quality-v2.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    md = [f"# Quality v2 report\n"]
    for r in all_results:
        md.append(f"## {r['label']} (`{r['model']}`)\n")
        retain = r["retain"]
        parsed = sum(1 for x in retain if x.get("parsed"))
        ents_hit = sum(x.get("entities_hit", 0) for x in retain)
        ents_total = sum(x.get("entities_total", 0) for x in retain)
        causal_ok = sum(1 for x in retain if x.get("causal_ok") is True)
        temporal_ok = sum(1 for x in retain if x.get("temporal_ok") is True)
        generic = sum(x.get("generic_entities", 0) for x in retain)
        reasoning_tot = sum(x.get("reasoning_chars", 0) for x in retain)
        avg_lat = sum(x.get("wall_s", 0) for x in retain) / len(retain)
        md.append(f"```\n"
                  f"  JSON parse: {parsed}/{len(retain)}\n"
                  f"  Entities: {ents_hit}/{ents_total}\n"
                  f"  Causal correct: {causal_ok}/{len(retain)}\n"
                  f"  Temporal correct: {temporal_ok}/{len(retain)}\n"
                  f"  Generic-word entities: {generic}\n"
                  f"  Total reasoning chars: {reasoning_tot}\n"
                  f"  Avg retain latency: {avg_lat:.2f}s\n"
                  f"  Reflect: {r['reflect']['turns']} turns, "
                  f"answer_chars={len(r['reflect']['answer'] or '')}, "
                  f"wall={r['reflect']['wall_s']:.2f}s\n```\n\n")
        md.append("### Retain samples\n\n")
        for x in retain:
            md.append(f"#### {x['case']}\n")
            md.append(f"parsed={x.get('parsed')} facts={x.get('fact_count',0)} "
                      f"fields={x.get('fields_used')} avg_what_len={x.get('avg_what_len',0):.0f}\n\n")
            if x.get("raw_content"):
                try:
                    pretty = json.dumps(json.loads(strip_fences(x["raw_content"])), indent=2)
                    md.append(f"```json\n{pretty[:3000]}\n```\n\n")
                except Exception:
                    md.append(f"```\n{x['raw_content'][:2000]}\n```\n\n")
        md.append("### Reflect trace\n\n```\n")
        for t in r["reflect"]["trace"]:
            md.append(json.dumps(t, default=str)[:500] + "\n")
        md.append(f"```\n\n### Final answer\n\n> {(r['reflect']['answer'] or '<empty>')[:1500]}\n\n")

    with open(f"{out}/quality-v2.md", "w") as f:
        f.write("".join(md))
    print(f"\nSaved: {out}/quality-v2.json and quality-v2.md")


if __name__ == "__main__":
    asyncio.run(main())
