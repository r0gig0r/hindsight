# Handoff — 2026-04-24 Gemma primary live, UUID-orphan fix deployed, single-checkout workflow

## 0. File location (READ FIRST — do not use relative paths)

- **Canonical**: `/Users/igorvaisman/hindsight/docs/handoffs/2026-04-24-gemma-primary-uuid-fix-deployed.md`
- **Mirror**: `/Users/igorvaisman/.claude/handoffs/hindsight/2026-04-24-gemma-primary-uuid-fix-deployed.md`

Worktree-based sessions cannot find this file via the relative
`docs/handoffs/...` path. **Always use the absolute path above.**

Future handoffs in this repo MUST be written to BOTH locations using
absolute paths. The mirror under `~/.claude/handoffs/<repo>/` exists so
a fresh shell with no cwd context can locate the handoff via
`ls ~/.claude/handoffs/`.

## 1. Goal

Run Hindsight on a private, local primary LLM (Gemma-4-26b on LM Studio),
with the OpenRouter `gpt-5-nano` fallback and a circuit breaker, while
keeping the OpenClaw retain pipeline writing memories without errors.
End state reached today: cutover stable, blocking UUID bug fixed,
production unblocked.

## 2. Current status

### Done
- **Primary LLM swapped to local LM Studio** (2026-04-20 10:37 Sofia):
  `lmstudio/gemma-4-26b-a4b-it-ara-abliterated` at
  `http://100.66.118.101:1234/v1` (Tailscale IP). Fallback unchanged
  (`openai/gpt-5-nano` via OpenRouter).
- **UUID orphan-placeholder bug fixed** in `_remap_phase1_results`
  (commit `fa9681c3`). The fork's Phase 1.5 dedup step removes facts
  between Phase 1 (entity resolution) and Phase 2 (write txn); the
  remap fell back to raw placeholder strings (`'4'`, `'2'`, `'6'`)
  when a mapping was missing, leaking integer-strings into asyncpg
  uuid[] binds. Three-day production outage (2026-04-20 → 2026-04-23,
  zero memories persisted in any production bank) ended at 20:55
  Sofia on 2026-04-23.
- **Concurrency cap raised** from 4 → 8 (`HINDSIGHT_API_LLM_MAX_CONCURRENT=8`)
  after the user removed the LM Studio context cap. N=8 burst test
  showed 8/8 success, p95 41 s, zero fallback activations.
- **All work merged to `main` and pushed** (`origin/main` HEAD `ed41835b`):
  - `fa9681c3` fix(retain): drop orphaned placeholders in `_remap_phase1_results`
  - `91bdff32` chore: ruff format `link_utils.py`
  - `2b81c10a` Merge `claude/condescending-merkle-979601` into main
  - `7e8d163f` docs: add 2026-04-19 v0.5.3 merge handoff
  - `ed41835b` chore: add bash hygiene + LLM probe scripts (`env.sh`,
    `scripts/test-*.py`, `scripts/diag-nemotron.py`, CLAUDE.md
    BASH HYGIENE PROTOCOL §0–§10)
- **Worktree workflow abandoned.** The `condescending-merkle-979601`
  worktree was removed (directory + `.git/worktrees/` metadata both
  gone). Working in `~/hindsight` directly on `main` from now on.
- **MEMORY.md** updated with the new primary config, the workflow
  decision, the `CCD_SESSION_ROOT` lesson, and the `patch-launchagent.sh`
  staleness note.
- **Live verification**: N=8 burst created 48 memories across 8 fresh
  banks, all consolidated, zero failed ops, zero fallback. Recall +
  reflect probed against `gemma-smoke-test` bank work end-to-end.

### In progress
Nothing live.

### Blocked
- **Bash tool wedged in *this* session.** `git worktree remove --force`
  deleted the directory the Bash tool was using as its persistent cwd.
  Every subsequent `Bash` invocation fails with
  `Path "/Users/igorvaisman/hindsight/.claude/worktrees/condescending-merkle-979601" does not exist`
  before any command runs. Read/Edit/Write still work because they
  take absolute paths. The block clears in any fresh session.

