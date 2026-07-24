# Base image digest-pinned to match sibling services (langchain, bloommcp)
FROM python:3.11-slim@sha256:9358444059ed78e2975ada2c189f1c1a3144a5dab6f35bff8c981afb38946634

# Install uv from official image (pinned to version + immutable digest so rebuilds
# are bit-identical; matches the digest-pin pattern on the python base image above)
COPY --from=ghcr.io/astral-sh/uv:0.11@sha256:b1e699368d24c57cda93c338a57a8c5a119009ba809305cc8e86986d4a006754 /uv /uvx /bin/

# Venv outside /app so dev bind-mounts (if ever used) don't shadow it
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"

# bloomctl's dependencies (click, rich, httpx, supabase, python-dotenv,
# cryptography, sleap-roots-contracts) are all pure-Python / prebuilt-wheel —
# no apt-get block needed here, unlike bloommcp's matplotlib/scipy native deps.

RUN addgroup --system bloom && adduser --system --ingroup bloom bloom \
    && mkdir -p /opt/venv \
    && chown -R bloom:bloom /opt/venv

WORKDIR /app
USER bloom

# Install dependencies only (cached independently of source code changes).
COPY --chown=bloom:bloom pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-cache --no-install-project

COPY --chown=bloom:bloom . .

# Install the project itself now that src/ is present.
RUN uv sync --frozen --no-dev --no-cache

ENTRYPOINT ["bloomctl"]
