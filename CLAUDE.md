# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Hindsight is an agent memory system that provides long-term memory for AI agents using biomimetic data structures. Memories are organized as:
- **World facts**: General knowledge ("The sky is blue")
- **Experience facts**: Personal experiences ("I visited Paris in 2023")
- **Mental models**: Consolidated knowledge synthesized from facts ("User prefers functional programming patterns")

## Fork Customizations (PROTECT DURING UPSTREAM MERGES)

This is a fork of `vectorize-io/hindsight`. The following files contain critical fork customizations
that upstream may remove or break during merges. **Always verify these survive after any merge:**

| File | Customization | Why |
|------|--------------|-----|
| `hindsight-api-slim/.../retain/orchestrator.py` | `duplicate_checker_fn` parameter + `[FORK] Deduplication` step in streaming Phase 1.5 | Upstream removed inline dedup (commit `5eb484fb`). Without it, ~10K duplicates/day are created. Dedup runs between Phase 1 (entity resolution) and Phase 2 (write txn) in the streaming pipeline. |
| `hindsight-api-slim/.../retain/deduplication.py` | Within-batch dedup + time-bucketed DB dedup | Must be imported by orchestrator. If orchestrator is refactored, re-wire the import. |
| `hindsight-api-slim/.../engine/memory_engine.py` | `_find_duplicate_facts_batch` + global dedup fallback, passed as `duplicate_checker_fn` to orchestrator | Upstream removed the call site. Ensure the function still exists and is passed to `retain_batch()`. |
| `hindsight-api-slim/.../engine/memory_engine.py` | FallbackLLMProvider wiring (primary LLM) | Primary Codex LLM with circuit breaker fallback to OpenRouter. |
| `hindsight-api-slim/.../engine/providers/fallback_llm.py` | FallbackLLMProvider + CircuitBreaker + string-based rate limit detection | Fork-only file. `CodexRateLimitError` was removed upstream; uses string matching instead. |
| `hindsight-api-slim/.../engine/providers/codex_llm.py` | Codex OAuth LLM provider (shared with upstream since v0.5.0) | Upstream now ships this file too. Our fork's additions are merged. |
| `hindsight-api-slim/.../consolidation/prompts.py` | `build_single_fact_prompt` + single-fact prompt templates | Fork-only addition for weaker fallback models. |
| `hindsight-api-slim/.../consolidation/consolidator.py` | `_SingleFactAction/Response` models, `_is_duplicate_create`, single-fact mode routing | Activates when circuit breaker fallback is active (`is_fallback_active`). |
| `hindsight-integrations/openclaw/src/index.ts` | `recallExp` on `BankScopedClient` (direct HTTP), Jaccard dedup + compact formatting | `recall_exp` not in generated SDK; uses direct HTTP call with fallback to `client.recall()`. |
| `hindsight-integrations/openclaw/src/memory-formatter.ts` | `stripMarkdown`, `deduplicateByJaccard`, `formatMemoriesCompact` | Fork-only file. 64% character reduction on injected memories. |

**Post-merge verification:**
```bash
# 1. Dedup is wired in
grep "duplicate_checker_fn" hindsight-api-slim/hindsight_api/engine/retain/orchestrator.py
# 2. Dedup module is imported
grep "from .deduplication import" hindsight-api-slim/hindsight_api/engine/retain/orchestrator.py
# 3. Memory engine passes the checker
grep "duplicate_checker_fn=self._find_duplicate_facts_batch" hindsight-api-slim/hindsight_api/engine/memory_engine.py
# 4. Single-fact prompt exists
grep "build_single_fact_prompt" hindsight-api-slim/hindsight_api/engine/consolidation/prompts.py
# 5. Fallback LLM exists
test -f hindsight-api-slim/hindsight_api/engine/providers/fallback_llm.py && echo OK
```

## Development Commands

### Local Development (API + UI)
```bash
# Start both API server and control plane UI
./scripts/dev/start.sh
```

### API Server (Python/FastAPI)
```bash
# Start API server only (loads .env automatically)
./scripts/dev/start-api.sh

# Run all tests (parallelized with pytest-xdist)
cd hindsight-api-slim && uv run pytest tests/

# Run specific test file
cd hindsight-api-slim && uv run pytest tests/test_http_api_integration.py -v

# Run single test function
cd hindsight-api-slim && uv run pytest tests/test_retain.py::test_retain_simple -v

# Lint and format
cd hindsight-api-slim && uv run ruff check .
cd hindsight-api-slim && uv run ruff format .

# Type checking (uses ty - extremely fast type checker from Astral)
cd hindsight-api-slim && uv run ty check hindsight_api/
```

### Control Plane (Next.js)
```bash
./scripts/dev/start-control-plane.sh
# Or manually:
cd hindsight-control-plane && npm run dev
```