### Intentionally deferred
- **`env.sh` has one uncommitted edit** (added `CCD_SESSION_ROOT="/private/tmp/claude-501"`
  with a `# why` comment, to satisfy the Stop-hook token-economy
  rule). Couldn't commit it because the Bash tool is wedged. Run
  `git -C ~/hindsight diff env.sh` first session, then commit if it
  looks right.
- **Possible stale local branch** `claude/condescending-merkle-979601`
  may still appear in `git branch`. The worktree it backed is gone and
  the branch is fully merged into main; harmless. Run
  `git -C ~/hindsight branch -D claude/condescending-merkle-979601`
  if you want a clean list.
- **`git worktree prune`** to clear any leftover admin entries.
- **TEI reranker (Infinity sidecar)** at port 7997 has been returning
  `404 /info` and `422 /rerank` for some time. Pre-existing, unrelated
  to this work. Surfaces as recall errors on the `openclaw` bank.
  See `~/.infinity-reranker/` and `~/hindsight/scripts/dev/start-infinity.sh`.
- **`patch-launchagent.sh`** at `~/.openclaw/hooks/` still has the OLD
  Codex env vars hardcoded in its `ENV_VARS` whitelist (lines 53–67).
  It patches the *gateway* plist (which is currently ignored in
  external-API mode), but if OpenClaw ever regenerates the gateway
  plist AND we flip back to managed-daemon mode, this will restore
  obsolete config. Update before any such transition.
- **Test bank cleanup**: 11 test banks in DB — `gemma-burst-1..8`,
  `gemma-n8-test`, `gemma-smoke-test`, `gemma-uuid-fix-test`. Total
  ~70 memories. Cosmetic; delete via control plane or SQL when convenient.
- **pytest** not run for the UUID fix. Only the pre-commit lint
  (`ruff check`) passed. The three regression tests in
  `TestRemapPhase1Results` exist but were not executed in this session.
  Run `cd ~/hindsight/hindsight-api-slim && uv run pytest tests/test_retain_orchestrator_mapping.py -v`
  for confirmation.

## 3. Key decisions and rationale

### Why `lmstudio` provider, not `openai` with custom base URL
`openai_compatible_llm.py` (line 381) explicitly skips `json_object`
grammar enforcement for `lmstudio`, which LM Studio rejects with
`400: 'response_format.type' must be 'json_schema' or 'text'`. Using
`provider=openai` with an LM Studio base URL would send the unsupported
flag and fail. The `lmstudio` code path also defaults to `max_tokens`
instead of `max_completion_tokens`, which LM Studio actually accepts.

### Why `HINDSIGHT_API_LLM_MAX_CONCURRENT=8`
Pre-context-cap test showed Gemma's KV cache topped out at N=3
(N=4 → all 400 context-exceeded). After the user removed the context
cap, N=16 succeeded but throughput plateaued at ~0.34 req/s starting
at N≈4. The 60 s primary timeout breaks under sustained N>8 because
queue depth pushes individual calls past 60 s. Eight is the sweet
spot: real production prompts (~3–5 K input tokens) settle at p95 ≈
41 s with all 8 in flight, leaving 19 s of headroom before timeout.

### Why fix `_remap_phase1_results` instead of extending `_sanitize_link_uuids`
The 2026-04-19 sanitizer (`a5d4884b`) only covers `_bulk_insert_links`.
The actual failure was upstream of that: `_remap_phase1_results` used
`mapping.get(unit_id, unit_id)`, which returned the raw placeholder
string when dedup had removed the entry. The bad string then flowed
into `entity_resolver.link_units_to_entities_batch` → asyncpg →
`ValueError: invalid UUID '4'`. Adding a sanitizer in that codepath
too would mask a real bug; fixing the remap to drop unmapped entries
+ log dedup-collateral counts surfaces dedup behavior visibly without
silent data loss.

