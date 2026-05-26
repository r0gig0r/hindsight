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
VARIANTS = ("baseline", "entity_labels", "entity_tools", "entity_labels_entity_tools", "trust", "structural")

ENTITY_LABEL_TAXONOMY: list[dict[str, Any]] = [
    {
        "key": "domain",
        "type": "multi-values",
        "description": "Igor usage area for the memory.",
        "tag": True,
        "values": [
            {"value": "hindsight", "description": "Hindsight memory API, schema, retrieval, or daemon work"},
            {"value": "openclaw", "description": "OpenClaw gateway, plugin, or agent memory injection"},
            {"value": "fraud_detector", "description": "Fraud-detector production incident or trading dashboard work"},
            {"value": "home_automation", "description": "Homey, homee, Salus, Qubino, garage, or household automation"},
            {"value": "preference", "description": "Durable Igor working preferences or corrections"},
            {"value": "infra", "description": "Production infrastructure, queues, Redis, NFS, schedulers, or services"},
        ],
    },
    {
        "key": "memory_kind",
        "type": "multi-values",
        "description": "How the memory should be used by future agents.",
        "tag": True,
        "values": [
            {"value": "configuration", "description": "Active version, mode, endpoint, or deployment setting"},
            {"value": "decision", "description": "Human decision or correction that should override alternatives"},
            {"value": "preference", "description": "Durable style or implementation preference"},
            {"value": "runbook", "description": "Operational troubleshooting or remediation procedure"},
            {"value": "incident", "description": "Production incident pattern or diagnosis"},
            {"value": "codebase_fact", "description": "Schema, function, module, or implementation fact"},
            {"value": "stale", "description": "Known stale or superseded fact"},
        ],
    },
    {
        "key": "risk",
        "type": "value",
        "description": "Operational caution level for the memory.",
        "tag": True,
        "optional": True,
        "values": [
            {"value": "active", "description": "Current trusted working fact"},
            {"value": "stale", "description": "Known stale fact to avoid"},
            {"value": "experimental", "description": "Experimental or gated behavior"},
            {"value": "production", "description": "Production-impacting memory"},
        ],
    },
    {
        "key": "system",
        "type": "map",
        "description": "Named technical or home-automation system mentioned in the memory.",
        "fields": {
            "name": {"type": "text", "description": "System, tool, service, device, library, or component name"},
            "environment": {
                "type": "value",
                "description": "Where the system is used.",
                "optional": True,
                "values": [
                    {"value": "local", "description": "Igor's local Mac mini/OpenClaw setup"},
                    {"value": "production", "description": "Production cloud or trading infrastructure"},
                    {"value": "home", "description": "Home automation environment"},
                    {"value": "unknown", "description": "Environment not clear from the text"},
                ],
            },
        },
    },
]


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


def _label_values_for_item(item: dict[str, Any]) -> dict[str, set[str]]:
    text = f"{item.get('text', '')} {' '.join(item.get('tags', []))} {' '.join(item.get('entities', []))}".lower()
    domains: set[str] = set()
    kinds: set[str] = set()
    risks: set[str] = set()
    systems: set[str] = set()

    checks = {
        "hindsight": ("hindsight", "memory_units", "unit_entities", "onnxruntime"),
        "openclaw": ("openclaw", "compact injection", "external api mode"),
        "fraud_detector": ("fraud-detector", "trader dashboard"),
        "home_automation": ("homey", "homee", "qubino", "salus", "garage", "hormann"),
        "preference": ("preference", "prefers", "compact answers", "feature toggles", "measurable evidence"),
        "infra": ("redis", "nfs", "pub/sub", "watchdog scheduler", "vm preemption"),
    }
    for label, needles in checks.items():
        if any(needle in text for needle in needles):
            domains.add(label)

    if "stale" in text:
        kinds.add("stale")
        risks.add("stale")
    if any(term in text for term in ("external api mode", "python 3.13", "python 3.14", "port 9077")):
        kinds.add("configuration")
    if any(term in text for term in ("prefers", "preference", "feature toggles", "compact answers")):
        kinds.add("preference")
    if any(term in text for term in ("should", "must", "requires", "correction")):
        kinds.add("decision")
    if any(term in text for term in ("check ", "restart", "reapply", "watchdog", "runbook")):
        kinds.add("runbook")
    if any(term in text for term in ("incident", "preemption", "production")):
        kinds.add("incident")
        risks.add("production")
    if any(term in text for term in ("memory_units", "unit_entities", "daemon", "plugin", "python")):
        kinds.add("codebase_fact")
    if "experimental" in text:
        risks.add("experimental")
    if not risks:
        risks.add("active")

    known_systems = [
        "Hindsight",
        "OpenClaw",
        "Python",
        "onnxruntime",
        "fraud-detector",
        "Pub/Sub",
        "Redis",
        "NFS",
        "watchdog scheduler",
        "trader dashboard",
        "Homey",
        "homee",
        "Qubino",
        "Salus",
        "Hormann",
    ]
    for name in known_systems:
        if name.lower() in text:
            systems.add(name)

    return {"domain": domains, "memory_kind": kinds, "risk": risks, "system": systems}


