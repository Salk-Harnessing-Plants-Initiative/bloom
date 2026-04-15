# Claude Commands Adaptation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adapt all `.claude/commands/` files to accurately reflect the salk-bloom stack, and add 3 new commands + 1 skill from bloom-desktop.

**Architecture:** Each task is an independent group of command files that can be edited in parallel. All tasks share a canonical glossary and replacement table defined in the design spec at `docs/superpowers/specs/2026-04-08-claude-commands-adaptation-design.md`. The implementing agent MUST read that spec before starting any task.

**Tech Stack:** Markdown command files for Claude Code CLI. No runtime code — only documentation and prompt content.

**Reference data for all tasks:**

<details>
<summary>Root package.json scripts (the ONLY npm scripts that exist)</summary>

| Script | Command |
|---|---|
| `lint` | `turbo run lint` |
| `lint:fix` | `turbo run lint:fix` |
| `format` | `prettier --write "**/*.{js,jsx,ts,tsx,json,md}"` |
| `format:check` | `prettier --check "**/*.{js,jsx,ts,tsx,json,md}"` |
| `test` | `turbo run test` (no workspace implements this — effectively a no-op) |
| `test:coverage` | `turbo run test:coverage` (no workspace implements this — effectively a no-op) |

`web/package.json` scripts: `next`, `dev`, `build`, `start`, `init-env`. No `lint`, `test`, or `type-check`.
</details>

<details>
<summary>Actual CI jobs (pr-checks.yml)</summary>

| Job | Key commands |
|---|---|
| `build-and-audit` | `npm ci`, `npm audit`, Caddyfile check, `tsc` in packages, `cd web && npx tsc --noEmit`, `cd web && npm run build` |
| `python-audit` | `pip-audit -r langchain/requirements.txt`, `pip-audit -r bloommcp/requirements.txt` |
| `docker-build` | Build `bloom-web`, `langchain-agent`, `bloommcp` images; Trivy CVE scan |
| `compose-health-check` | Start prod compose with `.env.ci`; wait 180s for healthy; `uv run pytest tests/integration/`; teardown |
| `extract-pinned-images` | Extract `image:` lines from compose for matrix |
| `scan-pinned-images` | Trivy scan per pinned image (matrix) |
| `pinned-images-summary` | Aggregate Trivy results, post PR comment |
</details>

<details>
<summary>Makefile targets</summary>

`dev-up`, `prod-up`, `staging-up`, `dev-down`, `prod-down`, `staging-down`, `rebuild-dev-fresh`, `rebuild-prod-fresh`, `dev-logs`/`logs`, `prod-logs`, `staging-logs`, `reset-storage`, `new-migration`, `apply-migrations-local`, `load-test-data`, `upload-images`, `create-bucket`, `list-buckets`, `rebuild`, `configure-storage-dev`, `configure-storage-prod`, `gen-types`
</details>

<details>
<summary>Docker services</summary>

**Prod** (16): `caddy`, `bloom-web`, `langchain-agent`, `bloommcp`, `kong`, `auth`, `rest`, `realtime`, `supabase-minio`, `minio-init`, `storage`, `db-prod`, `supavisor`, `studio`, `imgproxy`, `meta`

**Dev** (16): `bloom-web`, `langchain-agent`, `bloommcp`, `studio`, `kong`, `auth`, `rest`, `realtime`, `supabase-minio`, `minio-init`, `storage`, `imgproxy`, `meta`, `db-dev`, `swagger-ui`, `supavisor`
</details>

---

### Task 1: Independent minor/moderate files (Agent A)

**Files:**
- Modify: `.claude/commands/changelog.md`
- Modify: `.claude/commands/cleanup-merged.md`
- Modify: `.claude/commands/release.md`
- Modify: `.claude/commands/docs-review.md`

**Context:** These files have minimal cross-references to other commands. Read the design spec's canonical glossary and replacement table before starting.

- [ ] **Step 1: Read the design spec**

Read `docs/superpowers/specs/2026-04-08-claude-commands-adaptation-design.md` in full. Internalize the canonical glossary and replacement table.

- [ ] **Step 2: Fix changelog.md**

Read `.claude/commands/changelog.md`. Apply these changes:
- Replace all `flask` package labels in examples with `langchain` and `bloommcp` (e.g., `- **flask**: Video generation` → `- **langchain**: LangGraph agent endpoint`)
- Replace `VideoWriter` examples with real salk-bloom examples (e.g., Supabase migration, Docker config change, API endpoint)
- Replace `git log --oneline -- flask/` with `git log --oneline -- langchain/ bloommcp/`
- Keep the overall structure and Keep a Changelog format guidance intact

- [ ] **Step 3: Verify changelog.md**

```bash
grep -ri "flask\|videoWriter\|VideoWriter\|pnpm" .claude/commands/changelog.md
```
Expected: zero results.

