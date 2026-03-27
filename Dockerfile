# ─── Build stage ───
FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && \
    rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ src/

# Install dependencies (frozen from lockfile)
RUN uv sync --frozen --no-dev

# ─── Runtime stage ───
FROM python:3.13-slim

# Playwright system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxcomposite1 \
    libxrandr2 libxdamage1 libgbm1 libpango-1.0-0 libcairo2 \
    libasound2 libxshmfence1 fonts-liberation curl && \
    rm -rf /var/lib/apt/lists/*

# Install uv in runtime
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy installed venv from builder
COPY --from=builder /app/.venv .venv

# Copy source and config (single copy, not duplicated)
COPY pyproject.toml uv.lock ./
COPY src/ src/

# Install Playwright Chromium
RUN uv run playwright install chromium --with-deps 2>/dev/null || true


# Data directory is a volume mount point
VOLUME ["/app/data"]

# Environment defaults
ENV JOBHUNTER_DATA_DIR=/app/data
ENV JOBHUNTER_HEADLESS=true

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

ENTRYPOINT ["uv", "run", "hunt"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]

