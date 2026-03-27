#!/usr/bin/env bash
# ─────────────────────────────────────────────────────
# AI Job Hunter — deployment script
# Stops all running containers, prunes Docker resources,
# pulls the latest code, rebuilds the image, and starts
# the container with data volume and .env config.
# ─────────────────────────────────────────────────────
set -euo pipefail

CONTAINER_NAME="ai-job-hunter"
IMAGE_NAME="ai-job-hunter"
HOST_PORT=80
CONTAINER_PORT=8000
DATA_DIR="./data"
ENV_FILE=".env"

echo "═══ AI Job Hunter Deploy ═══"

# ── Stop running containers ──
echo "→ Stopping running containers…"
RUNNING=$(docker ps -q 2>/dev/null || true)
if [ -n "$RUNNING" ]; then
    docker stop $RUNNING
fi

# ── Prune Docker resources ──
echo "→ Pruning Docker system (images, containers, volumes)…"
docker system prune -a --volumes -f

# ── Pull latest code ──
echo "→ Pulling latest code…"
git pull

# ── Build image ──
echo "→ Building Docker image: ${IMAGE_NAME}…"
docker build -t "$IMAGE_NAME" .

# ── Run container ──
echo "→ Starting container: ${CONTAINER_NAME}…"
docker run -d \
    --name "$CONTAINER_NAME" \
    -p "${HOST_PORT}:${CONTAINER_PORT}" \
    -v "${DATA_DIR}:/app/data" \
    --env-file "$ENV_FILE" \
    --restart unless-stopped \
    "$IMAGE_NAME"

echo ""
echo "✓ AI Job Hunter is running at http://localhost:${HOST_PORT}"
echo "  Container : ${CONTAINER_NAME}"
echo "  Data      : ${DATA_DIR} → /app/data"
echo "  Logs      : docker logs -f ${CONTAINER_NAME}"
echo "  Stop      : docker stop ${CONTAINER_NAME}"

