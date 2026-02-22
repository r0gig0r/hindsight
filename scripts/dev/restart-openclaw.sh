#!/usr/bin/env bash
# restart-openclaw.sh — Clean restart of the OpenClaw gateway + Hindsight daemon.
#
# Use after:
#   - Deploying new engine code (hindsight-api changes)
#   - Fixing the plist / environment variables
#   - Recovering from a crashed or stuck daemon
#
# What it does:
#   1. Bootout the gateway LaunchAgent (stops the Node process)
#   2. Kill any stale hindsight-api daemon still holding port 9077
#   3. Rebuild the Python venv with the correct interpreter (python3.13)
#   4. Bootstrap the gateway LaunchAgent (starts Node → starts daemon)
#   5. Wait for the daemon health check to pass
#   6. Print a summary
#
# Flags:
#   --skip-venv   Skip the uv sync step (faster if venv is already correct)
#   --force       Use kill -9 instead of graceful kill
#   --dry-run     Print what would happen without doing it
#
set -euo pipefail

PLIST="$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist"
LABEL="ai.openclaw.gateway"
DAEMON_PORT=9077
GATEWAY_PORT=18789
REPO_DIR="$HOME/hindsight"
VENV_PYTHON="python3.13"
HEALTH_URL="http://127.0.0.1:${DAEMON_PORT}/health"
MAX_WAIT=90  # seconds to wait for daemon health

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Parse flags
SKIP_VENV=false
FORCE_KILL=false
DRY_RUN=false
for arg in "$@"; do
    case "$arg" in
        --skip-venv) SKIP_VENV=true ;;
        --force)     FORCE_KILL=true ;;
        --dry-run)   DRY_RUN=true ;;
        -h|--help)
            echo "Usage: $0 [--skip-venv] [--force] [--dry-run]"
            echo ""
            echo "  --skip-venv  Skip 'uv sync' (faster if venv is correct)"
            echo "  --force      Kill -9 instead of graceful kill"
            echo "  --dry-run    Print steps without executing"
            exit 0
            ;;
        *)
            echo "Unknown flag: $arg (use --help)" >&2
            exit 1
            ;;
    esac
done

run() {
    if $DRY_RUN; then
        echo -e "  ${CYAN}[dry-run]${NC} $*"
    else
        "$@"
    fi
}

step() {
    echo -e "\n${GREEN}▸${NC} $1"
}

warn() {
    echo -e "  ${YELLOW}⚠${NC} $1"
}

ok() {
    echo -e "  ${GREEN}✓${NC} $1"
}

fail() {
    echo -e "  ${RED}✗${NC} $1" >&2
}

# --- Preflight checks ---
if [[ ! -f "$PLIST" ]]; then
    fail "LaunchAgent plist not found: $PLIST"
    exit 1
fi

if ! command -v uv &>/dev/null; then
    fail "'uv' not found in PATH"
    exit 1
fi

echo -e "${CYAN}OpenClaw restart${NC} ($(date '+%H:%M:%S'))"

# --- Step 1: Stop the gateway ---
step "Stopping gateway LaunchAgent..."
if launchctl list "$LABEL" &>/dev/null; then
    run launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    sleep 1
    ok "Gateway service stopped"
else
    warn "Gateway was not loaded"
fi

# --- Step 2: Kill stale gateway processes ---
GATEWAY_PIDS=$(lsof -ti ":$GATEWAY_PORT" 2>/dev/null || true)
if [[ -n "$GATEWAY_PIDS" ]]; then
    step "Killing stale gateway processes on :${GATEWAY_PORT}..."
    for pid in $GATEWAY_PIDS; do
        if $FORCE_KILL; then
            run kill -9 "$pid" 2>/dev/null || true
        else
            run kill "$pid" 2>/dev/null || true
        fi
    done
    sleep 2
    # Verify port is free
    if lsof -ti ":$GATEWAY_PORT" &>/dev/null; then
        warn "Port $GATEWAY_PORT still occupied, force-killing..."
        run kill -9 $(lsof -ti ":$GATEWAY_PORT") 2>/dev/null || true
        sleep 1
    fi
    ok "Gateway port $GATEWAY_PORT is free"
fi

# --- Step 3: Kill the hindsight daemon ---
step "Stopping hindsight daemon..."
DAEMON_PIDS=$(pgrep -f "hindsight-api.*--daemon" 2>/dev/null || true)
if [[ -n "$DAEMON_PIDS" ]]; then
    for pid in $DAEMON_PIDS; do
        if $FORCE_KILL; then
            run kill -9 "$pid" 2>/dev/null || true
        else
            run kill "$pid" 2>/dev/null || true
        fi
    done
    # Wait up to 5s for graceful shutdown
    for i in {1..5}; do
        if ! pgrep -f "hindsight-api.*--daemon" &>/dev/null; then
            break
        fi
        sleep 1
    done
    # Force kill if still alive
    if pgrep -f "hindsight-api.*--daemon" &>/dev/null; then
        warn "Daemon didn't stop gracefully, force-killing..."
        run pkill -9 -f "hindsight-api.*--daemon" 2>/dev/null || true
        sleep 1
    fi
    ok "Daemon stopped"
else
    ok "No daemon was running"
fi

