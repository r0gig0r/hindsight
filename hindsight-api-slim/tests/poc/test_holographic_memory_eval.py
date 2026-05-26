import os

import pytest
import yaml

from tests.poc.run_holographic_memory_eval import load_cases, run_eval

os.environ.setdefault("HINDSIGHT_API_LLM_PROVIDER", "none")


@pytest.mark.asyncio
async def test_holographic_poc_runner_produces_report(memory, request_context, tmp_path):
    case = load_cases()[0]
    fixture = tmp_path / "one_case.yaml"
    fixture.write_text(yaml.safe_dump({"cases": [case]}), encoding="utf-8")

    report = await run_eval(memory, request_context, fixture, tmp_path / "artifacts")
    variants = report["cases"][0]["variants"]
    assert {
        "baseline",
        "entity_labels",
        "entity_tools",
        "entity_labels_entity_tools",
        "trust",
        "structural",
    } <= set(variants)
    assert "entity_labels_improved_scenarios" in report["acceptance"]
    assert "entity_labels_plus_entity_tools_improved_scenarios" in report["acceptance"]
    assert (tmp_path / "artifacts" / "holographic-memory-poc-report.json").exists()
    assert (tmp_path / "artifacts" / "holographic-memory-poc-report.md").exists()