### Why drop `_remap_phase1_results` orphans (not error)
By the time remap runs, dedup has already decided those facts are
duplicates and the orchestrator has chosen not to write them. The
remap is supposed to translate *kept* placeholders to UUIDs — orphans
mean the upstream dedup correctly discarded them, so the right action
is to drop them from the remap result, not propagate them. The new
info-level log line lets us watch dedup volume without crashing.

### Why single-checkout workflow (no more worktrees)
Worktree deletion broke this session's Bash tool irrecoverably. The
benefit of worktrees (isolated branches per feature) was modest
because we already do single-feature commits and the merge step is
trivial. The cost (cwd footguns, disk duplication, extra git
metadata, branch-cleanup ceremony) is real. Net: not worth it for
this repo.

### Rejected alternatives
- **Roll back to Codex primary** while fixing the UUID bug: tempting
  but the bug is LLM-agnostic and rollback wouldn't have fixed it.
- **Lower concurrency to 2 + raise timeout to 120 s**: addresses
  symptoms (timeouts) without addressing the root cause (orphans).
- **Switch to non-reasoning model on LM Studio** (`gemma-4-31b`,
  `huihui-qwen3-vl-30b`): premature — Gemma-4-26b already meets
  quality bar. Revisit if quality drifts.
- **Auto-disable thinking on Nemotron** via `extra_body`: probed,
  doesn't work — LM Studio's thinking flag is a per-model UI setting
  not exposed via API. Switching back to Nemotron would require
  manual UI re-toggle.

## 4. Relevant files

### Code changes (committed in `ed41835b` and earlier on `main`)

| Path | Purpose | What changed |
|---|---|---|
| `hindsight-api-slim/hindsight_api/engine/retain/orchestrator.py` | Streaming retain orchestrator | `_remap_phase1_results` now uses `.get(k)` with drop-if-None for all three remap loops (`entity_to_unit`, `unit_to_entity_ids`, `semantic_ann_links`). Dedup-collateral drop count logged at info level. Lines ~1390–1450. |
| `hindsight-api-slim/tests/test_retain_orchestrator_mapping.py` | Regression tests | New `TestRemapPhase1Results` class: `test_drops_orphaned_placeholders_after_dedup` (the prod crash), `test_no_dedup_keeps_all_entries`, `test_empty_phase1_noop`. |
| `hindsight-api-slim/hindsight_api/engine/retain/link_utils.py` | Bulk link insert choke point | Cosmetic ruff format: one multiline `bad_samples.append()` collapsed (commit `91bdff32`). Behavior unchanged. The `_sanitize_link_uuids` from 2026-04-19 still in place at line 77. |

### New files in main (committed in `ed41835b`)

| Path | Purpose |
|---|---|
| `env.sh` | Project-root idempotent env script. Defines `LLM_BASE`, `LLM_API`, `LLM_MODEL`, `LLM_KEY`, `LLM_OUT`, `CCD_SESSION_ROOT` (the last is uncommitted — see §2). Exports universal-quieting + Python noise vars. Helpers `llm_post` / `llm_get`. |
| `CLAUDE.md` | Project memory. Now contains the MANDATORY BASH HYGIENE PROTOCOL §0–§10 (appended via SessionStart hook). |
| `scripts/test-nemotron.py` | Full retain+tools+concurrency suite via openai SDK (matches Hindsight call shape). |
| `scripts/test-nemotron-v2.py` | Round 2 with `max_tokens=8000`, handles `reasoning_content` side channel. |
| `scripts/test-nemotron-concurrency.py` | N=1..N concurrency sweep — KV/GPU ceiling finder. |
| `scripts/diag-nemotron.py` | Reasoning-mode probe (thinking on/off, content vs reasoning_content split, response_format support). |
| `scripts/test-quality.py` | 4 retain cases (simple, timestamps, causal, multi-entity) + reflect tool loop with grading rubric. |
| `scripts/test-quality-v2.py` | Same with Hindsight's production forced-tool reflect sequence (`search_observations` → `recall` → auto). |
| `docs/handoffs/2026-04-19-v053-merge-codex-autoresolve.md` | Prior-session handoff (`7e8d163f`). |

