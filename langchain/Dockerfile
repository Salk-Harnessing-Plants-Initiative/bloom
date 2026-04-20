FROM python:3.11-slim@sha256:9358444059ed78e2975ada2c189f1c1a3144a5dab6f35bff8c981afb38946634

# Install uv from official image (pinned to version + immutable digest so rebuilds
# are bit-identical; matches the digest-pin pattern on the python base image above)
COPY --from=ghcr.io/astral-sh/uv:0.11@sha256:b1e699368d24c57cda93c338a57a8c5a119009ba809305cc8e86986d4a006754 /uv /uvx /bin/

# Venv outside /app so dev compose bind-mounts don't shadow it
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"

# Install Node.js (required for stdio-based MCP servers like web-search) — runs as root
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Create bloom user AND pre-create bloom-owned dirs in a single layer BEFORE the
# venv install, so `uv sync` (and every subsequent COPY) can run as bloom — no
# trailing `chown -R` over the site-packages tree on source changes.
RUN addgroup --system bloom && adduser --system --ingroup bloom bloom \
    && mkdir -p /opt/venv /app/data/PLOTS_DIR \
    && chown -R bloom:bloom /opt/venv /app

WORKDIR /app
USER bloom

# Install dependencies (cached independently of source code changes)
COPY --chown=bloom:bloom pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-cache

COPY --chown=bloom:bloom . .

EXPOSE 5002

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:5002/health || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5002", "--reload"]
