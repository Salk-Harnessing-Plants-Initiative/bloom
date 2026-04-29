---
name: CI Debug
description: Debug GitHub Actions CI failures for Bloom's Next.js + FastAPI + Docker stack
category: Troubleshooting
tags: [ci, github-actions, debugging, testing]
---

# CI Debug - GitHub Actions Pipeline

Guide for debugging CI failures in Bloom (Next.js + FastAPI/LangGraph + FastMCP + Docker + Supabase).

## CI Pipeline Overview

### PR Checks (`pr-checks.yml`) — runs on PRs to `main`

| Job | Purpose | Key Commands |
|---|---|---|
| `build-and-audit` | npm CVE audit, TypeScript check, Next.js build | `npm ci`, `npm audit --audit-level=critical`, `npx tsc --noEmit`, `npm run build` |
| `python-audit` | Python CVE scanning | `uv export --frozen --no-hashes` piped to `uvx pip-audit` per service |
| `docker-build` | Build + Trivy scan Docker images | Build `bloom-web`, `langchain-agent`, `bloommcp`; Trivy CRITICAL gate |
| `compose-health-check` | Full stack integration tests | Start prod compose, wait 180s for health, `uv run --with pytest pytest tests/integration/` |
| `extract-pinned-images` | Extract pinned images for matrix scan | Grep `image:` from compose |
| `scan-pinned-images` | CVE scan each pinned image (matrix) | Trivy per-image |
| `pinned-images-summary` | Aggregate CVE report | Post combined table as PR comment |

### Deploy (`deploy.yml`) — runs on release publish

- Build + verify (same checks as PR)
- `deploy-staging` — stub (TODO)
- `deploy-production` — stub (TODO)

## Quick Diagnosis

### Step 1: Identify Failing Job

```bash
gh pr checks <PR_NUMBER>
```

### Step 2: View Logs

```bash
# List recent workflow runs
gh run list --limit 5

# View failed run logs
gh run view <RUN_ID> --log-failed
```

### Step 3: Common Failure Patterns

| Symptom | Likely Cause | Quick Fix |
|---|---|---|
| `npm audit` fails | CVE in npm dependency | Update vulnerable package |
| `tsc` type errors | TypeScript compilation issues | `cd web && npx tsc --noEmit` locally |
| `npm run build` fails | Next.js build error | `cd web && npm run build` locally |
| `pip-audit` fails | CVE in Python dependency | Update in `langchain/pyproject.toml` or `bloommcp/pyproject.toml` and run `uv lock` |
| Docker build fails | Dockerfile or dependency issue | `docker compose -f docker-compose.prod.yml build` |
| Trivy CRITICAL | Critical CVE in Docker image | Update base image or dependency |
| Health check timeout | Service not starting | Check container logs |
| Integration tests fail | API or database issue | Run tests locally against dev stack |

## Job-by-Job Debugging

### 1. `build-and-audit`

**What it does:**
1. `npm ci` — install dependencies from lockfile
2. `npm audit --audit-level=critical` — check for CVEs
3. Check Caddyfile has no `auto_https off` (dev-only setting)
4. Build shared packages (`packages/bloom-js`, `packages/bloom-fs`) via `tsc`
5. `cd web && npx tsc --noEmit` — TypeScript type check
6. `cd web && npm run build` — Next.js production build

**Debug locally:**

```bash
# Reproduce the full job
npm ci
npm audit --audit-level=critical
cd web && npx tsc --noEmit && npm run build
```

**Common failures:**

- **npm audit:** A dependency has a known CVE. Fix: `npm update <package>` or add to audit exceptions
- **TypeScript errors:** Type mismatch or missing types. Fix: resolve type errors in `web/`
- **Build errors:** Next.js build failure (missing env vars, import errors). Fix: check build output for specific error

---

### 2. `python-audit`

**What it does:**
- Exports pinned versions from `uv.lock` and scans all transitive dependencies for known CVEs

**Debug locally:**

```bash
cd langchain && uv export --frozen --no-hashes | uvx pip-audit -r /dev/stdin
cd bloommcp && uv export --frozen --no-hashes | uvx pip-audit -r /dev/stdin
cd services/video-worker && uv export --frozen --no-hashes | uvx pip-audit -r /dev/stdin
```

**Common failures:**
- A Python dependency has a known CVE. Fix: update the package in the relevant `pyproject.toml` with `uv add` and run `uv lock`

---

### 3. `docker-build`

**What it does:**
1. Builds three Docker images: `bloom-web`, `langchain-agent`, `bloommcp`
2. Scans each with Trivy for CRITICAL and HIGH vulnerabilities
3. Posts CVE report as a sticky PR comment
4. Fails if any CRITICAL CVE is found

**Debug locally:**

```bash
# Build all images
docker compose -f docker-compose.prod.yml build

# Build specific service
docker compose -f docker-compose.prod.yml build bloom-web
docker compose -f docker-compose.prod.yml build langchain-agent
docker compose -f docker-compose.prod.yml build bloommcp

# Run Trivy scan (if installed)
trivy image bloom-web:latest --severity CRITICAL,HIGH
```

**Common failures:**
- **Build failure:** Check Dockerfile syntax, missing files in build context, dependency installation errors
- **Trivy CRITICAL:** Update base image or vulnerable package