### Live config (local, NOT in git)

| Path | State |
|---|---|
| `~/Library/LaunchAgents/ai.openclaw.hindsight-daemon.plist` | `HINDSIGHT_API_PRIMARY_LLM_PROVIDER=lmstudio`, `MODEL=gemma-4-26b-a4b-it-ara-abliterated`, `BASE_URL=http://100.66.118.101:1234/v1`, `API_KEY=local`, `LLM_MAX_CONCURRENT=8`. ✓ |
| `~/.openclaw/hooks/backups/ai.openclaw.hindsight-daemon.plist.20260420_103642.bak` | Pre-cutover backup (Codex `auto-latest-mini`). Use to roll back. |
| `~/Library/LaunchAgents/ai.openclaw.gateway.plist` | Still has Codex primary vars; ignored in external-API mode. Don't bother updating. |
| `~/.openclaw/hooks/patch-launchagent.sh` | Patches *gateway* plist with stale OLD Codex env. Update lines 53–67 before next OpenClaw extension upgrade. |
| `~/.hindsight/daemon.log` | All structured logs. Use `awk '$0 >= "YYYY-MM-DD HH:MM"'` to slice by time. |

### MEMORY (user-private, not git)

| Path | What's there |
|---|---|
| `~/.claude/projects/-Users-igorvaisman-hindsight/memory/MEMORY.md` | Updated with: Gemma primary config + verification, LM-Studio thinking-per-model gotcha, rollback path, `CCD_SESSION_ROOT` lesson, single-checkout workflow decision, `patch-launchagent.sh` staleness, prior `Workflow` section. |

## 5. Validation

Commands actually run and their results:

| Command | Result |
|---|---|
| `git push origin main` | ✓ `a5d4884b..ed41835b main -> main` |
| Pre-commit lint on `fa9681c3` (`ruff check`, etc.) | ✓ all lints passed |
| Pre-commit lint on `ed41835b` | ✓ all lints passed |
| `curl /health` after restart | ✓ `{"status":"healthy","database":"connected"}` |
| Live retain via HTTP (1 item) → `gemma-n8-test` bank | ✓ HTTP 200, 8.7 s wall, 1 memory persisted |
| Live burst N=8 retains via HTTP → 8 fresh banks | ✓ 8/8 HTTP 200, wall 41 s, 24 memories + 24 observations persisted |
| Daemon log scope/model attribution post-fix | ✓ all calls `lmstudio/gemma-4-26b-...`, zero fallback |
| Daemon log circuit-breaker state post-fix | ✓ no new OPEN events |
| Reflect probe with forced-tool sequence on smoke bank | ✓ 3 turns, 571-char synthesis, no hallucinations |
| `_sanitize_link_uuids` still present in running module | ✓ `link_utils.py:77, 136` |
| `hindsight_api.__file__` points at main checkout | ✓ `/Users/igorvaisman/hindsight/hindsight-api-slim/hindsight_api/__init__.py` |

### Not run
- `cd ~/hindsight/hindsight-api-slim && uv run pytest tests/test_retain_orchestrator_mapping.py -v`
  — the three new regression tests have NOT been executed. Highest-value
  next validation if you don't change anything else.
- `cd ~/hindsight/hindsight-api-slim && uv run pytest tests/` — full
  suite not run.
- Real OpenClaw production traffic — no user activity hit `openclaw`
  bank in the post-fix window. The fix's behavior under live OpenClaw
  load is unproven (it works for synthetic bursts; OpenClaw may stress
  different code paths).

### Next highest-value validation if you resume without touching anything
```bash
cd /Users/igorvaisman/hindsight
git status                      # expect: only env.sh modified
git log --oneline -6
curl -s http://127.0.0.1:9077/health
# Confirm primary still healthy:
grep "Primary LLM fallback enabled" ~/.hindsight/daemon.log | tail -1
# Confirm no new UUID errors since 2026-04-23 20:55 Sofia restart:
awk '$0 >= "2026-04-23 20:55"' ~/.hindsight/daemon.log | grep -i "invalid uuid" | head
# Run the new regression tests:
cd hindsight-api-slim && uv run pytest tests/test_retain_orchestrator_mapping.py -v
```

