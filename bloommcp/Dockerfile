FROM python:3.11-slim@sha256:9358444059ed78e2975ada2c189f1c1a3144a5dab6f35bff8c981afb38946634

# Install uv from official image (pinned to version + immutable digest so rebuilds
# are bit-identical; matches the digest-pin pattern on the python base image above)
COPY --from=ghcr.io/astral-sh/uv:0.11@sha256:b1e699368d24c57cda93c338a57a8c5a119009ba809305cc8e86986d4a006754 /uv /uvx /bin/

# Venv outside /app so dev compose bind-mounts don't shadow it
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"

# No apt-get block: matplotlib, numpy, scipy, and every other native-code dependency
# (scikit-learn, statsmodels, pillow, numba/llvmlite, etc.) publish manylinux wheels
# for this image's cp311 + x86_64/aarch64 target, so `uv sync` resolves prebuilt
# wheels rather than compiling from source (confirmed via build log; see #590). A
# future dependency that genuinely needs native compilation should reintroduce an
# apt-get block then — tests/unit/test_bloommcp_dockerfile_shape.py guards against
# a silent regression back to one being unnecessary.

# Create bloom user AND pre-create bloom-owned dirs in a single layer BEFORE the
# venv install, so `uv sync` (and every subsequent COPY) can run as bloom — no
# trailing `chown -R` over the ~hundreds-of-MB site-packages tree on source changes.
RUN addgroup --system bloom && adduser --system --ingroup bloom bloom \
    && mkdir -p /opt/venv \
                /app/data/TRAITS_DIR \
                /app/data/PLOTS_DIR \
                /app/data/ANALYSIS_OUTPUT \
    && chown -R bloom:bloom /opt/venv /app

WORKDIR /app
USER bloom

# Install dependencies only (cached independently of source code changes).
# --no-install-project: bloom_mcp is now a real package under src/, which isn't
# present at this layer — installing it here would fail, so defer it.
COPY --chown=bloom:bloom pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-cache --no-install-project

COPY --chown=bloom:bloom . .

# Install the project itself now that src/ is present. The install is editable
# and the venv lives at /opt/venv (outside /app), so the dev bind-mount of
# ./bloommcp:/app does not shadow it while the editable path /app/src/bloom_mcp
# still resolves to the mounted source — preserving hot-reload.
RUN uv sync --frozen --no-dev --no-cache

EXPOSE 8811

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8811/health', timeout=3).close()" || exit 1

CMD ["python", "-m", "bloom_mcp"]
