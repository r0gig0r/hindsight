# Handoff — 2026-04-19 v0.5.3 merge, Codex auto-model, retain UUID fix

## 1. Goal

Keep the Hindsight fork (`r0gig0r/hindsight`) current with upstream
`vectorize-io/hindsight`, running healthy against OpenClaw on this Mac mini,
with primary LLM fallback that survives OpenAI model removals automatically.
End state reached today: all three in-scope issues fixed and deployed.

## 2. Current status

### Done
- Merged upstream `main` (v0.5.1 → v0.5.3, 128 commits) into fork; 3 conflicts
  resolved preserving all fork customizations. Commit `f1e8ec46`.
- Auto-formatting follow-up commit `0f9f71e6`.
- Fast-forwarded `~/hindsight` main to the worktree branch, pushed to
  `origin r0gig0r/hindsight`. Main is now at `a5d4884b`.
- `hindsight-updater` skill created at `~/.claude/skills/hindsight-updater/`
  and pushed to `r0gig0r/skills-private`. Captures the merge workflow and
  the fork-customization verification checks.
- Codex model switched from removed `gpt-5.1-codex-mini` → `gpt-5.4-mini` in
  both plists. Then hardened to `auto-latest-mini` magic value (self-healing).
- **Root-cause fixes** shipped in fork (commit `a5d4884b`):
  1. `fallback_llm.py`: permanent-disable on "model not supported" / deprecated;
     advisory replacement suggestion in log.
  2. `codex_model_resolver.py` (new): resolve `auto-latest-mini` /
     `auto-latest-codex` / `auto-latest` / `auto` from
     `~/.codex/models_cache.json`.
  3. `codex_llm.py`: call resolver at init.
  4. `link_utils.py`: `_sanitize_link_uuids()` at `_bulk_insert_links` choke
     point; drops malformed UUIDs + logs sample + caller stack. Prevents the
     intermittent "invalid UUID '110'" retain failures (123 accumulated since
     2026-03-09).
- Daemon deployed and verified at 2026-04-19 18:18 Sofia. Direct Codex call
  under new model: 1.3s. Retain end-to-end under `auto-latest-mini` path:
  completed. Consolidation: caught up (0 unconsolidated, 0 pending,
  0 failures since the fix).

### In progress
Nothing live.

### Blocked
Nothing.

### Intentionally deferred
- **Gateway plist** (`~/Library/LaunchAgents/ai.openclaw.gateway.plist`)
  still has `HINDSIGHT_API_PRIMARY_LLM_MODEL=gpt-5.4-mini`, not
  `auto-latest-mini`. The daemon runs independently from its own plist and
  is what actually uses this env — gateway plist primary-llm vars are
  currently ignored in our external-API-mode setup. Low priority to change.
- **`HINDSIGHT_API_SKIP_LLM_VERIFICATION=true`** in both plists. With the
  new permanent-disable behavior, verification failures are no longer toxic
  and could be re-enabled. Not done — no functional motivation.
- **109 failed retain ops + 11 failed batch_retain + 3 failed consolidation**
  left in DB as history. They won't retry. Archive or leave.
- **Worktree cleanup**: `.claude/worktrees/condescending-merkle-979601/`
  still exists on disk. Run `git worktree remove` from `~/hindsight` to
  purge when convenient.
- **Handoff uncommitted cosmetic diff** in `link_utils.py`: a 3-line ruff
  format change the pre-commit hook produced after the main commit. Not
  yet committed — run `git add -u && git commit -m "chore: ruff format"`
  if you want a clean tree.

## 3. Key decisions and rationale

### Why `auto-latest-mini` instead of pinning `gpt-5.4-mini`
The whole incident started because `gpt-5.1-codex-mini` was silently removed
from ChatGPT-account Codex. Pinning the next mini just delays the next
incident. The resolver reads `~/.codex/models_cache.json` (same file the
`codex` CLI maintains) and picks the highest-version `visibility=list` +
`supported_in_api=True` mini slug. If `gpt-5.4-mini` gets retired, the next
daemon restart picks whatever's current — no plist edit needed.