- [ ] **Step 4: Fix cleanup-merged.md**

Read `.claude/commands/cleanup-merged.md`. This file is already clean (no pnpm/flask references). Verify and confirm no changes needed, OR apply any fixes discovered during reading.

- [ ] **Step 5: Fix release.md**

Read `.claude/commands/release.md`. Apply these changes:
- Replace all `pnpm` → `npm run` (e.g., `pnpm lint` → `npm run lint`, `pnpm build` → `npm run build`, `pnpm install` → `npm install`)
- Replace `pnpm install --frozen-lockfile` → `npm ci`
- Replace `pnpm-lock.yaml` in git add commands with `package-lock.json`
- Replace `cd flask && uv run black --check . && uv run ruff check . && uv run mypy .` → `cd langchain && uv run black --check . && cd ../bloommcp && uv run black --check .`
- Replace `cd flask && uv run pytest --cov --cov-fail-under=70` → `uv run pytest tests/integration/ -v --tb=short`
- Remove or update "Phase 2+" references — replace with "when test infrastructure is added"
- Replace `npm version --workspaces --include-workspace-root` with the correct npm version command if applicable
- Keep the overall release workflow structure intact

- [ ] **Step 6: Verify release.md**

```bash
grep -ri "pnpm\|flask\|Phase 2" .claude/commands/release.md
```
Expected: zero results.

- [ ] **Step 7: Fix docs-review.md**

Read `.claude/commands/docs-review.md`. This file contains embedded documentation templates (API.md, ARCHITECTURE.md, DEVELOPMENT.md) with extensive flask/pnpm/VideoWriter/port 5002 references. Apply these changes:

- Replace all `pnpm` commands in templates → `npm run` equivalents
- Replace `npm install -g pnpm` → remove
- Replace `flask/app.py`, `flask/videoWriter.py`, `flask/config.py` → `langchain/main.py`, `bloommcp/main.py`
- Replace `@app.route` grep → `@app.` or FastAPI route decorator
- Replace `VideoWriter` class documentation → LangGraph agent or FastMCP server documentation
- Replace port `5002` → document Caddy reverse proxy routing
- Replace `Flask (Python 3.11)` → `FastAPI + LangGraph (Python 3.11)` and `FastMCP (Python 3.11)`
- Replace `pnpm workspaces` → `npm workspaces + Turborepo`
- Replace `supabase db push` in templates → `make apply-migrations-local`
- Replace `Phase 2` references → remove or reword
- Replace `cd flask && uv run python` → `cd langchain && uv run python` or appropriate
- Update ARCHITECTURE.md template to reflect actual architecture: Next.js + Supabase + FastAPI/LangGraph + FastMCP + Docker + Caddy

- [ ] **Step 8: Verify docs-review.md**

```bash
grep -ri "pnpm\|flask\|videoWriter\|VideoWriter\|VideoPlayer\|port 5002\|localhost:5002\|Phase 1\|Phase 2" .claude/commands/docs-review.md
```
Expected: zero results.

- [ ] **Step 9: Commit Agent A files**