def _label_entities_for_item(item: dict[str, Any]) -> tuple[list[str], list[str]]:
    values = _label_values_for_item(item)
    entities: list[str] = []
    tags: list[str] = []
    for key in ("domain", "memory_kind", "risk"):
        for value in sorted(values[key]):
            label = f"{key}:{value}"
            entities.append(label)
            tags.append(label)
    env = "unknown"
    if "fraud_detector" in values["domain"] or "infra" in values["domain"]:
        env = "production"
    elif "home_automation" in values["domain"]:
        env = "home"
    elif "hindsight" in values["domain"] or "openclaw" in values["domain"]:
        env = "local"
    if values["system"]:
        entities.append(f"system:environment:{env}")
    for system_name in sorted(values["system"]):
        entities.append(f"system:name:{system_name}")
    return entities, tags


async def seed_case(
    memory: MemoryEngine, bank_id: str, case: dict[str, Any], *, with_entity_labels: bool = False
) -> dict[str, str]:
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
                label_entities, label_tags = _label_entities_for_item(item) if with_entity_labels else ([], [])
                entities = list(dict.fromkeys([*(item.get("entities") or []), *label_entities]))
                tags = list(dict.fromkeys([*(item.get("tags") or []), *label_tags]))
                text_signals = " ".join(entities)
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
                    tags,
                    text_signals,
                )
                unit_id = row["id"]
                corpus_id_to_unit_id[item["id"]] = str(unit_id)
                for entity_name in entities:
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
            for source_name in merged.get("source_ranks") or {}:
                counts[source_name.replace("_rank", "")] += 1
    return dict(counts)


def score_results(case: dict[str, Any], variant: str, results: list[dict[str, Any]], latency_ms: float, trace=None):
    injected = _inject(results, case["max_injected_tokens"])
    joined = "\n".join(r["text"] for r in injected)
    expected = case.get("expected_included_substrings", [])
    forbidden = case.get("forbidden_substrings", [])
    relevant_results = [r for r in injected if any(fragment.lower() in r["text"].lower() for fragment in expected)]
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
    with_entity_labels = variant in {"entity_labels", "entity_labels_entity_tools"}
    id_map = await seed_case(memory, bank_id, case, with_entity_labels=with_entity_labels)
    try:
        if with_entity_labels:
            await memory._config_resolver.update_bank_config(
                bank_id,
                {"entity_labels": ENTITY_LABEL_TAXONOMY, "entities_allow_free_form": True},
                request_context,
            )
        if variant in {"entity_tools", "entity_labels_entity_tools"}:
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

        if variant in {"entity_tools", "entity_labels_entity_tools"} and case.get("expected_entities"):
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
    entity_labels_improved = 0
    labels_plus_entity_tools_improved = 0

    for report in case_reports:
        baseline = report["variants"]["baseline"]
        entity_tools = report["variants"].get("entity_tools")
        entity_labels = report["variants"].get("entity_labels")
        labels_entity = report["variants"].get("entity_labels_entity_tools")
        if entity_labels and entity_labels["forbidden_hit_count"] <= baseline["forbidden_hit_count"]:
            if entity_labels["expected_include_hit_count"] > baseline["expected_include_hit_count"]:
                entity_labels_improved += 1
        if (
            entity_tools
            and labels_entity
            and labels_entity["forbidden_hit_count"] <= entity_tools["forbidden_hit_count"]
        ):
            if labels_entity["expected_include_hit_count"] > entity_tools["expected_include_hit_count"]:
                labels_plus_entity_tools_improved += 1
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
            if baseline_stale_rank is not None and (trust_stale_rank is None or trust_stale_rank > baseline_stale_rank):
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
        "entity_labels_improved_scenarios": entity_labels_improved,
        "entity_labels_plus_entity_tools_improved_scenarios": labels_plus_entity_tools_improved,
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
