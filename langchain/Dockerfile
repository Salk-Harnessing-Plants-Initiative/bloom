# Source of Node.js binaries only (see the COPY block below) — same pinned digest
# web/Dockerfile.bloom-web.prod's builder stage already trusts.
FROM node:20-bookworm@sha256:a4545fc6f4f1483384ad5f4c71d34d71781c3779da407173ec6058079a718520 AS node_source

FROM python:3.11-slim@sha256:9358444059ed78e2975ada2c189f1c1a3144a5dab6f35bff8c981afb38946634

# Install uv from official image (pinned to version + immutable digest so rebuilds
# are bit-identical; matches the digest-pin pattern on the python base image above)
COPY --from=ghcr.io/astral-sh/uv:0.11@sha256:b1e699368d24c57cda93c338a57a8c5a119009ba809305cc8e86986d4a006754 /uv /uvx /bin/

# Venv outside /app so dev compose bind-mounts don't shadow it
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"

# Upgrade base packages to pick up any security patches released after the pinned
# base image digest. Run before installing anything so upgraded packages are present
# for all subsequent layers.
RUN apt-get update && apt-get upgrade -y && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Node.js (required for stdio-based MCP servers like web-search) — runs as root.
# Copied from the node_source stage above, NOT apt-installed: NodeSource's curl-piped
# setup script's signing-key fetch has started 403ing in CI, and on failure silently
# falls through to `apt-get install nodejs`, which then resolves to Debian trixie's own
# nodejs/node-lodash/node-undici packages — carrying unpatched CRITICAL CVEs
# (CVE-2026-48930/4800/1525) with no fixed Debian version available. node:20-bookworm
# ships upstream's own Node build (confirmed via a local Trivy scan: none of the three
# CVEs are present), sidestepping both problems at once.
#
# npm/npx are recreated as symlinks rather than copied directly: `COPY --from` dereferences
# a symlink source into a plain file, which breaks npm-cli.js's own `require('../lib/...')`
# (it ends up thinking its directory is /usr/local/bin instead of the real
# lib/node_modules/npm/bin) — confirmed by a failed local build before this was added.
COPY --from=node_source /usr/local/bin/node /usr/local/bin/node
COPY --from=node_source /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# node:20-bookworm's bundled global npm vendors its own tar (CVE-2026-59873, gzip-bomb
# DoS) at /usr/local/lib/node_modules/npm/node_modules/tar — unrelated to this app's own
# dependencies. Pin npm forward to a version that vendors the fixed tar (confirmed:
# npm@11.18.0 -> tar@7.5.19). Same fix as web/Dockerfile.bloom-web.prod's runner stage.
RUN npm install -g npm@11.18.0

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

# Probe with python, not curl — curl is not installed in this image.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5002/health')" || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5002", "--reload"]