```bash
git add .claude/commands/changelog.md .claude/commands/cleanup-merged.md .claude/commands/release.md .claude/commands/docs-review.md
git commit -m "docs: adapt changelog, cleanup-merged, release, docs-review commands for salk-bloom stack

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Python tooling files (Agent B)

**Files:**
- Modify: `.claude/commands/lint.md` (major rewrite)
- Modify: `.claude/commands/fix-formatting.md` (moderate fixes)
- Modify: `.claude/commands/coverage.md` (full rewrite)

- [ ] **Step 1: Read the design spec**

Read `docs/superpowers/specs/2026-04-08-claude-commands-adaptation-design.md` in full.

- [ ] **Step 2: Rewrite lint.md**

Read the current `.claude/commands/lint.md`. Then rewrite it to accurately describe the salk-bloom linting setup:

**TypeScript/JavaScript:**
- ESLint: `npm run lint` (runs via Turborepo across workspaces that define a `lint` script)
- `npm run lint:fix` for auto-fix
- Prettier: `npm run format` / `npm run format:check`
- Config: `.eslintrc.json` at repo root, `.prettierrc.json` at repo root
- Type checking: `cd web && npx tsc --noEmit` (no `type-check` script exists)

**Python (langchain/ and bloommcp/):**
- Black: `cd langchain && uv run black .` / `cd bloommcp && uv run black .`
- Ruff: `cd langchain && uv run ruff check .` / `cd bloommcp && uv run ruff check .`
- mypy: `cd langchain && uv run mypy . --ignore-missing-imports` (same for bloommcp)
- Config: each service has its own `requirements.txt`; no shared `pyproject.toml` for Python tools
- **Important note:** Python linting is NOT enforced in CI. CI only runs `pip-audit` for CVE scanning. Python linting is recommended locally and via pre-commit hooks.

**Pre-commit hooks** (`.pre-commit-config.yaml`):
- General: trailing-whitespace, end-of-file-fixer, check-yaml, check-added-large-files, check-merge-conflict, check-toml
- Python: Black, Ruff, mypy targeting `^(langchain|bloommcp)/`
- JS/TS: Prettier on `.(js|jsx|ts|tsx|json|md)$`

**CI context:**
- `build-and-audit` job: `npm audit`, `npx tsc --noEmit`, `npm run build` (type + build check)
- `python-audit` job: `pip-audit` only (no Black/Ruff/mypy)

Remove all references to: `pnpm`, `flask/`, `flask/pyproject.toml`, Phase 1/2, Vitest, Jest, Codecov.

- [ ] **Step 3: Verify lint.md**

```bash
grep -ri "pnpm\|flask\|Phase 1\|Phase 2\|Vitest\|Jest\|Codecov\|pytest-flask" .claude/commands/lint.md
```
Expected: zero results.

- [ ] **Step 4: Fix fix-formatting.md**

Read `.claude/commands/fix-formatting.md`. Apply these changes:
- Replace all `pnpm format` → `npm run format`
- Replace all `pnpm lint` → `npm run lint`
- Replace `pnpm lint --fix` → `npm run lint:fix`
- Replace `pnpm test` → `uv run pytest tests/integration/ -v --tb=short`
- Replace `pnpm install` → `npm install`
- Replace `pnpm prettier --version` → `npx prettier --version`
- Replace `cd flask` → `cd langchain` (and add equivalent for `bloommcp`)
- Replace `git diff flask/` → `git diff langchain/ bloommcp/`
- Replace `VideoPlayer` in formatting examples with a real component name (check `web/src/` for actual component names)
- Replace Makefile section: check if `make format` exists (it does NOT in the Makefile) — remove or note it's not configured
- Update pre-commit section to reference the fixed `.pre-commit-config.yaml` with `^(langchain|bloommcp)/`
- Replace `flask/pyproject.toml` config references → note that Python tools use defaults or `requirements.txt`

- [ ] **Step 5: Verify fix-formatting.md**

```bash
grep -ri "pnpm\|flask\|VideoPlayer\|videoWriter" .claude/commands/fix-formatting.md
```
Expected: zero results.

- [ ] **Step 6: Rewrite coverage.md**

The current `coverage.md` is entirely bloom-desktop content (VideoWriter, flask, pytest-flask, 70%/80% thresholds). Write it from scratch:

**Current state of testing in salk-bloom:**
- **Integration tests only**: `tests/integration/` — `test_api_endpoints.py`, `test_smoke.py`, `test_supabase.py`
- **Runner**: `uv run pytest tests/integration/ -v --tb=short`
- **With coverage**: `uv run pytest tests/integration/ --cov --cov-report=term-missing`
- **No frontend unit tests exist yet** (no Vitest, no Jest)
- **No coverage thresholds enforced** in CI
- **CI runs integration tests** in the `compose-health-check` job after full stack health

**Coverage commands:**
```bash
# Run integration tests with coverage report
uv run pytest tests/integration/ --cov --cov-report=term-missing --cov-report=html -v

# View HTML report
open htmlcov/index.html  # macOS
start htmlcov/index.html  # Windows

# Run with coverage threshold (for future enforcement)
uv run pytest tests/integration/ --cov --cov-fail-under=70
```

**What to cover (priority):**
1. API endpoint smoke tests (all services respond)
2. Supabase connection and RLS policy tests
3. LangGraph agent endpoint tests
4. FastMCP server endpoint tests
5. Docker health check verification

Remove ALL references to: VideoWriter, test_video, flask, pnpm, Vitest, Jest, pytest-flask, Codecov, 70%/80% thresholds.

- [ ] **Step 7: Verify coverage.md**

```bash
grep -ri "pnpm\|flask\|videoWriter\|VideoWriter\|test_video\|Vitest\|Jest\|pytest-flask\|Codecov" .claude/commands/coverage.md
```
Expected: zero results.

- [ ] **Step 8: Commit Agent B files**

```bash
git add .claude/commands/lint.md .claude/commands/fix-formatting.md .claude/commands/coverage.md
git commit -m "docs: rewrite lint, fix-formatting, coverage commands for salk-bloom stack

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: CI-focused full rewrites (Agent C)

**Files:**
- Modify: `.claude/commands/ci-debug.md` (full rewrite)
- Modify: `.claude/commands/run-ci-locally.md` (full rewrite)

**Context:** These files have zero salvageable structure. The current content describes 8 fictional CI jobs. Write from scratch based on the actual `pr-checks.yml` and `deploy.yml`.

- [ ] **Step 1: Read the design spec and actual CI workflows**

Read:
- `docs/superpowers/specs/2026-04-08-claude-commands-adaptation-design.md`
- `.github/workflows/pr-checks.yml` (the actual CI)
- `.github/workflows/deploy.yml`

