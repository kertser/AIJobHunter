#!/usr/bin/env bash
# ─────────────────────────────────────────────────────
# AI Job Hunter — deployment script (docker compose)
#
# Usage:
#   ./deploy.sh              # app + local LLM sidecar (default)
#   ./deploy.sh --with-llm   # (deprecated, LLM is always included now)
#
# Requires: docker compose v2  (docker compose version)
# ─────────────────────────────────────────────────────
set -euo pipefail

for arg in "$@"; do
    case "$arg" in
        --with-llm) echo "Note: --with-llm is no longer needed — LLM sidecar is always included." ;;
        *) echo "Unknown flag: $arg"; exit 1 ;;
    esac
done

COMPOSE="docker compose"

echo "═══ AI Job Hunter Deploy ═══"
echo "  Mode: app + local LLM sidecar"

# ── Pull latest code ──
echo "→ Pulling latest code…"
git pull

# ── Stop running containers ──
echo "→ Stopping containers…"
# Clean up legacy containers started via raw 'docker run' (before compose migration)
docker stop ai-job-hunter 2>/dev/null && docker rm ai-job-hunter 2>/dev/null || true
docker stop ai-job-hunter-llm 2>/dev/null && docker rm ai-job-hunter-llm 2>/dev/null || true
$COMPOSE down --remove-orphans 2>/dev/null || true

# ── Build images (layer cache preserved for fast rebuilds) ──
echo "→ Building images…"
$COMPOSE build

# ── Clean up dangling images from previous build ──
docker image prune -f 2>/dev/null || true

# ── Download model if missing ──
if [ ! -f "./models/model.gguf" ]; then
    echo "→ Downloading LLM model (Llama-3.2-3B-Instruct, ~1.8 GB)…"
    bash ./scripts/download_model.sh
fi

# ── Start containers ──
echo "→ Starting containers…"
$COMPOSE up -d

echo ""
echo "✓ AI Job Hunter is running"
echo "  App       : http://localhost:80"
echo "  Data      : ./data → /app/data"
echo "  LLM API   : http://localhost:8080/v1  (internal: http://llm:8080/v1)"
echo "  Logs      : $COMPOSE logs -f"
echo "  Stop      : $COMPOSE down"
