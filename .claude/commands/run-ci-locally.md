---
name: Run CI Locally
description: Run the same checks locally that GitHub Actions CI runs
category: Testing
tags: [ci, testing, linting, validation]
---

# Run CI Checks Locally

Run the same checks locally that run in GitHub Actions before pushing.

## What CI Actually Checks

The `pr-checks.yml` workflow runs these jobs:

| Job | What It Does | Local Equivalent |
|---|---|---|
| `build-and-audit` | npm audit, TypeScript check, Next.js build | `npm ci && npm audit && cd web && npx tsc --noEmit && npm run build` |
| `python-audit` | Python CVE scanning | `uv tool run pip-audit -r langchain/requirements.txt` |
| `docker-build` | Build Docker images + Trivy scan | `docker compose -f docker-compose.prod.yml build` |
| `compose-health-check` | Full stack integration tests | `make prod-up && uv run --with pytest pytest tests/integration/` |

## Quick Check (~1 min)

Matches the `build-and-audit` job:

```bash
npm ci
npm audit --audit-level=critical
cd web && npx tsc --noEmit && npm run build
```

## Python Audit (~30s)

Matches the `python-audit` job:

```bash
uv tool run pip-audit -r langchain/requirements.txt
uv tool run pip-audit -r bloommcp/requirements.txt
```

## Docker Build (~5-10 min)

Matches the `docker-build` job:

```bash
docker compose -f docker-compose.prod.yml build
```

To scan for vulnerabilities locally (requires Trivy installed):

```bash
trivy image bloom-web:latest --severity CRITICAL,HIGH
trivy image langchain-agent:latest --severity CRITICAL,HIGH
trivy image bloommcp:latest --severity CRITICAL,HIGH
```

## Full Stack Integration Tests (~5 min)

Matches the `compose-health-check` job (uses prod stack, not dev):

```bash
# Start the prod stack (CI uses prod compose with Caddy routing)
make prod-up

# Wait for services to be healthy
docker compose -f docker-compose.prod.yml ps

# Run integration tests
uv run --with pytest pytest tests/integration/ -v --tb=short

# Teardown
make prod-down
```

## Optional: Local Python Linting (NOT in CI)

Python linting is recommended locally but **not enforced in CI**:

```bash
# Check formatting
cd langchain && uv run black --check . && uv run ruff check .
cd ../bloommcp && uv run black --check . && uv run ruff check .

# Auto-fix formatting
cd langchain && uv run black . && uv run ruff check --fix .
cd ../bloommcp && uv run black . && uv run ruff check --fix .
```

## Optional: ESLint (NOT separately in CI)

CI checks types via `tsc` and builds via `npm run build`, but doesn't run ESLint separately. You can run it locally:

```bash
npm run lint          # Check
npm run lint:fix      # Auto-fix
npm run format:check  # Prettier check
npm run format        # Prettier fix
```

## Run Everything

```bash
# Phase 1: build-and-audit (run each line separately)
npm ci
npm audit --audit-level=critical
cd web && npx tsc --noEmit && npm run build && cd ..

# Phase 2: python-audit
uv tool run pip-audit -r langchain/requirements.txt
uv tool run pip-audit -r bloommcp/requirements.txt

# Phase 3: docker-build
docker compose -f docker-compose.prod.yml build

# Phase 4: integration tests (prod stack, matching CI)
make prod-up
docker compose -f docker-compose.prod.yml ps  # verify all healthy
uv run --with pytest pytest tests/integration/ -v --tb=short
make prod-down
```

## Troubleshooting

### "npm audit" finds vulnerabilities

```bash
# See details
npm audit

# Fix automatically
npm audit fix

# If fix requires major version bump
npm audit fix --force  # Review changes carefully
```

### "tsc" type errors

```bash
# See all errors
cd web && npx tsc --noEmit 2>&1 | head -50
```

### "pip-audit" finds vulnerabilities

```bash
# Update the vulnerable package
cd langchain  # or bloommcp
uv pip install --upgrade <package-name>
uv pip freeze > requirements.txt
```

### Docker build fails

```bash
# Build without cache
docker compose -f docker-compose.prod.yml build --no-cache

# Build specific service
docker compose -f docker-compose.prod.yml build bloom-web
```

### Integration tests fail

```bash
# Check service health first
docker compose -f docker-compose.prod.yml ps

# Check specific service logs
docker compose -f docker-compose.prod.yml logs langchain-agent --tail=50
docker compose -f docker-compose.prod.yml logs db-prod --tail=50

# Run specific test
uv run --with pytest pytest tests/integration/test_smoke.py -v --tb=long
```

## When to Run

- **Before every `git push`:** Quick check (Phase 1)
- **Before creating a PR:** Quick check + Python audit
- **Before merge:** Full suite including Docker and integration tests
- **After changing Dockerfiles:** Docker build + integration tests
- **After updating dependencies:** npm audit + pip-audit + integration tests

## Related Commands

- `/lint` — linting checks (TypeScript + Python)
- `/coverage` — test coverage analysis
- `/validate-env` — validate development environment
- `/fix-formatting` — auto-fix formatting issues
- `/ci-debug` — debug CI failures