- [ ] **Step 2: Rewrite ci-debug.md**

Delete all current content and write from scratch. Structure:

```markdown
---
name: CI Debug
description: Debug GitHub Actions CI failures for Bloom's Next.js + FastAPI + Docker stack
category: Troubleshooting
tags: [ci, github-actions, debugging, testing]
---

# CI Debug - GitHub Actions Pipeline

Guide for debugging CI failures in Bloom (Next.js + FastAPI/LangGraph + FastMCP + Docker + Supabase).

## CI Pipeline Overview

**PR Checks (`pr-checks.yml`)** — runs on PRs to `main`:

| Job | Purpose | Key commands |
|---|---|---|
| `build-and-audit` | npm audit, TypeScript check, Next.js build | `npm ci`, `npm audit`, `npx tsc --noEmit`, `npm run build` |
| `python-audit` | Python CVE scanning | `pip-audit -r langchain/requirements.txt`, `pip-audit -r bloommcp/requirements.txt` |
| `docker-build` | Build + Trivy scan Docker images | Build `bloom-web`, `langchain-agent`, `bloommcp`; Trivy CRITICAL gate |
| `compose-health-check` | Full stack integration tests | Start prod compose, wait for health, `uv run pytest tests/integration/` |
| `extract-pinned-images` | Extract pinned images for matrix | Grep `image:` from compose |
| `scan-pinned-images` | CVE scan each pinned image | Trivy per-image (matrix) |
| `pinned-images-summary` | Aggregate CVE report | Post combined table as PR comment |

**Deploy (`deploy.yml`)** — runs on release publish:
- Build + verify (same as PR checks)
- `deploy-staging` — stub (TODO)
- `deploy-production` — stub (TODO)

## Quick Diagnosis

[Step 1: identify failing job from `gh pr checks <number>`]
[Step 2: view logs with `gh run view <run-id> --log-failed`]
[Step 3: common failure patterns table]

## Job-by-Job Debugging

[One section per actual job, with local reproduction commands]
[For build-and-audit: npm ci, npm audit, cd web && npx tsc --noEmit, cd web && npm run build]
[For python-audit: uv pip install pip-audit && pip-audit -r langchain/requirements.txt]
[For docker-build: docker compose -f docker-compose.prod.yml build, trivy commands]
[For compose-health-check: docker compose -f docker-compose.prod.yml up -d, wait, uv run pytest tests/integration/]

## Docker-Specific Issues
[Layer caching, build context, multi-stage builds — keep from current but fix container names]

## Supabase/Database Issues
[Migration issues via make apply-migrations-local, RLS debugging, pg_isready on db-dev]

## MinIO Issues
[Container is supabase-minio, bucket init, presigned URLs]

## Environment Variable Issues
[Reference .env.ci generation from pr-checks.yml since no .env.example exists]
```

Use ONLY actual commands, container names, and job names from the reference data above.

- [ ] **Step 3: Verify ci-debug.md**

```bash
grep -ri "pnpm\|flask\|bloom-desktop\|bloom-minio\|bloom-flask\|FLASK_ENV\|videoWriter\|VideoWriter\|test_video\|Vitest\|Jest\|pytest-flask\|supabase start\|supabase db reset\|supabase db push\|Phase 1\|Phase 2\|Codecov\|lint-typescript\|lint-python\|test-unit" .claude/commands/ci-debug.md
```
Expected: zero results.

- [ ] **Step 4: Rewrite run-ci-locally.md**

Delete all current content and write from scratch. Structure:

```markdown
---
name: Run CI Locally
description: Run the same checks locally that GitHub Actions CI runs
category: Testing
tags: [ci, testing, linting, validation]
---

# Run CI Checks Locally

Run the same checks locally that run in GitHub Actions before pushing.

## What CI Actually Checks

The PR checks workflow runs these jobs:

1. **build-and-audit**: `npm ci && npm audit --audit-level=critical && cd web && npx tsc --noEmit && npm run build`
2. **python-audit**: `pip-audit -r langchain/requirements.txt && pip-audit -r bloommcp/requirements.txt`
3. **docker-build**: `docker compose -f docker-compose.prod.yml build` + Trivy scans
4. **compose-health-check**: Full stack up + `uv run pytest tests/integration/ -v --tb=short`

## Quick Check (~1 min, matches build-and-audit)

```bash
npm ci && npm audit --audit-level=critical && cd web && npx tsc --noEmit && npm run build
```

## Python Audit (~30s, matches python-audit)

```bash
uv run pip-audit -r langchain/requirements.txt
uv run pip-audit -r bloommcp/requirements.txt
```

## Docker Build (~5-10 min, matches docker-build)

```bash
docker compose -f docker-compose.prod.yml build
```

## Full Stack Integration Tests (~5 min, matches compose-health-check)

```bash
make dev-up
# Wait for services to be healthy
docker compose -f docker-compose.dev.yml ps
# Run integration tests
uv run pytest tests/integration/ -v --tb=short
# Teardown
make dev-down
```

## Optional: Local Python Linting (NOT in CI but recommended)

```bash
cd langchain && uv run black --check . && uv run ruff check . && cd ../bloommcp && uv run black --check . && uv run ruff check .
```

Note: Python linting is NOT enforced in CI. These are recommended for local development and run via pre-commit hooks.

## Related Commands
- `/lint` — linting checks
- `/validate-env` — environment setup validation
- `/ci-debug` — debug CI failures
- `/fix-formatting` — auto-fix formatting issues
```

