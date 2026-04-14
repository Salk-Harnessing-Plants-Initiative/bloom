FROM python:3.11-slim@sha256:9358444059ed78e2975ada2c189f1c1a3144a5dab6f35bff8c981afb38946634

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

# Venv outside /app so dev compose bind-mounts don't shadow it
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"

# Install Node.js (required for stdio-based MCP servers like web-search)
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies (cached independently of source code changes)
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-cache

COPY . .

# Create plots directory (shared with bloommcp via volume mount)
RUN mkdir -p /app/data/PLOTS_DIR

RUN addgroup --system bloom && adduser --system --ingroup bloom bloom
RUN chown -R bloom:bloom /app /opt/venv

EXPOSE 5002

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:5002/health || exit 1

USER bloom
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5002", "--reload"]