---

### 4. `compose-health-check`

**What it does:**
1. Depends on `docker-build` completing successfully
2. Generates `.env.ci` from GitHub secrets
3. Starts the full prod compose stack
4. Waits up to 180 seconds for all services to report healthy
5. Runs `uv run --with pytest pytest tests/integration/ -v --tb=short`
6. Tears down with `docker compose down -v`

**Debug locally:**

```bash
# Start the prod stack (CI uses prod compose with Caddy routing)
make prod-up

# Check service health
docker compose -f docker-compose.prod.yml ps

# Check specific container logs
docker compose -f docker-compose.prod.yml logs bloom-web
docker compose -f docker-compose.prod.yml logs langchain-agent
docker compose -f docker-compose.prod.yml logs bloommcp
docker compose -f docker-compose.prod.yml logs db-prod

# Run integration tests
uv run --with pytest pytest tests/integration/ -v --tb=short

# Teardown
make prod-down
```

**Common failures:**
- **Health check timeout:** A service is failing to start. Check logs for the unhealthy container
- **Database not ready:** `docker exec db-prod pg_isready -U supabase_admin -h localhost`
- **Integration test failure:** Check test output for specific assertion errors
- **Port conflicts:** Another process using required ports. Stop conflicting containers

---

### 5-7. Pinned Image Scanning

`extract-pinned-images` → `scan-pinned-images` (matrix) → `pinned-images-summary`

These scan every third-party image pinned in `docker-compose.prod.yml` (PostgreSQL, GoTrue, PostgREST, Kong, etc.). Failures here are **warnings, not blocking** — the jobs post a CVE summary PR comment but don't fail the build (except for CRITICAL in custom-built images).

## Docker Issues

### Build Context Too Large

```bash
# Check .dockerignore exists and covers node_modules, .next, .turbo, .git
cat .dockerignore
```

### Layer Caching Issues

```bash
# Build without cache
docker compose -f docker-compose.prod.yml build --no-cache
```

### Multi-Stage Build Fails

```bash
# Build specific stage
docker build --target builder -f web/Dockerfile.bloom-web.prod .
```

## Database Issues

### Migrations Not Applied

```bash
# Check if DB is running
docker exec db-dev pg_isready -U supabase_admin -h localhost

# Apply migrations
make migrate-local

# Check migration status
docker exec db-dev psql -U supabase_admin -d postgres -c "SELECT * FROM _migrations ORDER BY applied_at;"
```

### RLS Policies Blocking

```bash
# Connect to database directly
PGPASSWORD=postgres psql -h localhost -p 5432 -U supabase_admin -d postgres

# List RLS policies
\dp
```

## MinIO/Storage Issues

### Bucket Not Initialized

```bash
# Check MinIO is running
docker compose -f docker-compose.dev.yml ps supabase-minio

# Create bucket
make create-bucket

# List buckets
make list-buckets
```

### Health Check

```bash
curl http://localhost:9000/minio/health/live
```

## Environment Variable Issues

No `.env.example` exists in this repo. In CI, `.env.ci` is generated from GitHub secrets in the `compose-health-check` job. Key variables include:

- `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`
- `JWT_SECRET`, `ANON_KEY`, `SERVICE_ROLE_KEY`
- `SITE_URL`, `API_EXTERNAL_URL`
- `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD`
- `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION`, `S3_ENDPOINT`, `S3_BUCKET`
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (for LangGraph agent)

For local development, check the `compose-health-check` job in `.github/workflows/pr-checks.yml` for the full list.

## Reproducing CI Locally

```bash
# Match CI environment
export CI=true

# Phase 1: build-and-audit
npm ci && npm audit --audit-level=critical && cd web && npx tsc --noEmit && npm run build && cd ..

# Phase 2: python-audit
cd langchain && uv export --frozen --no-hashes | uvx pip-audit -r /dev/stdin
cd bloommcp && uv export --frozen --no-hashes | uvx pip-audit -r /dev/stdin
cd services/video-worker && uv export --frozen --no-hashes | uvx pip-audit -r /dev/stdin

# Phase 3: docker-build
docker compose -f docker-compose.prod.yml build

# Phase 4: compose-health-check (prod stack, matching CI)
make prod-up
# Wait for services...
docker compose -f docker-compose.prod.yml ps
uv run --with pytest pytest tests/integration/ -v --tb=short
make prod-down
```

## Debugging Checklist

When CI fails:

- [ ] Which job failed? (`gh pr checks <number>`)
- [ ] What's the error message? (`gh run view <id> --log-failed`)
- [ ] Can you reproduce locally?
- [ ] Are all Docker services healthy? (`docker compose ps`)
- [ ] Is the database ready? (`docker exec db-dev pg_isready`)
- [ ] Are migrations applied? (`make migrate-local`)
- [ ] Are environment variables set?
- [ ] Did a dependency update introduce a CVE? (`npm audit` / `uv export | uvx pip-audit`)
- [ ] Is the Docker build cache stale? (`docker compose build --no-cache`)

## Related Commands

- `/run-ci-locally` — run the full CI suite locally
- `/validate-env` — validate development environment setup
- `/lint` — run linting checks
- `/coverage` — run test coverage analysis
- `/fix-formatting` — auto-fix code formatting issues