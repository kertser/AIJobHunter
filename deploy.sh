#!/usr/bin/env bash
# ─────────────────────────────────────────────────────
# AI Job Hunter — deployment script
# Stops our container, pulls latest code, rebuilds
# (with layer cache), and restarts.
# ─────────────────────────────────────────────────────
set -euo pipefail

CONTAINER_NAME="ai-job-hunter"
IMAGE_NAME="ai-job-hunter"
HOST_PORT=80
CONTAINER_PORT=8000
DATA_DIR="./data"
ENV_FILE=".env"

echo "═══ AI Job Hunter Deploy ═══"

# ── Stop and remove only our container ──
echo "→ Stopping container…"
docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm "$CONTAINER_NAME" 2>/dev/null || true

# ── Pull latest code ──
echo "→ Pulling latest code…"
git pull

# ── Build image (layer cache preserved for fast rebuilds) ──
echo "→ Building Docker image: ${IMAGE_NAME}…"
DOCKER_BUILDKIT=0 docker build -t "$IMAGE_NAME" .

# ── Clean up dangling images from previous build ──
docker image prune -f 2>/dev/null || true

# ── Run container ──
echo "→ Starting container: ${CONTAINER_NAME}…"
ENV_FLAG=""
if [ -f "$ENV_FILE" ]; then
    ENV_FLAG="--env-file ${ENV_FILE}"
else
    echo "  (no .env file found — skipping --env-file)"
fi

docker run -d \
    --name "$CONTAINER_NAME" \
    -p "${HOST_PORT}:${CONTAINER_PORT}" \
    -v "${DATA_DIR}:/app/data" \
    $ENV_FLAG \
    --restart unless-stopped \
    "$IMAGE_NAME"

echo ""
echo "✓ AI Job Hunter is running at http://localhost:${HOST_PORT}"
echo "  Container : ${CONTAINER_NAME}"
echo "  Data      : ${DATA_DIR} → /app/data"
echo "  Logs      : docker logs -f ${CONTAINER_NAME}"
echo "  Stop      : docker stop ${CONTAINER_NAME}"
