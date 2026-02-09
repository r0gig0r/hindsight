#!/bin/bash
set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${BLUE}Starting SigNoz...${NC}"

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo -e "${YELLOW}Error: Docker is not running. Please start Docker and try again.${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}SigNoz services starting...${NC}"
echo ""
echo -e "  ${GREEN}Web UI:${NC}        http://localhost:3301"
echo -e "  ${GREEN}OTLP Endpoint:${NC} http://localhost:4318 (HTTP) or http://localhost:4317 (gRPC)"
echo ""
echo -e "${YELLOW}Configure Hindsight:${NC}"
echo "  HINDSIGHT_API_OTEL_TRACES_ENABLED=true"
echo "  HINDSIGHT_API_OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318"
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Start SigNoz with Docker Compose (foreground)
cd "$SCRIPT_DIR"
docker compose up
