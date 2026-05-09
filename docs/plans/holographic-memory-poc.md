# Holographic-Inspired Hindsight Enhancements POC Plan

## Summary

Implement Hermes Holographic ideas as optional additions to Hindsight, never replacements. Every feature stays behind a default-off toggle, emits measurable evidence, and is validated by a POC fixture suite that compares baseline injected memory against enhanced injected memory for Igor-style OpenClaw usage.

Build four additions in this order:

1. Per-memory feedback and trust scoring.
2. Entity probe/reason retrieval tools.
3. Memory conflict/contradiction detection.
4. Experimental structural HRR retrieval arm.

Default behavior must remain exactly as today unless a feature flag is enabled.

## Feature Toggles

API/bank config flags, all default `false`:

- `experimental_memory_feedback_enabled`
- `experimental_trust_rerank_enabled`
- `experimental_entity_tools_enabled`
- `experimental_conflict_detection_enabled`
- `experimental_structural_retrieval_enabled`
- `experimental_structural_shadow_enabled`

OpenClaw plugin flags, all default preserving current behavior:

- `experimentalHolographicEnhancements?: boolean`
- `experimentalRecallShadow?: boolean`
- `experimentalRecallVariant?: "baseline" | "entity_tools" | "trust" | "structural"`

## API Surface

- `POST /v1/default/banks/{bank_id}/memories/{memory_id}/feedback`
- `GET /v1/default/banks/{bank_id}/entities/{entity}/probe`
- `POST /v1/default/banks/{bank_id}/entities/reason`
- `GET /v1/default/banks/{bank_id}/memory-conflicts`

Disabled endpoints return a clear disabled response and do not mutate state.

## Acceptance Gate

- Enhanced variants must not inject forbidden facts when baseline does not.
- Enhanced variants must improve expected-include hits in at least two scenarios.
- Trust feedback must demote the stale Python `3.14` fact after one `unhelpful` event.
- Entity reason must retrieve the correct multi-entity memory when baseline top-k misses it.
- Structural retrieval remains shadow-only unless it beats baseline in at least one multi-entity scenario without latency regression above 25%.

## Test Commands

```bash
cd hindsight-api-slim
uv run pytest tests/test_holographic_*.py tests/poc/test_holographic_memory_eval.py -v

cd ../hindsight-integrations/openclaw
npm test -- --run src/index.test.ts src/memory-formatter.test.ts src/holographic-poc.test.ts
```
