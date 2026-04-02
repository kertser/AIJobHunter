#!/usr/bin/env bash
# ─────────────────────────────────────────────────────
# AI Job Hunter — deployment script (docker compose)
#
# Usage:
#   ./deploy.sh              # app only
#   ./deploy.sh --with-llm   # app + local LLM sidecar
#
# Requires: docker compose v2  (docker compose version)
# ─────────────────────────────────────────────────────
set -euo pipefail

WITH_LLM=false
for arg in "$@"; do
    case "$arg" in
        --with-llm) WITH_LLM=true ;;
        *) echo "Unknown flag: $arg"; exit 1 ;;
    esac
done

COMPOSE="docker compose"
PROFILE_FLAGS=""
if $WITH_LLM; then
    PROFILE_FLAGS="--profile local-llm"
fi

echo "═══ AI Job Hunter Deploy ═══"
if $WITH_LLM; then
    echo "  Mode: app + local LLM sidecar"
else
    echo "  Mode: app only (use --with-llm to include LLM sidecar)"
fi

# ── Pull latest code ──
echo "→ Pulling latest code…"
git pull

# ── Stop running containers ──
echo "→ Stopping containers…"
# Clean up legacy containers started via raw 'docker run' (before compose migration)
docker stop ai-job-hunter 2>/dev/null && docker rm ai-job-hunter 2>/dev/null || true
docker stop ai-job-hunter-llm 2>/dev/null && docker rm ai-job-hunter-llm 2>/dev/null || true
$COMPOSE $PROFILE_FLAGS down --remove-orphans 2>/dev/null || true

# ── Build images (layer cache preserved for fast rebuilds) ──
echo "→ Building images…"
$COMPOSE $PROFILE_FLAGS build

# ── Clean up dangling images from previous build ──
docker image prune -f 2>/dev/null || true

# ── Download model if LLM sidecar requested but model missing ──
if $WITH_LLM && [ ! -f "./models/model.gguf" ]; then
    echo "→ Downloading LLM model (Llama-3.2-3B-Instruct, ~1.8 GB)…"
    bash ./scripts/download_model.sh
fi

# ── Start containers ──
echo "→ Starting containers…"
$COMPOSE $PROFILE_FLAGS up -d

echo ""
echo "✓ AI Job Hunter is running"
echo "  App       : http://localhost:80"
echo "  Data      : ./data → /app/data"
if $WITH_LLM; then
    echo "  LLM API   : http://localhost:8080/v1  (internal: http://llm:8080/v1)"
fi
echo "  Logs      : $COMPOSE $PROFILE_FLAGS logs -f"
echo "  Stop      : $COMPOSE $PROFILE_FLAGS down"
