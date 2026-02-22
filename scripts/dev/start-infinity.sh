#!/usr/bin/env bash
# start-infinity.sh — Manage the Infinity reranker sidecar.
#
# Infinity (michaelfeil/infinity) runs as a separate process with MPS/GPU
# acceleration, providing sub-second reranking for hindsight recall.
# The hindsight daemon connects to it via the TEI reranker provider.
#
# Usage: start-infinity.sh [setup|start|stop|status]
#
# The sidecar uses its own Python 3.12 venv at ~/.infinity-reranker/
# (separate from hindsight-api's Python 3.13 venv because torch 2.4
# required by infinity's optimum dependency only supports Python <=3.12).
#
set -euo pipefail

INFINITY_DIR="$HOME/.infinity-reranker"
VENV_DIR="$INFINITY_DIR/venv"
PID_FILE="$INFINITY_DIR/infinity.pid"
LOG_FILE="$INFINITY_DIR/infinity.log"
PORT=7997
MODEL="cross-encoder/ms-marco-MiniLM-L-6-v2"
HEALTH_URL="http://127.0.0.1:${PORT}/models"
PYTHON="python3.12"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/ai.openclaw.infinity-reranker.plist"
LAUNCHD_LABEL="ai.openclaw.infinity-reranker"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1" >&2; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
info() { echo -e "  ${CYAN}▸${NC} $1"; }

_get_pid() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        fi
    fi
    return 1
}

cmd_setup() {
    echo -e "${CYAN}Infinity reranker setup${NC}"

    if ! command -v "$PYTHON" &>/dev/null; then
        fail "Python 3.12 not found. Install it with: brew install python@3.12"
        exit 1
    fi

    mkdir -p "$INFINITY_DIR"

    if [[ -d "$VENV_DIR" ]]; then
        warn "Venv already exists at $VENV_DIR"
        info "Remove it first to reinstall: rm -rf $VENV_DIR"
        return 0
    fi

    info "Creating Python 3.12 venv at $VENV_DIR..."
    "$PYTHON" -m venv "$VENV_DIR"

    info "Installing infinity-emb and dependencies..."
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    "$VENV_DIR/bin/pip" install \
        "infinity-emb[all]" \
        "transformers<4.49" \
        "optimum>=1.21,<2.0" \
        "torch>=2.4,<2.5" \
        -q

    ok "Setup complete"
    info "Venv: $VENV_DIR"
    info "Python: $("$VENV_DIR/bin/python" --version)"
    info "Next: $0 start"
}

cmd_start() {
    echo -e "${CYAN}Starting Infinity reranker sidecar${NC}"

    # Check if already healthy
    if curl -s --max-time 2 "$HEALTH_URL" &>/dev/null; then
        local running_pid
        running_pid=$(lsof -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1 || echo "unknown")
        warn "Already running (pid $running_pid)"
        return 0
    fi

    if [[ ! -d "$VENV_DIR" ]]; then
        fail "Venv not found. Run: $0 setup"
        exit 1
    fi

    mkdir -p "$INFINITY_DIR"

    info "Model: $MODEL"
    info "Port: $PORT"
    info "Log: $LOG_FILE"

    # Write launcher script (the CLI has a typer bug with click 8.x)
    LAUNCHER="$INFINITY_DIR/_launcher.py"
    cat > "$LAUNCHER" <<PYEOF
import uvicorn
from infinity_emb import EngineArgs, create_server

engine_args = EngineArgs(
    model_name_or_path="$MODEL",
    engine="torch",
    device="auto",
)
app = create_server(engine_args_list=[engine_args])
uvicorn.run(app, host="0.0.0.0", port=$PORT, log_level="info")
PYEOF

    # Use LaunchAgent if plist exists, otherwise direct launch
    if [[ -f "$LAUNCHD_PLIST" ]]; then
        info "Starting via LaunchAgent..."
        if launchctl list "$LAUNCHD_LABEL" &>/dev/null; then
            launchctl bootout "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || true
            sleep 1
        fi
        launchctl bootstrap "gui/$(id -u)" "$LAUNCHD_PLIST"
    else
        info "Starting directly (no LaunchAgent plist)..."
        # Check if port is already in use (LISTEN only)
        if lsof -iTCP:"$PORT" -sTCP:LISTEN -t &>/dev/null; then
            fail "Port $PORT is already in use (LISTEN)"
            exit 1
        fi
        nohup "$VENV_DIR/bin/python" "$LAUNCHER" >> "$LOG_FILE" 2>&1 &
        echo "$!" > "$PID_FILE"
    fi

    info "Waiting for health..."

    # Wait for server to be ready (up to 60s)
    local elapsed=0
    local max_wait=60
    while [[ $elapsed -lt $max_wait ]]; do
        if curl -s --max-time 2 "$HEALTH_URL" &>/dev/null; then
            local new_pid
            new_pid=$(lsof -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1 || echo "unknown")
            ok "Healthy after ${elapsed}s (pid $new_pid, port $PORT)"
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done

    fail "Did not become healthy within ${max_wait}s"
    info "Check logs: tail -30 $LOG_FILE"
    exit 1
}

cmd_stop() {
    echo -e "${CYAN}Stopping Infinity reranker sidecar${NC}"

    # Use LaunchAgent if loaded
    if [[ -f "$LAUNCHD_PLIST" ]] && launchctl list "$LAUNCHD_LABEL" &>/dev/null; then
        launchctl bootout "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || true
        sleep 2
        rm -f "$PID_FILE"
        ok "Stopped (via LaunchAgent)"
        return 0
    fi

    # Fallback: PID-based stop
    if pid=$(_get_pid); then
        kill "$pid" 2>/dev/null || true
        for _ in {1..5}; do
            if ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
            sleep 1
        done
        if kill -0 "$pid" 2>/dev/null; then
            warn "Force-killing pid $pid..."
            kill -9 "$pid" 2>/dev/null || true
            sleep 1
        fi
        rm -f "$PID_FILE"
        ok "Stopped (was pid $pid)"
    else
        ok "Not running"
    fi
}

cmd_status() {
    local pid
    pid=$(lsof -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)
    if [[ -n "$pid" ]]; then
        ok "Running (pid $pid)"
        if curl -s --max-time 2 "$HEALTH_URL" &>/dev/null; then
            ok "Health: OK"
            local models
            models=$(curl -s --max-time 2 "$HEALTH_URL" 2>/dev/null || echo "{}")
            info "Models: $models"
        else
            warn "Health: not responding"
        fi
        # Show if managed by LaunchAgent
        if launchctl list "$LAUNCHD_LABEL" &>/dev/null 2>&1; then
            info "Managed by: LaunchAgent ($LAUNCHD_LABEL)"
        else
            info "Managed by: PID file"
        fi
    else
        info "Not running"
    fi
}

# --- Main ---
case "${1:-status}" in
    setup)  cmd_setup ;;
    start)  cmd_start ;;
    stop)   cmd_stop ;;
    status) cmd_status ;;
    -h|--help)
        echo "Usage: $0 [setup|start|stop|status]"
        echo ""
        echo "  setup   Create Python 3.12 venv and install infinity-emb"
        echo "  start   Launch the reranker sidecar (port $PORT)"
        echo "  stop    Stop the sidecar"
        echo "  status  Check if running and healthy (default)"
        ;;
    *)
        echo "Unknown command: $1 (use --help)" >&2
        exit 1
        ;;
esac
