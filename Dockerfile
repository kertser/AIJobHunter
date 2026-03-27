# ─── Build stage (no compiler needed — all deps are pre-built wheels) ───
FROM python:3.13-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /app

# 1) Copy only dependency specs (rarely change → layer cached)
COPY pyproject.toml uv.lock ./

# 2) Minimal package stub so uv can parse project metadata
RUN mkdir -p src/job_hunter && echo '__version__ = "0.0.0"' > src/job_hunter/__init__.py

# 3) Install ONLY third-party deps (cached unless lockfile changes)
RUN uv sync --frozen --no-dev --no-install-project

# 4) Copy real source (changes every deploy)
COPY src/ src/

# 5) Build & install the local package (fast — deps already cached above)
RUN uv sync --frozen --no-dev

# ─── Runtime stage ───
FROM python:3.13-slim

# All system deps in ONE call: Chromium runtime + Playwright extras + fonts.
# This eliminates the extra apt-get that "playwright install --with-deps" does.
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    # Chromium runtime libs
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxcomposite1 \
    libxrandr2 libxdamage1 libgbm1 libpango-1.0-0 libcairo2 \
    libasound2 libxshmfence1 libxkbcommon0 libxfixes3 \
    # OpenGL / GPU (needed even for headless software rendering)
    libgl1 libglx0 libglvnd0 libglx-mesa0 libgl1-mesa-dri libvulkan1 \
    # Fonts for proper text rendering (Latin, CJK, emoji)
    fonts-liberation fonts-freefont-ttf fonts-ipafont-gothic \
    fonts-noto-color-emoji fonts-wqy-zenhei \
    # Health check
    curl && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy pre-built venv from builder (all deps already installed)
COPY --from=builder /app/.venv .venv

# Copy source and config
COPY pyproject.toml uv.lock ./
COPY src/ src/

# Download Chromium binary only (system deps already installed above)
RUN uv run playwright install chromium 2>/dev/null || true

VOLUME ["/app/data"]

ENV JOBHUNTER_DATA_DIR=/app/data
ENV JOBHUNTER_HEADLESS=true

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

ENTRYPOINT ["uv", "run", "hunt"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
