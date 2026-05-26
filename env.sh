#!/usr/bin/env bash
# env.sh — project bash hygiene. Source me. Idempotent.
# Purpose: eliminate repeated literals and CLI noise from Claude Code context.

if [ -n "${ENV_SH_LOADED:-}" ]; then return 0 2>/dev/null || exit 0; fi
export ENV_SH_LOADED=1

# -------- local LLM under test (Nemotron nano, OpenAI-compatible) --------
export LLM_BASE="http://100.66.118.101:1234"
export LLM_API="${LLM_BASE}/v1"
export LLM_MODEL="gemma-4-26b-a4b-it-ara-abliterated"
export LLM_KEY="dummy"   # local server usually accepts anything; OpenAI SDK requires non-empty

# -------- test artifact location --------
export LLM_OUT="/tmp/nemotron-test"
mkdir -p "$LLM_OUT"

# -------- Claude Code session metadata paths (avoid repeating in commands) --------
# why: the harness's per-session cwd shadow lives under /private/tmp/claude-501/...
# Long, repeated, useless to re-type. Bind once if you ever need to reference it.
# (Lesson learned 2026-04-24: a deleted worktree under this root wedged the Bash
# tool for the rest of the session — use main checkout going forward.)
export CCD_SESSION_ROOT="/private/tmp/claude-501"

# -------- universal quieting --------
export NO_COLOR=1 CLICOLOR=0 PAGER=cat
export CI="${CI:-true}"
export TERM="${TERM:-dumb}"

# -------- python / pip noise --------
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1 PIP_QUIET=1
export PIP_PROGRESS_BAR=off PIP_ROOT_USER_ACTION=ignore
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
export PYTHONWARNINGS="ignore::DeprecationWarning,ignore::PendingDeprecationWarning,ignore::ResourceWarning"

# -------- curl helper: JSON POST to the LLM (auth + content-type baked in) --------
# usage: llm_post <path> <json-body>   e.g. llm_post chat/completions "$body"
llm_post() {
  local path="$1"; shift
  command curl -sS -X POST "${LLM_API}/${path}" \
    -H "Authorization: Bearer ${LLM_KEY}" \
    -H "Content-Type: application/json" \
    --data-binary "$1"
}
llm_get() {
  command curl -sS "${LLM_API}/$1" -H "Authorization: Bearer ${LLM_KEY}"
}