- [ ] **Step 5: Verify run-ci-locally.md**

```bash
grep -ri "pnpm\|flask\|bloom-desktop\|Vitest\|Jest\|Phase 1\|Phase 2\|Codecov\|supabase start\|supabase db reset" .claude/commands/run-ci-locally.md
```
Expected: zero results.

- [ ] **Step 6: Commit Agent C files**

```bash
git add .claude/commands/ci-debug.md .claude/commands/run-ci-locally.md
git commit -m "docs: rewrite ci-debug and run-ci-locally from actual pr-checks.yml

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Infrastructure/setup files (Agent D)

**Files:**
- Modify: `.claude/commands/validate-env.md` (full rewrite)
- Modify: `.claude/commands/database-migration.md` (moderate fixes)
- Modify: `.claude/commands/pre-merge.md` (major rewrite)
- Modify: `.claude/commands/pr-description.md` (major rewrite)

- [ ] **Step 1: Read the design spec**

Read `docs/superpowers/specs/2026-04-08-claude-commands-adaptation-design.md` in full.

- [ ] **Step 2: Rewrite validate-env.md**

Delete all current content and write from scratch. The current file has an embedded shell script, fake output, and references to pnpm/flask/wrong ports throughout. Structure:

```markdown
---
name: Validate Environment
description: Check development environment is correctly set up for Bloom
category: Setup
tags: [environment, setup, validation]
---

# Validate Development Environment

Checks that your local environment is ready for Bloom development.

## Prerequisites

| Tool | Minimum version | Check command |
|---|---|---|
| Node.js | 18+ | `node -v` |
| npm | 9+ | `npm -v` |
| Python | 3.11+ | `python --version` |
| uv | latest | `uv --version` |
| Docker | 20+ | `docker --version` |
| Docker Compose | v2+ | `docker compose version` |

## Check 1: Node.js and npm

```bash
node -v    # Should be 18+
npm -v     # Should be 9+
npm ci     # Install dependencies from lockfile
```

## Check 2: Python and uv

```bash
python --version  # Should be 3.11+
uv --version
cd langchain && uv sync
cd ../bloommcp && uv sync
```

## Check 3: Docker Services

```bash
make dev-up                                    # Start the full dev stack
docker compose -f docker-compose.dev.yml ps    # All services should be healthy
```

Key services to verify:
- `bloom-web` — Next.js app
- `langchain-agent` — FastAPI + LangGraph
- `bloommcp` — FastMCP server
- `db-dev` — PostgreSQL
- `supabase-minio` — S3-compatible storage
- `kong` — API gateway
- `auth` — GoTrue auth

## Check 4: Database

```bash
# Verify database is accepting connections
docker exec db-dev pg_isready -U supabase_admin -h localhost

# Apply any unapplied migrations
make apply-migrations-local

# Generate TypeScript types
make gen-types
```

## Check 5: MinIO/Storage

```bash
# Verify MinIO is running
docker compose -f docker-compose.dev.yml ps supabase-minio

# Create bucket if needed
make create-bucket
```

## Check 6: Service Connectivity

```bash
# Web app (via Caddy in prod, direct in dev)
curl http://localhost:3000

# Supabase API (via Kong)
curl http://localhost:8000

# MinIO health
curl http://localhost:9000/minio/health/live
```

## Troubleshooting
[Common issues and fixes for each check]
```

- [ ] **Step 3: Verify validate-env.md**

```bash
grep -ri "pnpm\|flask\|bloom-minio\|bloom-flask\|FLASK_ENV\|supabase start\|supabase status\|supabase db reset\|port 5002\|localhost:5002" .claude/commands/validate-env.md
```
Expected: zero results.

- [ ] **Step 4: Fix database-migration.md**

Read `.claude/commands/database-migration.md`. Apply these targeted fixes:
- Replace `supabase db push` → `make apply-migrations-local`
- Replace `supabase db reset` → warn this is destructive; use `docker compose -f docker-compose.dev.yml down -v && make dev-up && make apply-migrations-local`
- Replace `supabase start` → `make dev-up`
- Replace `supabase migration new` → `make new-migration name=<name>`
- Replace `supabase status` → `docker compose -f docker-compose.dev.yml ps`
- Replace `supabase logs db` → `docker compose -f docker-compose.dev.yml logs db-dev`
- Replace port 5002 curl → remove or replace with appropriate service URL
- Keep the SQL patterns section (CREATE TABLE, ALTER, etc.) — those are database-agnostic
- Keep RLS policy guidance — that's correct for Supabase
- Keep the `psql` connection string examples — update to match actual connection params: `postgresql://supabase_admin:postgres@localhost:5432/postgres`

