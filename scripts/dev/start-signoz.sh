#!/bin/bash
# Start SigNoz for local LLM observability
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/signoz/start.sh"
