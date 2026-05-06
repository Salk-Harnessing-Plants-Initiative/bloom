# Project Context

## Purpose

Bloom is a full-stack web application for biological/scientific data visualization and management, specifically designed for handling cylindrical scan data. The application provides capabilities for storing, managing, and visualizing scientific imaging data, with specialized video generation functionality for cylindrical scan sequences.

## Tech Stack

### Frontend

- **Framework**: Next.js 16.2.0 (root `package.json`); React ships with Next (the `react: 18.2.0` override block in `package.json` is currently disabled — see `_overrides_disabled`)
- **Language**: TypeScript 5.9.3 (strict mode enabled)
- **UI Library**: Material-UI
- **Build Tool**: Turbo (monorepo)
- **Linting/Formatting**: ESLint 9 + Prettier (configured in root `package.json`)
- **Package Manager**: npm (Docker, Makefile, and `npm ci` in CI all use npm; lockfile is `package-lock.json`)

### Backend (Python services)

- **LangGraph agent** (`langchain/`): FastAPI + LangGraph (Python 3.11). Container `langchain-agent`, listens on port 5002.
- **FastMCP server** (`bloommcp/`): FastMCP exposing plant-data tools to the LangGraph agent (Python 3.11). Container `bloommcp`, listens on port 8811.
- **Video worker** (`services/video-worker/`): Python 3.11 service for cylindrical-scan video generation.
- **Database**: PostgreSQL via self-hosted Supabase (`supabase/postgres:15.x`)
- **Auth/Backend Services**: Supabase (self-hosted): Authentication (GoTrue), REST (PostgREST), Realtime, Storage, Studio UI

### Storage & Infrastructure