- [ ] **Step 5: Verify database-migration.md**

```bash
grep -ri "supabase start\|supabase db push\|supabase db reset\|supabase migration new\|flask\|port 5002\|localhost:5002" .claude/commands/database-migration.md
```
Expected: zero results (except `supabase gen types` which is valid).

- [ ] **Step 6: Rewrite pre-merge.md**

Read `.claude/commands/pre-merge.md`. This needs a major rewrite because it references fictional CI jobs and wrong test commands. Rewrite to match the actual CI pipeline:

**Phase 1: Local CI checks** (match actual `build-and-audit` job):
```bash
npm ci
npm audit --audit-level=critical
cd web && npx tsc --noEmit
cd web && npm run build
```

**Phase 2: Python audit** (match actual `python-audit` job):
```bash
uv run pip-audit -r langchain/requirements.txt
uv run pip-audit -r bloommcp/requirements.txt
```

**Phase 3: Docker builds** (match actual `docker-build` job):
```bash
docker compose -f docker-compose.prod.yml build
```

**Phase 4: Integration tests** (match actual `compose-health-check` job):
```bash
make dev-up
uv run pytest tests/integration/ -v --tb=short
make dev-down
```

**Phase 5: PR status check**:
```bash
gh pr checks <number>
```
Reference actual CI job names: `build-and-audit`, `python-audit`, `docker-build`, `compose-health-check`

**Phase 6: Review feedback** — check Copilot and human review comments

**Phase 7: Optional local Python linting** (NOT in CI):
```bash
cd langchain && uv run black --check . && uv run ruff check .
cd bloommcp && uv run black --check . && uv run ruff check .
```
Note clearly: "Python linting is recommended but NOT enforced in CI"

**Phase 8: Documentation and changelog**

**Phase 9: Final verification**

Remove all references to: `pnpm`, `flask`, fictional CI jobs, Vitest/Jest, coverage thresholds, Codecov.

- [ ] **Step 7: Verify pre-merge.md**

```bash
grep -ri "pnpm\|flask\|lint-typescript\|lint-python\|test-unit\|Vitest\|Jest\|Codecov\|Phase 1\|Phase 2\|Phase 3" .claude/commands/pre-merge.md
```
Expected: zero results.

- [ ] **Step 8: Rewrite pr-description.md**

Read `.claude/commands/pr-description.md`. Replace flask-specific examples and wrong test commands:

- Replace all `flask/app.py`, `flask/videoWriter.py`, `flask/tests/` paths → use `langchain/`, `bloommcp/`, `tests/integration/` paths
- Replace `cd flask && uv run pytest` → `uv run pytest tests/integration/ -v --tb=short`
- Replace `cd flask && uv run black/ruff/mypy` → `cd langchain && uv run black --check . && cd ../bloommcp && uv run black --check .`
- Replace `pnpm type-check` → `cd web && npx tsc --noEmit`
- Replace `pnpm lint` → `npm run lint`
- Replace `pnpm format` → `npm run format`
- Replace coverage targets "70% TS, 80% Python" → "No coverage thresholds currently enforced"
- Replace "Flask API Changes" section → "LangGraph Agent / FastMCP Changes" section
- Replace `opencv-python` dependency example → real dependency from `langchain/requirements.txt`
- Replace `nginx` references in Docker checklist → `Caddy`
- Replace example PR descriptions to reference real salk-bloom features (Supabase migration, Docker config, API endpoint)

- [ ] **Step 9: Verify pr-description.md**

```bash
grep -ri "pnpm\|flask\|videoWriter\|VideoWriter\|test_video\|VideoPlayer\|nginx\|opencv\|Codecov" .claude/commands/pr-description.md
```
Expected: zero results.

- [ ] **Step 10: Commit Agent D files**

