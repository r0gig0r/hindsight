"""Run holographic-inspired memory POC comparisons.

The runner seeds deterministic Igor-style fixture banks, runs baseline recall
and enhanced variants, then writes JSON/Markdown evidence under artifacts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from hindsight_api import MemoryEngine, RequestContext
from hindsight_api.engine.memory_engine import Budget, count_tokens
from hindsight_api.engine.retain import embedding_utils

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = ROOT / "hindsight-api-slim" / "tests" / "fixtures" / "holographic_poc_cases.yaml"
ARTIFACT_DIR = ROOT / "artifacts" / "holographic-memory-poc"
VARIANTS = ("baseline", "entity_tools", "trust", "structural")


def load_cases(path: Path = FIXTURE_PATH) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data["cases"]


def _parse_dt(value: str | None) -> datetime:
    if value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.now().astimezone()


async def _ensure_entity(conn, bank_id: str, name: str):
    await conn.execute(
        """
        INSERT INTO entities (canonical_name, bank_id)
        VALUES ($1, $2)
        ON CONFLICT DO NOTHING
        """,
        name,
        bank_id,
    )
    return await conn.fetchval(
        "SELECT id FROM entities WHERE bank_id = $1 AND LOWER(canonical_name) = LOWER($2)",
        bank_id,
        name,
    )


async def seed_case(memory: MemoryEngine, bank_id: str, case: dict[str, Any]) -> dict[str, str]:
    """Seed a fixture case directly, avoiding LLM extraction variance."""
    texts = [item["text"] for item in case["corpus"]]
    embeddings = await embedding_utils.generate_embeddings_batch(memory.embeddings, texts)
    pool = await memory._get_pool()
    corpus_id_to_unit_id: dict[str, str] = {}

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO banks (bank_id) VALUES ($1) ON CONFLICT (bank_id) DO NOTHING",
                bank_id,
            )
            for item in case["corpus"]:
                doc_id = item.get("document_id")
                if doc_id:
                    await conn.execute(
                        """
                        INSERT INTO documents (id, bank_id, original_text)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (id, bank_id) DO NOTHING
                        """,
                        doc_id,
                        bank_id,
                        item["text"],
                    )

            for item, embedding in zip(case["corpus"], embeddings):
                event_dt = _parse_dt(item.get("event_date"))
                text_signals = " ".join(item.get("entities") or [])
                row = await conn.fetchrow(
                    """
                    INSERT INTO memory_units (
                        bank_id, text, embedding, event_date, occurred_start, mentioned_at,
                        context, fact_type, metadata, document_id, tags, text_signals
                    )
                    VALUES (
                        $1, $2, $3::vector, $4, $4, $4,
                        $5, $6, $7::jsonb, $8, $9::varchar[], $10
                    )
                    RETURNING id
                    """,
                    bank_id,
                    item["text"],
                    str(embedding),
                    event_dt,
                    case.get("description", "holographic POC fixture"),
                    item.get("fact_type", "world"),
                    json.dumps({"fixture_id": item["id"]}),
                    item.get("document_id"),
                    item.get("tags", []),
                    text_signals,
                )
                unit_id = row["id"]
                corpus_id_to_unit_id[item["id"]] = str(unit_id)
                for entity_name in item.get("entities", []):
                    entity_id = await _ensure_entity(conn, bank_id, entity_name)
                    await conn.execute(
                        """
                        INSERT INTO unit_entities (unit_id, entity_id)
                        VALUES ($1, $2)
                        ON CONFLICT DO NOTHING
                        """,
                        unit_id,
                        entity_id,
                    )

    return corpus_id_to_unit_id


async def apply_fixture_feedback(
    memory: MemoryEngine,
    bank_id: str,
    case: dict[str, Any],
    id_map: dict[str, str],
    request_context: RequestContext,
) -> None:
    for item in case["corpus"]:
        feedback = item.get("feedback")
        if not feedback:
            continue
        await memory.add_memory_feedback(
            bank_id,
            id_map[item["id"]],
            feedback["rating"],
            feedback.get("source", "eval"),
            feedback.get("reason"),
            request_context=request_context,
        )


def _fact_to_dict(fact: Any, source_label: str = "baseline") -> dict[str, Any]:
    return {
        "id": fact.id,
        "text": fact.text,
        "fact_type": fact.fact_type,
        "entities": fact.entities,
        "source_label": source_label,
    }


def _inject(results: list[dict[str, Any]], max_tokens: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used = 0
    seen: set[str] = set()
    for result in results:
        if result["id"] in seen:
            continue
        tokens = count_tokens(result["text"])
        if selected and used + tokens > max_tokens:
            break
        selected.append(result)
        seen.add(result["id"])
        used += tokens
    return selected


def _jaccard_redundancy(results: list[dict[str, Any]]) -> float:
    if len(results) < 2:
        return 0.0
    scores: list[float] = []
    token_sets = [set(r["text"].lower().split()) for r in results]
    for idx, left in enumerate(token_sets):
        for right in token_sets[idx + 1 :]:
            union = left | right
            scores.append(len(left & right) / len(union) if union else 0.0)
    return sum(scores) / len(scores) if scores else 0.0


def _source_contribution(results: list[dict[str, Any]], trace: Any | None = None) -> dict[str, int]:
    counts = Counter(r.get("source_label") or "baseline" for r in results)
    if trace:
        trace_dict = trace if isinstance(trace, dict) else trace.model_dump()
        selected_ids = {r["id"] for r in results}
        for merged in trace_dict.get("rrf_merged", []):
            if merged.get("node_id") not in selected_ids:
                continue
            for source_name in (merged.get("source_ranks") or {}):
                counts[source_name.replace("_rank", "")] += 1
    return dict(counts)


def score_results(case: dict[str, Any], variant: str, results: list[dict[str, Any]], latency_ms: float, trace=None):
    injected = _inject(results, case["max_injected_tokens"])
    joined = "\n".join(r["text"] for r in injected)
    expected = case.get("expected_included_substrings", [])
    forbidden = case.get("forbidden_substrings", [])
    relevant_results = [
        r for r in injected if any(fragment.lower() in r["text"].lower() for fragment in expected)
    ]
    return {
        "variant": variant,
        "precision_at_k": len(relevant_results) / max(1, len(injected)),
        "forbidden_hit_count": sum(1 for fragment in forbidden if fragment.lower() in joined.lower()),
        "expected_include_hit_count": sum(1 for fragment in expected if fragment.lower() in joined.lower()),
        "expected_include_total": len(expected),
        "injected_token_count": sum(count_tokens(r["text"]) for r in injected),
        "injected_count": len(injected),
        "duplicate_jaccard_redundancy": _jaccard_redundancy(injected),
        "latency_ms": latency_ms,
        "source_contribution": _source_contribution(injected, trace),
        "injected_texts": [r["text"] for r in injected],
    }


async def run_variant(
    memory: MemoryEngine,
    case: dict[str, Any],
    variant: str,
    request_context: RequestContext,
) -> dict[str, Any]:
    bank_id = f"holo_poc_{case['id']}_{variant}_{uuid.uuid4().hex[:8]}"
    id_map = await seed_case(memory, bank_id, case)
    try:
        if variant == "entity_tools":
            await memory._config_resolver.update_bank_config(
                bank_id, {"experimental_entity_tools_enabled": True}, request_context
            )
        elif variant == "trust":
            await memory._config_resolver.update_bank_config(
                bank_id,
                {
                    "experimental_memory_feedback_enabled": True,
                    "experimental_trust_rerank_enabled": True,
                },
                request_context,
            )
            await apply_fixture_feedback(memory, bank_id, case, id_map, request_context)
        elif variant == "structural":
            await memory._config_resolver.update_bank_config(
                bank_id, {"experimental_structural_retrieval_enabled": True}, request_context
            )

        started = time.perf_counter()
        recall = await memory.recall_async(
            bank_id=bank_id,
            query=case["query"],
            fact_type=["world", "experience", "observation"],
            budget=Budget.LOW,
            max_tokens=case["max_injected_tokens"],
            enable_trace=True,
            include_entities=True,
            request_context=request_context,
        )
        results = [_fact_to_dict(fact) for fact in recall.results]

        if variant == "entity_tools" and case.get("expected_entities"):
            entities = case["expected_entities"][:2]
            if len(entities) >= 2:
                reason = await memory.entity_reason(
                    bank_id,
                    entities,
                    request_context=request_context,
                    limit=6,
                    fact_types=["world", "experience", "observation"],
                )
                entity_results = [dict(item) for item in reason.get("results", [])]
            else:
                probe = await memory.entity_probe(
                    bank_id,
                    entities[0],
                    request_context=request_context,
                    limit=6,
                    fact_types=["world", "experience", "observation"],
                )
                entity_results = [dict(item) for item in probe.get("results", [])]
            results = entity_results + results

        latency_ms = (time.perf_counter() - started) * 1000
        return score_results(case, variant, results, latency_ms, recall.trace)
    finally:
        await memory.delete_bank(bank_id, request_context=request_context)


def acceptance_summary(case_reports: list[dict[str, Any]]) -> dict[str, Any]:
    improved_scenarios = 0
    forbidden_regressions: list[str] = []
    trust_demoted = False
    entity_reason_hit = False
    structural_win = False

    for report in case_reports:
        baseline = report["variants"]["baseline"]
        best_include = baseline["expected_include_hit_count"]
        for name, metrics in report["variants"].items():
            if name != "baseline" and metrics["expected_include_hit_count"] > best_include:
                improved_scenarios += 1
                break
        for name, metrics in report["variants"].items():
            if name != "baseline" and baseline["forbidden_hit_count"] == 0 and metrics["forbidden_hit_count"] > 0:
                forbidden_regressions.append(f"{report['case_id']}:{name}")
        trust = report["variants"].get("trust")
        if trust:
            baseline_texts = baseline["injected_texts"]
            trust_texts = trust["injected_texts"]
            baseline_stale_rank = next(
                (idx for idx, text in enumerate(baseline_texts) if text.startswith("Stale note: Hindsight")),
                None,
            )
            trust_stale_rank = next(
                (idx for idx, text in enumerate(trust_texts) if text.startswith("Stale note: Hindsight")),
                None,
            )
            if baseline_stale_rank is not None and (
                trust_stale_rank is None or trust_stale_rank > baseline_stale_rank
            ):
                trust_demoted = True
        entity = report["variants"].get("entity_tools")
        if entity and entity["source_contribution"].get("entity_reason", 0) > 0:
            entity_reason_hit = True
        structural = report["variants"].get("structural")
        if structural and structural["expected_include_hit_count"] > baseline["expected_include_hit_count"]:
            latency_limit = baseline["latency_ms"] * 1.25
            structural_win = structural["latency_ms"] <= latency_limit

    return {
        "improved_scenarios": improved_scenarios,
        "forbidden_regressions": forbidden_regressions,
        "trust_feedback_demoted_python_314": trust_demoted,
        "entity_reason_multi_entity_hit": entity_reason_hit,
        "structural_active_win_without_latency_regression": structural_win,
        "passed": improved_scenarios >= 2 and not forbidden_regressions and trust_demoted and entity_reason_hit,
    }


def write_reports(report: dict[str, Any], artifact_dir: Path = ARTIFACT_DIR) -> tuple[Path, Path]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    json_path = artifact_dir / "holographic-memory-poc-report.json"
    md_path = artifact_dir / "holographic-memory-poc-report.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = ["# Holographic Memory POC Report", ""]
    lines.append(f"Generated: `{report['generated_at']}`")
    lines.append("")
    lines.append(f"Acceptance passed: `{report['acceptance']['passed']}`")
    lines.append("")
    for case in report["cases"]:
        lines.append(f"## {case['case_id']}")
        for name, metrics in case["variants"].items():
            lines.append(
                "- "
                f"{name}: include={metrics['expected_include_hit_count']}/{metrics['expected_include_total']}, "
                f"forbidden={metrics['forbidden_hit_count']}, tokens={metrics['injected_token_count']}, "
                f"latency_ms={metrics['latency_ms']:.1f}, sources={metrics['source_contribution']}"
            )
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


async def run_eval(
    memory: MemoryEngine,
    request_context: RequestContext,
    fixture_path: Path = FIXTURE_PATH,
    artifact_dir: Path = ARTIFACT_DIR,
):
    cases = load_cases(fixture_path)
    case_reports = []
    for case in cases:
        variants = {}
        for variant in VARIANTS:
            variants[variant] = await run_variant(memory, case, variant, request_context)
        case_reports.append({"case_id": case["id"], "query": case["query"], "variants": variants})
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "cases": case_reports,
        "acceptance": acceptance_summary(case_reports),
    }
    paths = write_reports(report, artifact_dir)
    report["artifact_paths"] = [str(path) for path in paths]
    return report


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=FIXTURE_PATH)
    parser.add_argument("--artifacts", type=Path, default=ARTIFACT_DIR)
    args = parser.parse_args()

    memory = MemoryEngine()
    await memory.initialize()
    try:
        report = await run_eval(memory, RequestContext(), args.fixture, args.artifacts)
        print(json.dumps(report["acceptance"], indent=2))
    finally:
        await memory.close()


if __name__ == "__main__":
    asyncio.run(_main())