- **Object Storage**: MinIO (S3-compatible) on ports 9100-9101
- **API Gateway**: Kong on port 8000 (Supabase services)
- **Reverse Proxy**: Caddy in production (replaces the older Nginx setup as of #94); no reverse proxy in development (services are accessed directly)
- **Containerization**: Docker Compose with separate `docker-compose.dev.yml` / `docker-compose.prod.yml`
- **Volume Management**: Persistent volumes for Supabase (`volumes/`) and MinIO (`minio_data/`)

### Ports

Container-internal ports (same in every environment — hard-coded in each service):

- `bloom-web`: 3000
- `langchain-agent`: 5002
- `bloommcp`: 8811
- Kong Gateway: 8000
- PostgreSQL: 5432
- MinIO: 9100-9101
- Supabase Studio: 55323

Host-facing port mappings differ per environment:

- **Local dev** (`docker-compose.dev.yml`): `langchain-agent` and `bloommcp` are bound to the host via `${LANGCHAIN_PORT}` / `${BLOOMMCP_PORT}` for direct access (no Caddy in front).
- **Staging and production** (both use `docker-compose.prod.yml`, differentiated only by env files — `.env.staging.defaults` vs `.env.prod.defaults`): `langchain-agent` and `bloommcp` use `expose:` instead of `ports:` — they are reachable only over the internal Docker network, behind Caddy. The only host-facing port is Caddy's HTTPS listener (`CADDY_HTTPS_LISTEN_PORT`, set per env).
- A few host-facing ports differ between staging and prod via env vars — e.g., `POSTGRES_HOST_PORT=5432` (prod) vs `5433` (staging) (see comment at `docker-compose.prod.yml:481`).

## Project Conventions

### Code Style

#### TypeScript/JavaScript

- **Strict mode**: Enabled
- **Module system**: ESNext with Node resolution
- **JSX**: react-jsx transform
- **Path aliases**: `@/*` maps to project root
- **Naming**: Follow standard TypeScript conventions
  - PascalCase for components and classes
  - camelCase for functions and variables
  - UPPER_SNAKE_CASE for constants

#### Python (langchain-agent / bloommcp / video-worker)

- **Version**: Python 3.11 (pinned per service via `.python-version`)
- **Dependency management**: `uv` with per-service `pyproject.toml` and committed `uv.lock`. Conventions are codified in the `python-dependency-management` capability — see `openspec/changes/pin-python-deps/specs/python-dependency-management/spec.md` (in-flight via PR #160; will move to `openspec/specs/python-dependency-management/spec.md` on archive). CI security-scanning tools (`pip-audit`) MUST be pinned to a specific version (`uvx pip-audit@2.10.0`).
- **Naming**:
  - snake_case for functions and variables
  - PascalCase for classes
  - Type hints expected for new code

#### Linting & Formatting (currently configured)

- **Pre-commit hooks** (`.pre-commit-config.yaml`): `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-toml`, `check-merge-conflict`, `check-added-large-files`, `black`, `ruff` + `ruff-format`, `prettier`, `gitleaks`, and a local `uv-lock-check` (delegates to `scripts/check-uv-locks.py`).
- **CI enforcement** (`.github/workflows/pr-checks.yml`): npm audit (critical), TypeScript `tsc --noEmit`, Next.js build, per-service `uv lock --check` (lockfile-pyproject sync), `uvx pip-audit@2.10.0` per service, Docker builds + Trivy scans, env-defaults validation, env-parity check, migration linting, and integration tests against the prod compose stack.
- **Recommended-but-not-CI-blocking**: `black --check`, `ruff check`, `mypy` per service (run locally via `uv run`).

### Architecture Patterns

#### Monorepo Structure

```
/
├── web/                      # Next.js frontend application
├── langchain/                # LangGraph agent (FastAPI, Python 3.11, uv)
├── bloommcp/                 # FastMCP server (Python 3.11, uv)
├── services/
│   └── video-worker/         # Cylindrical-scan video generation (Python 3.11, uv)
├── packages/                 # Shared TypeScript packages
│   ├── bloom-fs/             # File system utilities
│   ├── bloom-js/             # Shared JavaScript utilities
│   └── bloom-nextjs-auth/    # Authentication helpers
├── supabase/                 # Supabase configuration + migrations
├── caddy/                    # Caddyfile (reverse proxy, prod + staging)
├── scripts/                  # Utility scripts (check-uv-locks.py, setup-env-secrets.sh, etc.)
├── tests/
│   ├── integration/          # pytest — runs against the prod compose stack
│   └── unit/                 # pytest — pure-Python checks (env defaults, uv conventions, etc.)
├── openspec/                 # OpenSpec proposals + specs
└── volumes/                  # Docker volume mounts
```

#### Service Architecture

- **Microservices approach**: Frontend (`bloom-web`), LangGraph agent (`langchain-agent`), FastMCP server (`bloommcp`), video worker, and the Supabase stack all run as separate containers.
- **API Gateway pattern**: Kong sits in front of Supabase services (Auth, REST, Storage, Realtime).
- **Reverse proxy**: Caddy in staging/prod fronts the public surface; dev does not use Caddy.
- **Environment separation**: `docker-compose.dev.yml` for local dev; `docker-compose.prod.yml` for both staging and production (differentiated by env files).
- **Volume persistence**: Data persists across container restarts.

#### Key Design Patterns

- **Multi-stage Docker builds** for production optimization
- **Volume mounts** in development for hot reload
- **Environment-based configuration** via .env files
- **Service naming convention**: `{service}-{env}` pattern

### Testing Strategy

- **Integration tests** (`tests/integration/`, pytest): exercise the full prod compose stack (`make prod-up` then `uv run --extra test pytest tests/integration/`). Covers API endpoints, Supabase, migrations, top router, smoke. Runs in CI's `compose-health-check` job after `docker-build` succeeds.
- **Unit tests** (`tests/unit/`, pytest): pure-Python checks — env defaults, env-parity verifier, agent unit, `check-uv-locks` script, and a regression-guard test (`test_ci_workflow_uv_conventions.py`) that walks `.github/workflows/*.yml` and asserts the uv conventions in `python-dependency-management` aren't re-broken. Runs in CI's `python-audit` job.
- **Test runner**: pinned via the root `pyproject.toml` `test` extra (single source of truth — CI invokes via `uv run --extra test pytest …`, never inline `--with`).
- **Frontend tests**: not currently in CI. Playwright e2e infra is present in `web/` but not yet wired into `pr-checks.yml`.

### Git Workflow

#### Branching Strategy (staging-first)

- **`staging`**: Integration branch. Every feature/fix/docs PR targets `staging` by default. Branch protection requires one non-author approving review (`enforce_admins=true` — admins do not bypass).
- **`main`**: Consolidation/release branch. Receives periodic `staging → main` rollup PRs. Releases are tagged here. Same protection rules.
- **Feature branches**: Create from `origin/staging` (`git fetch origin staging && git checkout -b <name> origin/staging`) so the branch starts from the integration tip.
- **Commit style**: Conventional Commits (`feat:`, `fix:`, `docs:`, `ops:`, `chore:`, etc.) with optional scope.

#### Environment Management

- `.env.dev` for local development
- `.env.staging.defaults` and `.env.prod.defaults` for the deployed environments (committed defaults; secrets pushed via `scripts/setup-env-secrets.sh`)
- Separate environment files for web app and Docker stack

## Domain Context

### Scientific Imaging Domain

- **Primary data type**: Cylindrical scan images
- **Data workflow**:
  1. Scan data stored in PostgreSQL (`cyl_scanners`, `cyl_images` tables)
  2. Image files stored in MinIO (S3-compatible storage)
  3. The `services/video-worker` service generates videos from image sequences
  4. The LangGraph agent (`langchain-agent`) provides AI-powered analysis via tools exposed by the FastMCP server (`bloommcp`)
  5. The Next.js frontend provides visualization and management

### Key Database Tables

- `cyl_scanners`: Scanner metadata
- `cyl_images`: Individual scan images with S3 references

### Video Generation

- `services/video-worker` consumes image sequences from S3 and produces videos
- Authenticated via JWT (Supabase Auth)

## Important Constraints

### Technical Constraints

- **React version**: ships with Next.js 16.2.0 (the `react: 18.2.0` override block in root `package.json` is currently disabled — `_overrides_disabled`)
- **Python version**: 3.11 for all Python services (pinned per-service via `.python-version`)
- **Storage requirement**: MinIO needs a writable `minio_data/` directory at the repo root (gitignored; `chmod 777` for Docker)

### Infrastructure Constraints

- **Self-hosted Supabase**: Requires full stack deployment, not using Supabase cloud
- **MinIO configuration**: Requires proper initialization and bucket setup
- **Volume persistence**: Critical data stored in Docker volumes

### Development Constraints

- **Environment files required**: Must have `.env.dev` and `.env.prod` configured
- **Test data**: Use `dev_init.ts` script with appropriate NODE_ENV
- **Port availability**: Multiple services require specific ports (see Tech Stack section)

## External Dependencies

### Required External Services

- **Docker & Docker Compose**: For running the full stack
- **MinIO**: S3-compatible object storage (self-hosted)
- **Supabase**: Full backend platform (self-hosted)
  - PostgreSQL database
  - Auth service
  - Storage service
  - Realtime service
  - REST API
  - Studio UI

### External Packages

- **Frontend**: Next.js, React, Material-UI, TypeScript
- **LangGraph agent**: FastAPI, LangGraph, langchain-core, boto3, PyJWT
- **FastMCP server**: FastMCP, statsmodels, umap, pandas (per the smoke imports in pre-merge.md)
- **Video worker**: see `services/video-worker/pyproject.toml`
- **Shared**: Supabase client libraries, custom bloom packages (`bloom-fs`, `bloom-js`, `bloom-nextjs-auth`)
- **Build**: Turbo for monorepo orchestration; `uv` for Python services

### Network Dependencies

- **Kong API Gateway**: Routes requests to Supabase services
- **Caddy**: Reverse proxy in staging and production (replaces older Nginx)
- **Inter-service communication**: Services communicate via the `supanet` Docker network

### Development Tools

- **ts-node**: For running TypeScript scripts
- **Make**: Orchestration via Makefile commands
  - `make dev-up` / `make prod-up`
  - `make dev-down` / `make prod-down`
  - `make rebuild-dev-fresh` / `make rebuild-prod-fresh`
  - `make migrate-local` (Supabase migrations)