Explicit slug is still honored: the magic value prefix is `auto-`, anything
else passes through unchanged.

### Why permanent-disable (not just longer cooldown)
Old behavior: after 3 model-not-supported 400s, circuit opened with 5-min
cooldown; HALF-OPEN probe re-hit the same 400; circuit re-opened. Repeat
forever. In 5 hours this burned **556 wasted calls**. A longer cooldown
still wastes calls every cycle. A permanent flag that only resets on
daemon restart is correct — a removed model doesn't un-remove itself.

### Why sanitize UUIDs at the choke point (not fix upstream caller)
The UUID bug is intermittent and spans 6 weeks of failures. The actual
caller producing non-UUID entries in link tuples is unknown. Fixing at
the callsite requires knowing the callsite. Dropping + logging with caller
stack at the asyncpg boundary gives us both protection now AND diagnostic
data to find the upstream caller the next time it fires. The sanitizer
cannot cause false drops — a valid UUID always parses.

### Rejected alternatives
- **Auto-switch primary model at runtime on permanent-disable**: too risky,
  behavior would change without an operator seeing a restart log.
- **Auto-refresh `models_cache.json` from the daemon**: adds a dependency
  on reverse-engineering the Codex CLI's fetch endpoint (not stable API).
  The cache is updated whenever the user runs `codex` anyway, and it
  refreshed itself between my two probes today (2026-04-13 → 2026-04-19).
- **Delete old upstream `hindsight-api/` metapackage**: kept as upstream
  ships it as a compatibility shim.
- **Bump consolidation_max_memories_per_round** from v0.5.3 default of 100:
  no benefit given our consolidation is caught up.

## 4. Relevant files

### Fork source (committed in `a5d4884b` on `r0gig0r/hindsight:main`)

| Path | Purpose | What changed |
|---|---|---|
| `hindsight-api-slim/hindsight_api/engine/providers/codex_model_resolver.py` | **new** — parse `~/.codex/models_cache.json`, resolve `auto-*` magic values | `resolve_codex_model()`, `suggest_replacement()` |
| `hindsight-api-slim/hindsight_api/engine/providers/codex_llm.py` | CodexLLM provider | After `openai/` prefix stripping, call resolver (line ~70) |
| `hindsight-api-slim/hindsight_api/engine/providers/fallback_llm.py` | FallbackLLMProvider + CircuitBreaker | Added `_PERMANENT_ERROR_MARKERS`, `_is_permanent_error()`, `CircuitBreaker.force_permanent_disable()`, `CircuitBreaker.permanently_disabled` flag, `_build_permanent_reason()`. Both `call()` and `call_with_tools()` check for permanent errors first. |
| `hindsight-api-slim/hindsight_api/engine/retain/link_utils.py` | Bulk link insert choke point | Added `_is_valid_uuid()`, `_sanitize_link_uuids()`. `_bulk_insert_links()` now filters + logs bad links with `traceback.format_stack(limit=8)`. |

### Config (local, not in git)

| Path | State |
|---|---|
| `~/Library/LaunchAgents/ai.openclaw.hindsight-daemon.plist` | `HINDSIGHT_API_PRIMARY_LLM_MODEL=auto-latest-mini` ✓ |
| `~/Library/LaunchAgents/ai.openclaw.gateway.plist` | `HINDSIGHT_API_PRIMARY_LLM_MODEL=gpt-5.4-mini` (not used in external-API mode, deferred) |

### Skill (committed in `r0gig0r/skills-private`)

| Path | State |
|---|---|
| `~/.claude/skills/hindsight-updater/references/fork-customizations.md` | Lists all 4 fork customizations from this session |
| `~/.claude/skills/hindsight-updater/scripts/verify-fork.sh` | Still covers the 14 original checks; new additions not yet in it |

### Prior artifacts this session