# --- Step 3.5: Restart Infinity reranker sidecar ---
INFINITY_PLIST="$HOME/Library/LaunchAgents/ai.openclaw.infinity-reranker.plist"
INFINITY_LABEL="ai.openclaw.infinity-reranker"
INFINITY_PORT=7997
if [[ -f "$INFINITY_PLIST" ]]; then
    step "Restarting Infinity reranker sidecar..."
    if launchctl list "$INFINITY_LABEL" &>/dev/null; then
        run launchctl bootout "gui/$(id -u)/$INFINITY_LABEL" 2>/dev/null || true
        sleep 2
    fi
    run launchctl bootstrap "gui/$(id -u)" "$INFINITY_PLIST"
    # Wait for Infinity health
    if ! $DRY_RUN; then
        INF_ELAPSED=0
        while [[ $INF_ELAPSED -lt 30 ]]; do
            if curl -s --max-time 2 "http://127.0.0.1:${INFINITY_PORT}/models" &>/dev/null; then
                ok "Infinity sidecar healthy on :${INFINITY_PORT}"
                break
            fi
            sleep 2
            INF_ELAPSED=$((INF_ELAPSED + 2))
        done
        if [[ $INF_ELAPSED -ge 30 ]]; then
            warn "Infinity sidecar did not become healthy in 30s (recall will use lazy init)"
        fi
    fi
else
    warn "Infinity LaunchAgent plist not found, skipping"
fi

# --- Step 4: Rebuild venv ---
if $SKIP_VENV; then
    step "Skipping venv rebuild (--skip-venv)"
else
    step "Rebuilding venv with ${VENV_PYTHON}..."
    run env UV_PYTHON="$VENV_PYTHON" uv sync --directory "$REPO_DIR/hindsight-api/"
    if ! $DRY_RUN; then
        ACTUAL_PY=$("$REPO_DIR/.venv/bin/python" --version 2>&1)
        ok "Venv ready: $ACTUAL_PY"
        # Quick sanity: onnxruntime must import
        if "$REPO_DIR/.venv/bin/python" -c "import onnxruntime" 2>/dev/null; then
            ok "onnxruntime imports OK"
        else
            fail "onnxruntime import failed — check Python version compatibility"
            exit 1
        fi
    fi
fi

# --- Step 5: Bootstrap gateway ---
step "Starting gateway LaunchAgent..."
run launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 2
if ! $DRY_RUN; then
    if launchctl list "$LABEL" &>/dev/null; then
        ok "Gateway service loaded"
    else
        fail "Gateway failed to load"
        exit 1
    fi
fi

# --- Step 6: Wait for daemon health ---
step "Waiting for daemon health check (up to ${MAX_WAIT}s)..."
if ! $DRY_RUN; then
    ELAPSED=0
    while [[ $ELAPSED -lt $MAX_WAIT ]]; do
        HEALTH=$(curl -s --max-time 3 "$HEALTH_URL" 2>/dev/null || true)
        if echo "$HEALTH" | grep -q '"healthy"' 2>/dev/null; then
            ok "Daemon healthy after ${ELAPSED}s"
            break
        fi
        sleep 5
        ELAPSED=$((ELAPSED + 5))
        echo -e "  … waiting (${ELAPSED}s)"
    done

    if [[ $ELAPSED -ge $MAX_WAIT ]]; then
        fail "Daemon did not become healthy within ${MAX_WAIT}s"
        echo ""
        echo "Check logs:"
        echo "  tail -30 ~/.openclaw/logs/gateway.err.log | grep -v ciao"
        echo "  tail -30 ~/.openclaw/logs/gateway.log | grep Hindsight"
        exit 1
    fi
fi

# --- Step 7: Summary ---
echo ""
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo -e "${GREEN}  OpenClaw restart complete${NC}"
echo -e "${GREEN}════════════════════════════════════════${NC}"

if ! $DRY_RUN; then
    # Gateway status
    GW_INFO=$(launchctl list "$LABEL" 2>/dev/null | head -1 || echo "unknown")
    GW_PID=$(echo "$GW_INFO" | awk '{print $1}')
    echo -e "  Gateway:  pid ${CYAN}${GW_PID}${NC} on :${GATEWAY_PORT}"

    # Daemon status
    DAEMON_PID=$(pgrep -f "hindsight-api.*--daemon" 2>/dev/null | head -1 || echo "none")
    DAEMON_PY=$("$REPO_DIR/.venv/bin/python" --version 2>&1 || echo "unknown")
    echo -e "  Daemon:   pid ${CYAN}${DAEMON_PID}${NC} on :${DAEMON_PORT} (${DAEMON_PY})"

    # Infinity sidecar (check via health endpoint)
    if curl -s --max-time 2 "http://127.0.0.1:${INFINITY_PORT}/models" &>/dev/null; then
        INFINITY_PID=$(lsof -iTCP:"${INFINITY_PORT}" -sTCP:LISTEN -t 2>/dev/null | head -1 || echo "?")
        echo -e "  Infinity: pid ${CYAN}${INFINITY_PID}${NC} on :${INFINITY_PORT}"
    else
        echo -e "  Infinity: ${YELLOW}not running${NC}"
    fi

    # Health
    echo -e "  Health:   ${GREEN}$(curl -s "$HEALTH_URL" 2>/dev/null)${NC}"

    # Version
    VERSION=$(curl -s "http://127.0.0.1:${DAEMON_PORT}/version" 2>/dev/null || echo "{}")
    echo -e "  Version:  ${CYAN}${VERSION}${NC}"
fi

echo ""