### Documentation Site (Docusaurus)
```bash
./scripts/dev/start-docs.sh
```


### Generating Clients/OpenAPI
```bash
# Regenerate OpenAPI spec after API changes (REQUIRED after changing endpoints)
./scripts/generate-openapi.sh

# Regenerate all client SDKs (Python, TypeScript, Rust)
./scripts/generate-clients.sh
```

### Benchmarks
```bash
# Accuracy benchmarks
./scripts/benchmarks/run-longmemeval.sh
./scripts/benchmarks/run-locomo.sh

# Performance benchmarks
./scripts/benchmarks/run-consolidation.sh
./scripts/benchmarks/run-retain-perf.sh --document <path>  # Requires API server running

# Results viewer
./scripts/benchmarks/start-visualizer.sh  # View results at localhost:8001
```

## Architecture

### Monorepo Structure
- **hindsight-api-slim/**: Core FastAPI server with memory engine (Python, uv)
- **hindsight-control-plane/**: Admin UI (Next.js, npm)
- **hindsight-cli/**: CLI tool (Rust, cargo, uses progenitor for API client)
- **hindsight-clients/**: Generated SDK clients (Python, TypeScript, Rust)
- **hindsight-docs/**: Docusaurus documentation site
- **hindsight-integrations/**: Framework integrations (LiteLLM, CrewAI, LangGraph, Pydantic AI, AG2, Claude Code, etc.)
- **hindsight-dev/**: Development tools and benchmarks

### Core Engine (hindsight-api-slim/hindsight_api/engine/)
- `memory_engine.py`: Main orchestrator for retain/recall/reflect operations
- `llm_wrapper.py`: LLM abstraction supporting OpenAI, Anthropic, Gemini, VertexAI, Groq, MiniMax, Ollama, LM Studio, LiteLLM, Claude Code
- `embeddings.py`: Embedding generation (local sentence-transformers or TEI)
- `cross_encoder.py`: Reranking (local or TEI)
- `entity_resolver.py`: Entity extraction and normalization
- `query_analyzer.py`: Query intent analysis

**retain/**: Memory ingestion pipeline
- `orchestrator.py`: Coordinates the retain flow
- `fact_extraction.py`: LLM-based fact extraction from content
- `link_utils.py`: Entity link creation and management

**search/**: Multi-strategy retrieval
- `retrieval.py`: Main retrieval orchestrator
- `graph_retrieval.py`: Graph retrieval abstract base class
- `link_expansion_retrieval.py`: Link expansion graph retrieval
- `fusion.py`: Reciprocal rank fusion for combining results
- `reranking.py`: Cross-encoder reranking

### API Layer (hindsight-api-slim/hindsight_api/api/)
- `http.py`: FastAPI HTTP routers for all REST endpoints
- `mcp.py`: Model Context Protocol server implementation

Main operations:
- **Retain**: Store memories, extracts facts/entities/relationships
- **Recall**: Retrieve memories via 4 parallel strategies (semantic, BM25, graph, temporal) + reranking
- **Reflect**: Disposition-aware reasoning using memories and mental models.

### Database
PostgreSQL with pgvector. Schema managed via Alembic migrations in `hindsight-api-slim/hindsight_api/alembic/`. Migrations run automatically on API startup.

Key tables: `banks`, `memory_units`, `documents`, `entities`, `entity_links`

### Adding Database Migrations

1. **Create a new migration file** in `hindsight-api-slim/hindsight_api/alembic/versions/`:
   - File name format: `<revision_id>_<description>.py` (e.g., `f1a2b3c4d5e6_add_new_index.py`)
   - Use a unique hex revision ID (12 chars)
   - Set `down_revision` to the previous migration's revision ID

2. **Migration template**:
   ```python
   """Description of the migration

   Revision ID: f1a2b3c4d5e6
   Revises: <previous_revision_id>
   Create Date: YYYY-MM-DD
   """
   from collections.abc import Sequence
   from alembic import context, op

   revision: str = "f1a2b3c4d5e6"
   down_revision: str | Sequence[str] | None = "<previous_revision_id>"
   branch_labels: str | Sequence[str] | None = None
   depends_on: str | Sequence[str] | None = None

   def _get_schema_prefix() -> str:
       """Get schema prefix for table names (required for multi-tenant support)."""
       schema = context.config.get_main_option("target_schema")
       return f'"{schema}".' if schema else ""

   def upgrade() -> None:
       schema = _get_schema_prefix()
       op.execute(f"CREATE INDEX ... ON {schema}table_name(...)")

   def downgrade() -> None:
       schema = _get_schema_prefix()
       op.execute(f"DROP INDEX IF EXISTS {schema}index_name")
   ```

3. **Run migrations locally**:
   ```bash
   # Set database URL and run migrations for the base schema plus all tenants
   uv run hindsight-admin run-db-migration

   # Run on a specific tenant schema
   uv run hindsight-admin run-db-migration --schema tenant_xyz
   ```

## Key Conventions

### Code Quality

**Before writing code, read `.claude/skills/code-review/SKILL.md`** for the full coding standards (Python style, type safety, TypeScript style, general principles).

**Always run the lint script after making Python or TypeScript/Node changes:**
```bash
./scripts/hooks/lint.sh
```

**After completing any implementation work, run `/code-review`** to verify your changes against project standards (missing tests, dead code, type safety, etc.). Fix any "must fix" issues before considering the task done.

**MANDATORY: Run `/code-review` before pushing code or creating a pull request.** Do not push or create a PR until all "must fix" issues are resolved.

### Memory Banks
- Each bank is an isolated memory store (like a "brain" for one user/agent)
- Banks have dispositions (skepticism, literalism, empathy traits 1-5) affecting reflect
- Banks can have background context
- Bank isolation is strict - no cross-bank data leakage

### API Design
- All endpoints operate on a single bank per request
- Multi-bank queries are client responsibility to orchestrate
- Disposition traits only affect reflect, not recall

### Control Plane API Routes

When adding or modifying parameters in the dataplane API (hindsight-api), you must also update the control plane routes that proxy to it:

1. **API Routes** (`hindsight-control-plane/src/app/api/`):
   - `recall/route.ts` - proxies to `/v1/default/banks/{bank_id}/memories/recall`
   - `reflect/route.ts` - proxies to `/v1/default/banks/{bank_id}/reflect`
   - `memories/retain/route.ts` - proxies to `/v1/default/banks/{bank_id}/memories/retain`
   - Other routes follow the same pattern

2. **Client types** (`hindsight-control-plane/src/lib/api.ts`):
   - Update the TypeScript type definitions for `recall()`, `reflect()`, `retain()` etc.

3. **Checklist when adding new API parameters**:
   - Add parameter extraction in the route handler (destructure from `body`)
   - Pass the parameter to the SDK call
   - Update the client type definition in `lib/api.ts`
   - Update any UI components that need to use the new parameter

### Adding New Integrations

Every new integration in `hindsight-integrations/` must satisfy all of the following before it can be merged:

1. **Tests are required** — tests must simulate or exercise the external system (mock the framework's interfaces and verify the integration actually calls Hindsight correctly). Pure unit tests of helper functions are not sufficient.
2. **CI job** — add a test job in `.github/workflows/test.yml` following the existing pattern (e.g., `test-crewai-integration`). The job must build, install deps, and run `uv run pytest tests -v`. Also add the integration to `detect-changes` outputs so it only runs when its files change.
3. **Release process** — add the integration name to the `VALID_INTEGRATIONS` array in `scripts/release-integration.sh` so it can be released via the standard release workflow.
4. **Follow project code standards** — Python style, type safety, no raw dicts for structured data, no multi-item tuple returns (see `.claude/skills/code-review/SKILL.md`).

If any of these are missing, the integration is incomplete and must not be pushed or merged.

### Changelogs

Never add "Unreleased" entries to changelogs (e.g. `hindsight-docs/src/pages/changelog/**`). Changelog entries are written by the release script (`./scripts/release-integration.sh`) when a version is actually cut. If a bug fix or feature needs documenting before release, describe it in the PR/commit — the release tooling will surface it in the published changelog section.

### Adding New API Configuration Flags

Configuration follows a hierarchical system: **Global (env vars) → Tenant (via extension) → Bank (database)**.

Fields must be categorized as either **hierarchical** (can be overridden per-tenant/bank) or **static** (server-level only).

#### Adding a New Configuration Field

1. **config.py** (`hindsight-api-slim/hindsight_api/config.py`):
   - Add `ENV_*` constant for the environment variable name (e.g., `ENV_MY_SETTING = "HINDSIGHT_API_MY_SETTING"`)
   - Add `DEFAULT_*` constant for the default value
   - Add field to `HindsightConfig` dataclass with type annotation
   - **Mark as configurable** by adding to `_CONFIGURABLE_FIELDS` set if the field should be overridable per-tenant/bank via API
   - Add initialization in `from_env()` method

   ```python
   # Configurable field (can be overridden per-tenant/bank via API)
   _CONFIGURABLE_FIELDS = {
       ...,
       "my_setting",  # Add here for configurable
   }

   # Static field - just don't add to _CONFIGURABLE_FIELDS
   ```

2. **main.py** (`hindsight-api-slim/hindsight_api/main.py`):
   - Add field to the manual `HindsightConfig()` constructor call (search for "CLI override")

3. **Use hierarchical config in MemoryEngine**:
   ```python
   # Config is resolved automatically per bank via ConfigResolver
   config_dict = await self._config_resolver.get_bank_config(bank_id, context)
   value = config_dict["my_setting"]
   ```

4. **Use static config** (non-hierarchical):
   ```python
   from ...config import get_config
   config = get_config()
   value = config.my_static_field
   ```

5. **Documentation** (`hindsight-docs/docs/developer/configuration.md`):
   - Add to appropriate section table with Variable, Description, Default
   - Mark if it's hierarchical (can be overridden per-bank)

#### Hierarchical vs Static Guidelines

**Hierarchical** (per-bank overridable):
- LLM settings (provider, model, API key, base URL)
- Operation-specific settings (retain mode, chunk size, etc.)
- Feature flags that vary by customer/bank

**Static** (server-level only):
- Infrastructure settings (database URL, port, host)
- Global limits (max concurrent operations)
- System-wide feature flags

## Environment Setup

```bash
cp .env.example .env
# Edit .env with LLM API key

# Python deps
uv sync --directory hindsight-api-slim/

# Node deps (uses npm workspaces)
npm install
```

Required env vars:
- `HINDSIGHT_API_LLM_PROVIDER`: openai, anthropic, gemini, groq, minimax, ollama, lmstudio
- `HINDSIGHT_API_LLM_API_KEY`: Your API key
- `HINDSIGHT_API_LLM_MODEL`: Model name (e.g., gpt-4o-mini, claude-sonnet-4-20250514)

Optional (uses local models by default):
- `HINDSIGHT_API_EMBEDDINGS_PROVIDER`: local (default) or tei
- `HINDSIGHT_API_RERANKER_PROVIDER`: local (default) or tei
- `HINDSIGHT_API_DATABASE_URL`: External PostgreSQL (uses embedded pg0 by default)
- `HINDSIGHT_API_ENABLE_BANK_CONFIG_API`: Enable per-bank config API (default: true)


---

<!-- Auto-injected by ~/.claude/hooks/inject-token-economy.sh at 20260419T193842Z -->
<!-- Original backed up to: /Users/igorvaisman/hindsight/.claude/worktrees/condescending-merkle-979601/CLAUDE.md.pre-hygiene.20260419T193842Z.bak -->
<!-- Rationale: project CLAUDE.md overrides global entirely; the mandatory bash-hygiene protocol must be re-included here to keep applying. -->

# Token Economy (Rule 25) — MANDATORY BASH HYGIENE PROTOCOL

**This section is non-negotiable. It applies from the FIRST message of every session.**
**Every token of repeated path, flag, project-id, or CLI warning is context pollution.**
**You MUST follow this protocol before, during, and after every `Bash` tool invocation.**

Every repeated long string and every unnecessary token shortens the useful session window.

## §0 — The rule in one line

**BEFORE running ANY bash command, source the project env script. If it does not exist, CREATE IT. If a command repeats values or prints known noise, EXTEND IT.**

## §1 — Session bootstrap (runs before the FIRST bash command)

The VERY FIRST `Bash` tool call in any session MUST be a discovery + source step. You are ABSOLUTELY FORBIDDEN from running any other bash command before completing this (trivial read-only discovery like `pwd`/`ls`/`echo` is OK — it does not repeat literals or emit noise).

Run this, adapted to the shell:

```bash
# 1) Discover an env script in CWD and up to 4 parents, then source it.
for d in . .. ../.. ../../.. ../../../..; do
  for f in "$d/.envrc" "$d/env.sh" "$d/scripts/env.sh" "$d/.claude/env.sh"; do
    [ -f "$f" ] && { echo "[bash-hygiene] sourcing $f"; . "$f"; break 2; }
  done
done
# 2) Confirm the loaded marker; if missing, env.sh MUST be created (see §3).
echo "ENV_SH_LOADED=${ENV_SH_LOADED:-<MISSING — CREATE env.sh NOW>}"
```

**zsh gotcha:** zsh's `.` / `source` builtin searches `$PATH`, not CWD. Always use an explicit slash: `. ./env.sh` or `. /abs/path/env.sh`. Plain `. env.sh` will fail in zsh.

**Claude Code gotcha:** each Bash tool call spawns a fresh shell, so env vars and wrapper functions do **NOT** persist across calls. Chain every command that needs them as `. ./env.sh && <command>`. The `ENV_SH_LOADED` marker is idempotent — re-sourcing is free.

After bootstrap, `ENV_SH_LOADED=1` MUST be set. If it is not, you MUST create `env.sh` (see §3) before proceeding with the user's task.

## §2 — When to create or extend `env.sh`

You MUST create or extend `env.sh` whenever ANY of the following is true:

1. A command contains a literal value that will appear again in this session: a GCP project ID, a compartment OCID, a region, a cluster/OKE name, a namespace, a bucket, a service account email, a Cloud Run service name, a Filestore instance, a DuckDB database path, a docker image tag prefix, or a long absolute path.
2. A command emits a warning, update notice, survey prompt, deprecation line, pagination pager, or progress bar that has already appeared once in this session.
3. A flag combination (e.g. `--project=... --region=... --quiet`) is about to be typed for the second time.

**"Second occurrence" is the hard threshold.** Never type the same literal twice — extract on the second occurrence, at the latest.

## §3 — env.sh creation rules (ABSOLUTE)

When creating `env.sh`, you MUST:

- Place it at the repo root (or inside `scripts/` if the repo convention demands it). Prefer `.envrc` if `direnv` is installed.
- Make it **idempotent** using the `ENV_SH_LOADED` marker pattern (see template in §6).
- Export **variables** for repeating identifiers (`PROJECT_ID`, `REGION`, `CLUSTER`, `NS`, `COMPARTMENT_OCID`, `DDB`, `IMG`, etc.).
- Export **CLI-quieting env vars** for every tool already seen in the session (§4).
- Define **wrapper functions** (`gcloud`, `kubectl`, `oci`, `docker`, `helm`, `duckdb`) that add `--quiet`-style flags and filter known-noise stderr lines without losing exit codes (§5).
- Append a short `# why` comment next to every suppression, so future sessions know what was hidden and can un-hide it if a genuine error gets masked.
- Source the new file at the end of the creation step and re-verify `ENV_SH_LOADED=1` before any subsequent command.

**NEVER** duplicate `env.sh` logic inline. **NEVER** pass long literals after `env.sh` exists — always reference the variable (`"$PROJECT_ID"`, `"$CLUSTER"`, etc.).

## §4 — Noise-suppression env vars you MUST set on first sight of the tool

Set these the moment you see a tool appear in any bash command. They are safe, widely supported, and documented. Append any that are missing to `env.sh`:

| Tool | Export in `env.sh` |
|---|---|
| Universal | `NO_COLOR=1`, `CLICOLOR=0`, `PAGER=cat`, `CI=${CI:-true}`, `TERM=${TERM:-dumb}` |
| gcloud | `CLOUDSDK_CORE_DISABLE_PROMPTS=1`, `CLOUDSDK_CORE_VERBOSITY=error`, `CLOUDSDK_CORE_DISABLE_USAGE_REPORTING=true`, `CLOUDSDK_SURVEY_DISABLE_PROMPTS=true`, `CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK=true` |
| kubectl | no env var exists; wrap the binary (§5) to strip `^Warning:` lines |
| oci (Oracle) | `OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING=True`, `SUPPRESS_LABEL_WARNING=True`, `OCI_CLI_AUTO_PROMPT=off` |
| docker / buildx | `DOCKER_CLI_HINTS=false`, `BUILDX_NO_DEFAULT_ATTESTATIONS=1`, `BUILDKIT_PROGRESS=plain` |
| npm | `NPM_CONFIG_FUND=false`, `NPM_CONFIG_AUDIT=false`, `NPM_CONFIG_UPDATE_NOTIFIER=false`, `NPM_CONFIG_LOGLEVEL=error`, `NPM_CONFIG_PROGRESS=false`, `NO_UPDATE_NOTIFIER=1` |
| pip | `PIP_DISABLE_PIP_VERSION_CHECK=1`, `PIP_NO_INPUT=1`, `PIP_QUIET=1`, `PIP_PROGRESS_BAR=off`, `PIP_ROOT_USER_ACTION=ignore` |
| python | `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`, `PYTHONWARNINGS="ignore::DeprecationWarning,ignore::PendingDeprecationWarning,ignore::ResourceWarning"` |
| node | `NODE_OPTIONS="--no-deprecation --disable-warning=ExperimentalWarning"`, `NO_UPDATE_NOTIFIER=1` |
| terraform | `TF_IN_AUTOMATION=1`, `TF_INPUT=0`, `CHECKPOINT_DISABLE=1`, `TF_CLI_ARGS="-no-color"` |
| aws | `AWS_PAGER=""`, `AWS_DEFAULT_OUTPUT=json` |
| helm | `HELM_COLOR=never` |
| git | `GIT_ADVICE=0`, `GIT_TERMINAL_PROMPT=0`, `GIT_PAGER=cat` |
| brew | `HOMEBREW_NO_AUTO_UPDATE=1`, `HOMEBREW_NO_ENV_HINTS=1`, `HOMEBREW_NO_ANALYTICS=1`, `HOMEBREW_NO_INSTALL_CLEANUP=1`, `HOMEBREW_NO_EMOJI=1` |
| duckdb | wrap as `duckdb -bail -init /dev/null` (§5) |

**NEVER** use blanket `2>/dev/null` — it hides real errors. ALWAYS filter specific known-noise regexes via process substitution so exit codes survive: `2> >(grep -Ev 'pattern' >&2)`.

## §5 — Wrapper functions: the canonical pattern

Define wrappers in `env.sh` that (a) add quiet flags, (b) filter known stderr noise line-by-line, (c) preserve the real exit code via `command` + process substitution, never a pipe. Template:

```bash
gcloud() {
  command gcloud --quiet --verbosity=error "$@" \
    2> >(grep -Ev '^(Updates are available|To take a quick anonymous survey)' >&2)
}
export -f gcloud   # bash only — zsh silently ignores `export -f`
```

**Function inheritance under Claude Code:** `export -f` works in bash so Make recipes and `bash -c` subshells inherit the wrapper. Under zsh (Claude Code's default shell), `export -f` is a no-op and subshells do NOT inherit functions. The practical consequence: every Bash tool call must re-source `env.sh` to get the wrappers. The `ENV_SH_LOADED` guard makes re-sourcing free.

You MUST add an entry to the filter regex every time a new recurring noise line is observed. Document each filter with a short `# why` comment.

## §6 — Starter `env.sh` template (copy to repo root on first need)

```bash
#!/usr/bin/env bash
# env.sh — project bash hygiene. Source me. Idempotent.
# Purpose: eliminate repeated literals and CLI noise from Claude Code context.

# -------- idempotent guard (do not remove) --------
if [ -n "${ENV_SH_LOADED:-}" ]; then return 0 2>/dev/null || exit 0; fi
export ENV_SH_LOADED=1

# -------- project identifiers (EDIT ME) --------
export PROJECT_ID="REPLACE-ME"                      # gcloud project
export REGION="us-central1"                         # gcloud / run region
export ZONE="${REGION}-a"
export CLUSTER="REPLACE-ME"                         # GKE / OKE cluster
export NS="default"                                 # kubectl namespace
export COMPARTMENT_OCID="ocid1.compartment.oc1..REPLACE-ME"
export OCI_REGION="us-ashburn-1"
export IMG_REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/app"
export DDB="${PWD}/data/warehouse.duckdb"

# -------- universal quieting --------
export NO_COLOR=1 CLICOLOR=0 PAGER=cat
export CI="${CI:-true}"
export DEBIAN_FRONTEND=noninteractive

# -------- gcloud --------
export CLOUDSDK_CORE_DISABLE_PROMPTS=1
export CLOUDSDK_CORE_VERBOSITY=error
export CLOUDSDK_CORE_DISABLE_USAGE_REPORTING=true
export CLOUDSDK_SURVEY_DISABLE_PROMPTS=true
export CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK=true

# -------- oci --------
export OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING=True
export SUPPRESS_LABEL_WARNING=True
export OCI_CLI_AUTO_PROMPT=off

# -------- docker / buildx --------
export DOCKER_CLI_HINTS=false
export BUILDX_NO_DEFAULT_ATTESTATIONS=1
export BUILDKIT_PROGRESS=plain

# -------- npm / node --------
export NPM_CONFIG_FUND=false NPM_CONFIG_AUDIT=false
export NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_LOGLEVEL=error
export NPM_CONFIG_PROGRESS=false NO_UPDATE_NOTIFIER=1
export NODE_OPTIONS="--no-deprecation --disable-warning=ExperimentalWarning"

# -------- pip / python --------
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1 PIP_QUIET=1
export PIP_PROGRESS_BAR=off PIP_ROOT_USER_ACTION=ignore
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
export PYTHONWARNINGS="ignore::DeprecationWarning,ignore::PendingDeprecationWarning,ignore::ResourceWarning"

# -------- terraform / aws / helm / git / brew --------
export TF_IN_AUTOMATION=1 TF_INPUT=0 CHECKPOINT_DISABLE=1 TF_CLI_ARGS="-no-color"
export AWS_PAGER="" AWS_DEFAULT_OUTPUT=json
export HELM_COLOR=never
export GIT_ADVICE=0 GIT_TERMINAL_PROMPT=0 GIT_PAGER=cat
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ENV_HINTS=1 \
       HOMEBREW_NO_ANALYTICS=1 HOMEBREW_NO_INSTALL_CLEANUP=1 HOMEBREW_NO_EMOJI=1

# -------- wrapper functions (preserve exit codes; filter known noise only) --------
# why: each grep pattern suppresses a recurring line observed during sessions.
gcloud() {
  command gcloud --quiet --verbosity=error "$@" \
    2> >(grep -Ev '^(Updates are available|To take a quick anonymous survey|WARNING: You do not appear to have access)' >&2)
}
kubectl() {
  # why: k8s has no env var for client-side warnings; strip only `Warning:` lines.
  command kubectl "$@" 2> >(grep -v '^Warning:' >&2)
}
oci() {
  command oci --no-retry "$@" \
    2> >(grep -Ev '^(WARNING: Permissions|WARNING: This operation supports pagination)' >&2)
}
docker() {
  command docker "$@" \
    2> >(grep -Ev "^(What's Next\?|View .* image vulnerabilities|docker scout)" >&2)
}
helm() {
  command helm "$@" \
    2> >(grep -Ev '^(WARNING: Kubernetes configuration file|coalesce\.go|manifest_sorter\.go)' >&2)
}
duckdb() { command duckdb -bail -init /dev/null "$@"; }

# -------- convenience functions for repeating command shapes --------
gk()  { command kubectl --context="$CLUSTER" -n "$NS" "$@" 2> >(grep -v '^Warning:' >&2); }
gcr() { gcloud run services "$@" --project="$PROJECT_ID" --region="$REGION"; }
gbq() { command bq --project_id="$PROJECT_ID" --quiet --headless "$@"; }
```

## §7 — Polluted vs clean execution (concrete before/after)

**Before — polluted (8 lines of warnings, 3 repeated `--project` flags, ~900 tokens):**
```
gcloud run services describe my-svc --project=my-org-prod-482910 --region=us-central1 --format=json
# → "Updates are available for some Google Cloud CLI components..."
# → "To take a quick anonymous survey..."
kubectl --context=prod-gke -n payments get pods
# → "Warning: autoscaling/v2beta2 HorizontalPodAutoscaler is deprecated..."
kubectl --context=prod-gke -n payments logs deploy/api | tail -50
oci compute instance list --compartment-id ocid1.compartment.oc1..aaaaaaaabigthing
# → "WARNING: Permissions on /Users/me/.oci/config are too open..."
```

**After — clean (sourced once per shell, reused forever, ~180 tokens):**
```
. ./env.sh
gcr describe my-svc --format=json
gk get pods
gk logs deploy/api | tail -50
oci compute instance list --compartment-id "$COMPARTMENT_OCID"
```

## §8 — Customizing for your stack

**GCP (gcloud, BigQuery, Cloud Run, Filestore):** export `PROJECT_ID`, `REGION`, `ZONE`, `BQ_DATASET`, `RUN_SERVICE`, `FILESTORE_INSTANCE`; wrap `gcloud` with `--quiet --verbosity=error`; wrap `bq` with `--project_id=$PROJECT_ID --quiet --headless`; set `gcloud config set survey/disable_prompts true` once so the state persists.

**Oracle Cloud / OKE:** export `COMPARTMENT_OCID`, `OCI_REGION`, `OKE_CLUSTER_OCID`; always set `OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING=True`; acquire kubeconfig once with `oci ce cluster create-kubeconfig --cluster-id "$OKE_CLUSTER_OCID" --region "$OCI_REGION" --file "$HOME/.kube/oke-config"` and export `KUBECONFIG`; then use the `gk` wrapper.

**kubectl across contexts:** define a per-context wrapper like `kprod() { command kubectl --context=prod -n "$NS" "$@"; }`. Never type `--context=` by hand twice.

**DuckDB:** always invoke as `duckdb -bail -init /dev/null "$DDB"` or via the wrapper; put `SET enable_progress_bar=false;` in a project `.duckdbrc` if you need long interactive sessions.

**Docker:** set `DOCKER_CLI_HINTS=false` once; pass `--progress=plain` in CI-like invocations; export an `IMG` prefix so `docker build -t "$IMG:$(git rev-parse --short HEAD)" .` is the only pattern.

**Parallel Claude Code sessions:** keep `env.sh` committed to the repo — every session bootstraps identically. Put session-specific secrets in a gitignored `.envrc.local` sourced at the end of `env.sh` (`[ -f .envrc.local ] && . .envrc.local`).

## §9 — Hard failure modes you must AVOID

- **NEVER** use `2>/dev/null` on a whole command. Filter specific lines.
- **NEVER** pipe through `grep` on stdout without `set -o pipefail` or process substitution — it destroys exit codes.
- **NEVER** repeat a literal project ID, region, OCID, cluster name, or path a second time in the same session without putting it in `env.sh` first.
- **NEVER** skip the §1 bootstrap, even on a "quick check" session. The first `ls` counts.
- **NEVER** define a wrapper without accepting the shell-inheritance rules in §5 — under zsh, subshells and `bash -c` invocations must re-source `env.sh` explicitly. Don't assume the wrapper is active in a child process.
- **NEVER** suppress `WARNING:` lines from `gcloud auth`, `oci config validate`, or `kubectl` admission controllers without leaving a `# why` comment — these sometimes surface real policy violations.

## §10 — Self-check before every `Bash` invocation

Before you emit a `Bash` tool call, run this mental checklist. If any answer is wrong, fix it FIRST:

1. Has `env.sh` been sourced this session? (`ENV_SH_LOADED=1`?)
2. Does the command contain any literal that already appears in `env.sh` as a variable? If yes, use the variable.
3. Does the command contain a repeating flag combo not yet wrapped? If yes, extend `env.sh`.
4. Will the command emit a warning/hint already seen? If yes, add its regex to the wrapper's grep filter.

**If you cannot answer "yes, clean" to all four, you MUST amend `env.sh` before sending the command.**

## Red flags — markers that this rule must be applied

| Marker | Fix |
| --- | --- |
| Same path/URL/parameter appears 2+ times in bash commands | Assign to a shell variable on first use |
| `kubectl` spelled out | Use `k` alias |
| Full cluster name typed out (e.g. `gc-pop3-prod-gke-cluster-191`) | Use `$C191`, `$C192`, `$CLOGS`, or build from `$CP` |
| Full GCP project ID typed out | Use `$GKE_PROJECT` |
| VictoriaMetrics URL in curl/exec | Use `vm 'query'` helper |
| Elasticsearch URL in curl/exec | Use `es 'body' 'path'` helper |
| Manual gcloud get-credentials + kubectl | Use `fleet $CLUSTER short_id` |
| Materializer scripts path spelled out | Use `$S` |
| GKE skill scripts path spelled out | Use `$SKILL_SCRIPTS` |
| `cat`, `head`, `tail`, `grep`, `rg`, `find`, `ls` in Bash tool | Use Read, Grep, Glob dedicated tools |
| Command output fills 50+ lines of context | Redirect to `/tmp/`, then Read only what matters |
| Same command pattern repeated with different params | Write a loop or wrap in a helper function |
| CLI tool dumping all fields when only 1–2 are needed | Use `--format`, `-o`, `--output` flags to select fields |
| Multiple independent Bash calls in sequence | Combine into parallel tool calls |

## Shell helpers reference (user-specific; loaded via `~/.zshrc` + project `env.sh` files)

| Helper | Expands to | Char savings |
| --- | --- | --- |
| `k` | `kubectl` | 6 |
| `$CP` | `gc-pop3-prod-gke-cluster-` | 27 |
| `$C191` / `$C192` / `$CLOGS` | full cluster names via `$CP` | ~35 each |
| `$GKE_PROJECT` | `gc-first-pop3-prod` | 19 |
| `$VM_URL` | `http://metrics.srv.tools/select/2/prometheus` | 50 |
| `$SKILL_SCRIPTS` | GKE troubleshoot scripts dir | ~45 |
| `$S` | materializer scripts dir | ~50 |
| `fleet $C191 c191` | gcloud connect + isolated `KUBECONFIG` | ~80 (replaces multi-line gcloud) |
| `vm 'query_url'` | exec into vmagent + curl against `$VM_URL` | ~80 |
| `es 'body' 'path'` | exec into ES pod + curl | ~80 |

## Hard requirements (quick-reference summary)

1. **Dedicated tools first** — Read over `cat`/`head`/`tail`, Grep over `grep`/`rg`, Glob over `find`/`ls`. No exceptions unless the dedicated tool literally cannot do it.
2. **Variables for anything repeated** — If a path, URL, cluster name, or parameter appears ≥2 times, store it in a variable on first use.
3. **Shell helpers are mandatory** — When in GKE/prod context, always use `$CP`, `$C191`, `k`, `vm`, `es`, `fleet` etc. Never spell out what a helper already provides.
4. **Cap output** — Use `head_limit` on Grep/Glob. Redirect verbose bash output to `/tmp/` and Read the relevant slice. Never dump 200 lines when 10 suffice.
5. **Combine commands** — Independent commands → parallel tool calls in one message. Sequential dependent commands → single `&&` chain, not separate tool calls.
6. **Format flags** — Use `--format='value(field)'` (gcloud), `-o jsonpath=` (kubectl), `--json` + `jq` to extract only needed fields. Never dump full JSON/YAML to grep for one value.

## Enforcement (Rule 13 — where hooks are available)

This protocol should be backed by deterministic enforcement wherever possible. Reference implementation lives in `~/prod_control/`:

- **`.claude/hooks/bash-hygiene.sh`** — PreToolUse Bash hook that exits 2 (blocks with feedback) when: env.sh not sourced, a literal value declared in env.sh appears in the command, a long flag token is repeated 3+ times, or a destructive pattern (`rm -rf /`, `mkfs`, `dd of=/dev/`, fork bomb) is detected.
- **`.claude/settings.local.json`** — wires the hook to `PreToolUse[Bash]` with a 10-second timeout, plus a second hook that appends every bash call to `.claude/bash-audit.log` for later refactoring review.
- **`.claude/templates/env.sh`** — starter template for cloning into sibling projects.
- **`env.sh`** — the project-root env file (idempotent, ENV_SH_LOADED-guarded).

To install in another project: copy the four artifacts, update the project-identifier section of `env.sh`, and verify with the smoke-test payload pattern `{"tool_name":"Bash","tool_input":{"command":"..."},"session_id":"smoke","cwd":"..."}` piped to the hook.

---