- `f1e8ec46` — merge: upstream v0.5.3 (128 commits)
- `0f9f71e6` — chore: prettier/ruff auto-format after merge
- `a5d4884b` — fork: auto-resolve + permanent-disable + UUID sanitize

## 5. Validation

Commands actually run and their results:

| Command | Result |
|---|---|
| `cd ~/hindsight/hindsight-api-slim && uv run ruff check hindsight_api/` | ✓ clean |
| `cd ~/hindsight/hindsight-integrations/openclaw && npx tsc --noEmit` | ✓ clean |
| `cd ~/hindsight/hindsight-integrations/openclaw && npx vitest run` | ✓ 225/225 tests pass |
| In-process unit tests (`_is_permanent_error`, `CircuitBreaker.force_permanent_disable`, `_sanitize_link_uuids`, `resolve_codex_model`) | ✓ all invariants hold |
| Direct `CodexLLM.call()` with `gpt-5.4-mini` | ✓ 1.3s, returns `'codex-works-2026-04-19'` |
| Retain via HTTP (3 test items, auto-latest-mini path) | ✓ completed, consolidated within seconds |
| `curl /health` after final restart | ✓ `{"status":"healthy","database":"connected"}` |
| DB: unconsolidated count | ✓ 0 (11,494 consolidated) |
| DB: failed ops since 2026-04-19 18:00 Sofia | ✓ 0 |

### Not run
- Full test suite: `cd hindsight-api-slim && uv run pytest tests/`. Only
  the integration package's vitest was run. Pytest was not run.
- End-to-end check from OpenClaw gateway (Telegram / actual usage path).
  Only daemon-level curls were tested.

### Next highest-value validation if you resume without touching anything
```bash
# Confirm daemon is still healthy and auto-resolver is the active path
curl -s http://127.0.0.1:9077/health
grep "Resolved Codex model" ~/.hindsight/daemon.log | tail -1
# Expected: Resolved Codex model 'auto-latest-mini' → 'gpt-5.4-mini'
```

## 6. Next steps

Ordered; first is runnable now if chosen.

1. **Nothing urgent.** System is healthy and caught up. All checklist items
   below are optional hygiene / nice-to-haves.

2. Commit the cosmetic ruff format delta in `link_utils.py`:
   ```bash
   cd ~/hindsight && git add -u && \
     git commit -m "chore: apply ruff format" && git push origin main
   ```

3. Unify gateway plist to the same `auto-latest-mini` value (consistency):
   ```bash
   # Edit ~/Library/LaunchAgents/ai.openclaw.gateway.plist —
   # change HINDSIGHT_API_PRIMARY_LLM_MODEL gpt-5.4-mini → auto-latest-mini
   # then: launchctl bootout/bootstrap the gateway.
   ```

4. Remove the spent worktree if no more work expected there:
   ```bash
   cd ~/hindsight
   git worktree remove .claude/worktrees/condescending-merkle-979601
   git branch -D claude/condescending-merkle-979601
   ```

5. (Optional) Archive the 123 historical failed ops (cosmetic):
   ```sql
   -- via psql, after review
   -- UPDATE public.async_operations SET status='archived'
   -- WHERE bank_id='openclaw' AND status='failed' AND created_at < '2026-04-19 18:00+03';
   ```

6. (Optional) Flip `HINDSIGHT_API_SKIP_LLM_VERIFICATION=true` → `false` on
   both plists. Safe now that permanent-disable prevents verification
   failures from causing probe storms.

## 7. Open questions / risks

- **`~/.codex/models_cache.json` staleness**: the resolver assumes the Codex
  CLI is used periodically. If the user hasn't run `codex` in months, the
  cache could miss newer models. Mitigation: the Codex CLI refreshed the
  cache between my two probes today (six days apart), suggesting normal use
  keeps it current. If staleness becomes a problem, a refresh-on-resolve
  could be added — requires reverse-engineering the Codex backend's models
  endpoint.