```bash
git add .claude/commands/validate-env.md .claude/commands/database-migration.md .claude/commands/pre-merge.md .claude/commands/pr-description.md
git commit -m "docs: rewrite validate-env, pre-merge, pr-description; fix database-migration

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: New files (Agent E)

**Files:**
- Replace: `.claude/commands/review-pr.md`
- Create: `.claude/commands/new-feature.md`
- Create: `.claude/commands/copilot-review.md`
- Create: `.claude/skills/openspec-review/SKILL.md`

**Context:** Port these from bloom-desktop (`/c/repos/bloom-desktop/.claude/commands/` and `.claude/skills/`), applying the adaptations specified in the design spec.

- [ ] **Step 1: Read the design spec and bloom-desktop originals**

Read:
- `docs/superpowers/specs/2026-04-08-claude-commands-adaptation-design.md` (sections on new commands and skill)
- `/c/repos/bloom-desktop/.claude/commands/review-pr.md`
- `/c/repos/bloom-desktop/.claude/commands/new-feature.md`
- `/c/repos/bloom-desktop/.claude/commands/copilot-review.md`
- `/c/repos/bloom-desktop/.claude/skills/openspec-review/SKILL.md`

- [ ] **Step 2: Create copilot-review.md**

Copy from bloom-desktop and apply ONLY these changes:
- Replace `bloom-desktop` → `bloom` in all GraphQL queries and REST API URLs
- In GraphQL: `repository(owner: "Salk-Harnessing-Plants-Initiative", name: "bloom")`
- In REST: `repos/Salk-Harnessing-Plants-Initiative/bloom/pulls/`

No other changes needed — the command is repo-name-agnostic otherwise.

- [ ] **Step 3: Create new-feature.md**

Copy from bloom-desktop and apply these changes:
- Line 8: Change persona from "Electron + React + TypeScript + Python" to "Next.js + Supabase + FastAPI + Docker web platform for plant phenotyping"
- Step 1: **Strengthen branch guardrail**: Replace "ask the user what branch name to create" with: "If on `main`, STOP. Do not proceed to Step 2. Ask the user for a branch name, create and check out the branch, and confirm with `git branch --show-current` before continuing."
- Add before Step 2: "If `$ARGUMENTS` is empty, ask the user to describe the feature before proceeding."
- Keep all other steps (OpenSpec proposal, review, approval, TDD implementation) intact

- [ ] **Step 4: Replace review-pr.md**

Delete current content. Write the 5-subagent review adapted for salk-bloom, following the design spec section "3. `review-pr.md` (replace existing)" exactly. Key adaptations from bloom-desktop original:

**Header:**
```markdown
# PR Code Review — Subagent Team

You are a senior scientific programmer reviewing a pull request for bloom
(Next.js + Supabase + FastAPI/LangGraph + FastMCP + Docker), a plant phenotyping
web platform used in research environments. You value testing, code quality,
reproducibility, data integrity, and UX above all else.
```

**Step 1 GraphQL:** Change repo to `bloom`

**Subagent 1 (Code Quality):** Replace Electron architecture with:
```
Architecture:
- Next.js app (web/) — React 19, server/client components, Supabase SSR
- Supabase — auth (GoTrue), database (PostgreSQL), storage, realtime
- LangGraph agent (langchain/) — FastAPI + Uvicorn, LangChain tools, port 5002 internal
- FastMCP server (bloommcp/) — data analysis, pandas/numpy/scipy, port 8811 internal
- Docker Compose — 16 services orchestrated, Caddy reverse proxy with auto-HTTPS
- Types in packages/bloom-js, packages/bloom-fs, packages/bloom-nextjs-auth
```
Replace IPC/preload checks with: server vs client component boundaries, Supabase client usage, API route patterns, Docker networking.

**Subagent 2 (Testing):** Replace with actual test infrastructure per design spec. Include the "no unit tests" guardrail.

**Subagent 3 (Scientific Rigor):** Use the 8-item adversarial checklist from the design spec (RLS, atomic writes, genome versions, etc.)

**Subagent 4 (Security):** Replace Electron security with Supabase RLS, Docker security, Caddy TLS per design spec.

**Subagent 5 (Behavioural Correctness):** Adapt call chains to Next.js → Supabase → FastAPI flow per design spec.

**Step 3 (Synthesize):** Keep structure. Add the APPROVE guardrail from design spec. Add subagent failure handling: if any subagent fails, note in synthesis, don't approve.

Update the `gh pr review` fallback commands to reference `bloom` repo.

- [ ] **Step 5: Create .claude/skills/openspec-review/SKILL.md**

Create directory `.claude/skills/openspec-review/` and write `SKILL.md`. Port from bloom-desktop, applying ALL adaptations from the design spec section "Add 1 new skill":

**Header:** Update `description` to reference salk-bloom stack, not Electron.

**Subagent 1 (Spec Quality):** Keep as-is (OpenSpec rules are universal).

**Subagent 2 (Code & Architecture):** Replace Electron architecture with salk-bloom architecture (same as review-pr Subagent 1). Replace Prisma/IPC checks with Supabase/Docker/Caddy checks.

**Subagent 3 (GitHub Issues):** Change repo context. Keep logic.

**Subagent 4 (TDD & Testing):** Replace test infrastructure description with:
```
Testing infrastructure:
- pytest integration tests: tests/integration/, uv run pytest tests/integration/ -v --tb=short
- CI: compose-health-check job runs tests after full stack is healthy
- NO unit tests, NO frontend tests currently exist
- TDD for this project means: write integration tests before implementation
- Do NOT flag absence of unit tests as BLOCKING
- DO flag if tasks.md does not include integration tests for new behavior
```
Replace all `npm run test:unit`, `npm run test:python`, `npm run test:e2e` references. Replace Vitest/Playwright/IPC coverage references. Remove cross-platform CI concerns (Linux only).

**Subagent 5 (Scientific Rigor):** Rebuild with the 8-item checklist from the design spec. Remove all Basler camera, gain range, metadata.json references. Replace with genomic data integrity, Supabase RLS, visualization accuracy.

**Step 4 (Synthesize):** Add subagent failure handling per design spec.

- [ ] **Step 6: Verify all new files**

```bash
grep -ri "pnpm\|flask\|bloom-desktop\|Electron\|IPC\|preload\|Vitest\|Jest\|Prisma\|SQLite\|metadata\.json\|Basler\|acA2000\|gain range" .claude/commands/review-pr.md .claude/commands/new-feature.md .claude/commands/copilot-review.md .claude/skills/openspec-review/SKILL.md
```
Expected: zero results.

- [ ] **Step 7: Commit Agent E files**

```bash
git add .claude/commands/review-pr.md .claude/commands/new-feature.md .claude/commands/copilot-review.md .claude/skills/openspec-review/SKILL.md
git commit -m "docs: add review-pr subagent team, new-feature, copilot-review, openspec-review

