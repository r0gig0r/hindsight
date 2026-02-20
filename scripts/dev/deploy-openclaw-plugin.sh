#!/usr/bin/env bash
# deploy-openclaw-plugin.sh — Test, build, deploy, and restart the OpenClaw plugin.
#
# Typical usage after editing plugin source:
#   ./scripts/dev/deploy-openclaw-plugin.sh
#   ./scripts/dev/deploy-openclaw-plugin.sh --skip-test    # trust yourself
#   ./scripts/dev/deploy-openclaw-plugin.sh --skip-venv    # venv already correct
#   ./scripts/dev/deploy-openclaw-plugin.sh --no-restart   # deploy only, restart later
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLUGIN_DIR="$REPO_DIR/hindsight-integrations/openclaw"
INSTALL_DIR="$HOME/.openclaw/extensions/hindsight-openclaw"
RESTART_SCRIPT="$REPO_DIR/scripts/dev/restart-openclaw.sh"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Parse flags
SKIP_TEST=false
SKIP_VENV=false
NO_RESTART=false
FORCE=false
for arg in "$@"; do
    case "$arg" in
        --skip-test)  SKIP_TEST=true ;;
        --skip-venv)  SKIP_VENV=true ;;
        --no-restart) NO_RESTART=true ;;
        --force)      FORCE=true ;;
        -h|--help)
            echo "Usage: $0 [--skip-test] [--skip-venv] [--no-restart] [--force]"
            echo ""
            echo "  --skip-test   Skip vitest (faster, use when you're confident)"
            echo "  --skip-venv   Pass --skip-venv to restart script"
            echo "  --no-restart  Deploy files only, don't restart the gateway"
            echo "  --force       Pass --force to restart script (kill -9)"
            exit 0
            ;;
        *)
            echo "Unknown flag: $arg (use --help)" >&2
            exit 1
            ;;
    esac
done

step() { echo -e "\n${GREEN}▸${NC} $1"; }
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1" >&2; }

echo -e "${CYAN}OpenClaw plugin deploy${NC} ($(date '+%H:%M:%S'))"

# --- Step 1: Test ---
if $SKIP_TEST; then
    step "Skipping tests (--skip-test)"
else
    step "Running tests..."
    cd "$PLUGIN_DIR"
    if npx vitest run src 2>&1 | tail -5; then
        ok "Tests passed"
    else
        fail "Tests failed — aborting deploy"
        exit 1
    fi
fi

# --- Step 2: Build ---
step "Building TypeScript..."
cd "$PLUGIN_DIR"
npm run build 2>&1
ok "Build complete"

# --- Step 3: Deploy to extensions dir ---
step "Deploying to $INSTALL_DIR..."
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

cp -r "$PLUGIN_DIR/dist" "$INSTALL_DIR/"
cp "$PLUGIN_DIR/package.json" "$INSTALL_DIR/"
cp "$PLUGIN_DIR/openclaw.plugin.json" "$INSTALL_DIR/"
[ -f "$PLUGIN_DIR/README.md" ] && cp "$PLUGIN_DIR/README.md" "$INSTALL_DIR/"

# Install production deps only
cd "$INSTALL_DIR"
npm install --omit=dev --ignore-scripts 2>&1 | tail -3
ok "Plugin deployed"

# --- Step 4: Restart ---
if $NO_RESTART; then
    step "Skipping restart (--no-restart)"
    echo ""
    echo -e "  Run ${CYAN}$RESTART_SCRIPT --skip-venv${NC} when ready."
else
    step "Restarting OpenClaw..."
    RESTART_ARGS=()
    $SKIP_VENV && RESTART_ARGS+=(--skip-venv)
    $FORCE && RESTART_ARGS+=(--force)
    "$RESTART_SCRIPT" "${RESTART_ARGS[@]}"
fi

echo ""
echo -e "${GREEN}Deploy complete.${NC}"