## 6. Next steps

Ordered. First item is runnable immediately.

1. **Verify state and commit the deferred env.sh edit:**
   ```bash
   cd /Users/igorvaisman/hindsight
   git status
   git diff env.sh         # expect: +CCD_SESSION_ROOT block with # why
   git add env.sh
   git commit -m "chore(env.sh): bind CCD_SESSION_ROOT to silence repeated path"
   git push origin main
   ```

2. **Run the new regression tests** to convert "lint passed" into
   "tests pass":
   ```bash
   cd /Users/igorvaisman/hindsight/hindsight-api-slim
   uv run pytest tests/test_retain_orchestrator_mapping.py -v
   ```

3. **Cleanup leftover branch / worktree admin** (harmless if skipped):
   ```bash
   cd /Users/igorvaisman/hindsight
   git worktree prune -v
   git branch -D claude/condescending-merkle-979601 2>/dev/null || true
   git remote prune origin
   ```

4. **(Optional) Archive test banks.** They're cosmetic but bulk up the
   bank list:
   ```bash
   PGPASSWORD=hindsight ~/.pg0/installation/18.1.0/bin/psql -h localhost -p 5432 -U hindsight -d hindsight -c "
     DELETE FROM public.memory_units WHERE bank_id LIKE 'gemma-%';
     DELETE FROM public.banks WHERE bank_id LIKE 'gemma-%';
   "
   ```

5. **(Optional, when ready) Investigate the TEI reranker** at port 7997.
   Recalls on `openclaw` bank fail with `404 /info`. Pre-existing.
   ```bash
   ls ~/.infinity-reranker/
   ~/hindsight/scripts/dev/start-infinity.sh status
   ```

6. **(Optional) Update `patch-launchagent.sh`** lines 53–67 to reflect
   the Gemma primary, so a future OpenClaw extension update doesn't
   restore stale Codex config to the gateway plist. Currently irrelevant
   because gateway plist is unused, but a landmine for the future.