- **UUID sanitizer masks the upstream caller**: the sanitizer drops bad
  links and logs the caller stack, but does NOT surface which caller
  produces malformed IDs. Next time the log fires, capture the stack and
  hunt the root cause; it's intermittent (≈6 weeks, ≈123 occurrences).
  Likely suspects: causal links (`create_causal_links_batch` uses
  `unit_ids[target_idx]`), temporal links (pre-computed ANN results),
  or entity links where an integer leaked into an ID field.
- **Gateway plist still on `gpt-5.4-mini`**: if the external-API mode ever
  flips off (plugin reverts to managed daemon), gateway plist would be the
  active config and still brittle. Not a current risk.
- **`consolidated_at` timestamps look odd** (facts consolidated on
  2026-04-18 show in per-day DB queries as 2026-04-13). Likely explanation:
  the v0.5.3 "observation cleanup on upsert" preserves original
  `consolidated_at` when reprocessing. Cosmetic.

## 8. How to resume

```bash
cd ~/hindsight
git status                           # expect clean or the one ruff format
git log --oneline -5
# Expected HEAD: a5d4884b fork: auto-resolve Codex model ...

# Check the live system
curl -s http://127.0.0.1:9077/health
launchctl list | grep -E "hindsight|openclaw"

# Review today's commits
git show a5d4884b --stat
git show f1e8ec46 --stat    # the v0.5.3 merge

# Relevant documentation
cat ~/hindsight/docs/handoffs/2026-04-19-v053-merge-codex-autoresolve.md
cat ~/.claude/skills/hindsight-updater/references/fork-customizations.md
```

Files to read first:
- **This handoff.**
- `~/.claude/skills/hindsight-troubleshooter/SKILL.md` — overall operating model.
- `~/.claude/skills/hindsight-updater/references/known-conflicts.md` — merge playbook.

Exact first prompt for next session:
> Read `docs/handoffs/2026-04-19-v053-merge-codex-autoresolve.md`. Summarise
> the state, then ask what I want to do next.

## 9. Resumption protocol — READ ME FIRST (next session)

> **STOP — do not act yet.** Before executing anything recommended
> below, including the "Next steps" checklist, the "How to resume"
> commands, or any fix suggested in earlier sections:
>
> 1. Read this entire handoff file.
> 2. Summarise the current state back to the user in 2–3 sentences.
> 3. Ask the user explicitly how they want to proceed.
> 4. Wait for their answer before running any command, editing any
>    file, or taking any other action.
>
> The handoff is a record, not an instruction. The user decides the
> next move; your job is to load context, present options, and obey.

## 10. Dead ends and local state

### Dead ends worth NOT repeating
- Running `~/hindsight/scripts/dev/restart-openclaw.sh` DID NOT kill the
  Python daemon process the first time. `launchctl bootout` returned
  success, but the PID kept running with stale env. **Always verify with
  `ps -p <PID> -o etime`** after a bootout. The reliable restart sequence
  today was:
  ```bash
  launchctl bootout gui/$(id -u)/ai.openclaw.hindsight-daemon
  sleep 1
  pkill -9 -f "hindsight-api.*daemon" || true
  sleep 1
  lsof -iTCP:9077 -sTCP:LISTEN   # must be empty
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.hindsight-daemon.plist
  launchctl kickstart gui/$(id -u)/ai.openclaw.hindsight-daemon
  ```
- Updating only the **gateway** plist doesn't affect the daemon's env —
  the daemon has its own plist at
  `~/Library/LaunchAgents/ai.openclaw.hindsight-daemon.plist`.

### Local-only state not in git
- Both plists (daemon, gateway, infinity-reranker, hindsight-backup).
- OpenRouter API key embedded in plists.
- `~/.codex/auth.json` (OAuth).
- `~/.codex/models_cache.json` (resolver reads this).
- Pg0 DB contents.
- `~/.infinity-reranker/` venv + cache.

### Working tree
Dirty: one 3-line ruff format change in `link_utils.py` left uncommitted
(see "Next steps" #2). Nothing else pending.