Ported from bloom-desktop and adapted for salk-bloom's Next.js + Supabase +
FastAPI/LangGraph + Docker stack. Includes 5-subagent PR review, OpenSpec
proposal review skill, and new-feature workflow with strengthened guardrails.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Final Verification

**Files:** All files from Tasks 1-5.

- [ ] **Step 1: Run full prohibited-string grep**

Run every check from the design spec's verification plan:

```bash
grep -ri "pnpm" .claude/commands/ .claude/skills/
grep -ri "flask" .claude/commands/ .claude/skills/
grep -ri "test_video\|videoWriter\|VideoWriter\|VideoPlayer" .claude/commands/ .claude/skills/
grep -ri "FLASK_ENV\|flask-app\|bloom-flask" .claude/commands/ .claude/skills/
grep -ri "bloom-desktop" .claude/commands/ .claude/skills/
grep -ri "vitest\|jest" .claude/commands/ .claude/skills/
grep -ri "pytest-flask" .claude/commands/ .claude/skills/
grep -ri "supabase start\|supabase db reset\|supabase db push" .claude/commands/ .claude/skills/
grep -ri "bloom-minio" .claude/commands/ .claude/skills/
grep -ri "port 5002\|localhost:5002" .claude/commands/ .claude/skills/
grep -ri "Phase 1\|Phase 2\|Phase 3" .claude/commands/ .claude/skills/
grep -ri "Codecov" .claude/commands/ .claude/skills/
```

ALL must return zero results. If any match, fix the offending file and re-commit.

- [ ] **Step 2: Verify npm scripts exist**

Extract every `npm run <script>` referenced in command files and verify each exists in root `package.json`:

```bash
grep -roh "npm run [a-z:_-]*" .claude/commands/ .claude/skills/ | sort -u
```

Valid scripts: `lint`, `lint:fix`, `format`, `format:check`, `test`, `test:coverage`. Any other `npm run` reference is a bug.

- [ ] **Step 3: Verify container names exist**

Extract container/service names referenced and verify against compose files:

```bash
grep -roh "container [a-z-]*\|docker.*exec [a-z-]*\|docker.*logs [a-z-]*" .claude/commands/ .claude/skills/ | sort -u
```

Valid service names: `caddy`, `bloom-web`, `langchain-agent`, `bloommcp`, `kong`, `auth`, `rest`, `realtime`, `supabase-minio`, `minio-init`, `storage`, `db-prod`, `db-dev`, `supavisor`, `studio`, `imgproxy`, `meta`, `swagger-ui`.

- [ ] **Step 4: Verify file paths exist**

Spot-check that key file paths referenced in commands actually exist:

```bash
ls langchain/requirements.txt bloommcp/requirements.txt tests/integration/ supabase/migrations/ .pre-commit-config.yaml .prettierrc.json .eslintrc.json turbo.json web/package.json
```

- [ ] **Step 5: Verify CI job names**

Extract CI job names referenced in commands and verify against `pr-checks.yml`:

```bash
grep -roh "build-and-audit\|python-audit\|docker-build\|compose-health-check\|extract-pinned-images\|scan-pinned-images\|pinned-images-summary" .claude/commands/ | sort -u
```

Cross-reference with actual job names in `.github/workflows/pr-checks.yml`.

- [ ] **Step 6: Final commit if any fixes were needed**

If Steps 1-5 found issues, fix them and commit:

```bash
git add .claude/commands/ .claude/skills/
git commit -m "fix: address verification findings in adapted commands

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```