7. **(Optional) Consolidate the three handoffs** (`2026-04-19`,
   `2026-04-24`, plus whatever's next) into a `docs/handoffs/README.md`
   index when there are >5 of them.

## 7. Open questions / risks

- **Is the timeout truly safe at N=8?** Burst test showed p95 = 41 s,
  19 s under the 60 s timeout. But OpenClaw may issue larger documents
  (input_tokens up to 5077 observed in production) that push individual
  calls higher. If we see a new round of `Primary LLM timed out`
  warnings, lower to N=6 or raise the timeout — both env knobs exist.
- **Dedup-collateral log volume**: the new info-level log line in
  `_remap_phase1_results` could become noisy under sustained dedup
  activity. Watch `~/.hindsight/daemon.log` for "[remap] dropped"
  patterns — if it's >20/min, downgrade to debug.
- **Has OpenClaw actually retried the failed retains?** Probably not —
  the 115 failed `retain` ops from the 3-day outage have `retry_count=3`
  and are terminal. Their underlying conversations are LOST (not in
  any memory bank). If you want to backfill, you'd need to re-feed
  OpenClaw's source logs.
- **Reranker outage might be hiding a worse issue.** The TEI 404 has
  been failing all `recall_exp` calls on `openclaw`. We patched retain
  but recall is degraded; reflect on `openclaw` would also fail. Out
  of scope for this work but a known production issue.
- **Memory file `MEMORY.md` is the manually edited inline form**, not
  the per-fact file convention. If you ever migrate, the inline edits
  from this session will need to be re-split.

## 8. How to resume

```bash
cd /Users/igorvaisman/hindsight   # NOT a worktree
git status                        # expect: only env.sh dirty
git log --oneline -6              # expect HEAD = ed41835b or descendant
git remote -v                     # confirm origin = r0gig0r/hindsight

# Live system check
curl -s http://127.0.0.1:9077/health
launchctl list | grep -E "hindsight|openclaw"
ps -p $(pgrep -f "hindsight-api.*daemon" | head -1) -o pid,etime,command 2>/dev/null

# Look at very recent log activity
tail -30 ~/.hindsight/daemon.log

# Read this handoff (absolute path — relative won't work from worktrees):
cat /Users/igorvaisman/hindsight/docs/handoffs/2026-04-24-gemma-primary-uuid-fix-deployed.md
# Or via the cross-cwd mirror:
cat ~/.claude/handoffs/hindsight/2026-04-24-gemma-primary-uuid-fix-deployed.md
```

Files to read first:
1. **This handoff** (absolute path above).
2. `~/.claude/projects/-Users-igorvaisman-hindsight/memory/MEMORY.md`
   — current LLM config + workflow decisions.
3. `/Users/igorvaisman/hindsight/CLAUDE.md` — project rules + hygiene
   protocol (now in main).
4. `/Users/igorvaisman/hindsight/docs/handoffs/2026-04-19-v053-merge-codex-autoresolve.md`
   — the prior session (Codex auto-resolve, original UUID sanitizer,
   plist sequence).

Exact first prompt to paste into the next session:

> Read `/Users/igorvaisman/hindsight/docs/handoffs/2026-04-24-gemma-primary-uuid-fix-deployed.md`.
> Summarise the state in 2–3 sentences, then ask me what to do next.

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

## 10. Dead ends and local-only state

### Dead ends worth NOT repeating

- **`git worktree remove --force` while the Bash tool's persistent
  cwd is *inside* the worktree** wedges the Bash tool for the rest
  of the session. `cd` inside a Bash command does not help — the
  tool fails before it runs `cd`. Workaround: only remove worktrees
  from a fresh shell, OR avoid worktrees entirely (the current plan).
- **`response_format={"type": "json_object"}` on LM Studio** → 400
  `'response_format.type' must be 'json_schema' or 'text'`. The
  `lmstudio` provider already skips this; don't re-add.
- **Forcing tool with `tool_choice={"type":"function","function":{"name":...}}`
  on LM Studio**: rejected. The `openai_compatible_llm.py` already
  normalises this to `tool_choice="required"` + filtered tool list.
- **`extra_body={"thinking": False}`** on Nemotron-3-nano: doesn't
  disable thinking. LM Studio's thinking flag is a per-model UI
  setting only.
- **Restart sequence shortcuts**: `~/hindsight/scripts/dev/restart-openclaw.sh`
  alone may not kill the Python daemon. Use the verified sequence
  from the prior handoff (`launchctl bootout` + `pkill -9` + verify
  port + `bootstrap` + `kickstart`).

### Local-only state not in git

- LaunchAgent plists (daemon, gateway, infinity-reranker, hindsight-backup).
- OpenRouter API key embedded in plists.
- LM Studio app + loaded model (must be running for the daemon to work).
- Pg0 DB contents at `~/.pg0/`.
- `~/.infinity-reranker/` venv (currently broken — see §7).
- `~/.openclaw/hooks/backups/` — plist backups including the rollback
  point for this cutover.
- `~/.hindsight/daemon.log` (and `daemon.err.log`).
- Shell `env.sh` exports — only loaded inside sessions that source it;
  daemon does NOT read this file (it reads the plist's `EnvironmentVariables`).

### Working tree state

Dirty: `env.sh` only — one uncommitted edit adding `CCD_SESSION_ROOT`
with a `# why` comment to satisfy the Stop-hook token-economy rule.
Commit suggestion in §6 step 1. Otherwise clean.

## 11. Skill maintenance note

The handoff skill itself was updated only via this run's writing
process; no edits to `~/.claude/skills/handoff/SKILL.md` were made.
Skill push step from the SKILL.md is therefore not required this
time. If a future session edits the skill, follow the push procedure
in `~/.claude/skills/handoff/SKILL.md`.
