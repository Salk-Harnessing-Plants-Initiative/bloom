FROM python:3.11-slim@sha256:9358444059ed78e2975ada2c189f1c1a3144a5dab6f35bff8c981afb38946634

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

# Venv outside /app so dev compose bind-mounts don't shadow it
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# System deps for matplotlib rendering and scipy (must precede uv sync)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6-dev \
    libpng-dev \
    libjpeg-dev \
    pkg-config \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies (cached independently of source code changes)
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-cache

COPY . .

# Create data directories (owned by bloommcp)
RUN mkdir -p /app/data/SLEAP_OUT_CSV /app/data/PLOTS_DIR /app/data/ANALYSIS_OUTPUT

RUN addgroup --system bloom && adduser --system --ingroup bloom bloom
RUN chown -R bloom:bloom /app /opt/venv

EXPOSE 8811

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8811/mcp')" || exit 1

USER bloom
CMD ["python", "server.py"]
