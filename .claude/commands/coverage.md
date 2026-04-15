---
name: Test Coverage Analysis
description: Run test coverage analysis for integration tests
category: Testing
tags: [test, coverage, quality]
---

# Test Coverage Analysis

Run integration tests with coverage analysis to identify untested code.

## Current Testing State

Bloom currently has **integration tests only** — no frontend unit tests exist yet.

- **Test files:** `tests/integration/test_api_endpoints.py`, `test_smoke.py`, `test_supabase.py`
- **Runner:** pytest via uv
- **CI job:** `compose-health-check` runs tests after full Docker stack is healthy
- **No coverage thresholds enforced** in CI

## Quick Commands

```bash
# Run integration tests (requires Docker stack running)
uv run --with pytest pytest tests/integration/ -v --tb=short

# Run with coverage report
uv run --with pytest-cov pytest tests/integration/ --cov --cov-report=term-missing -v

# Run with HTML coverage report
uv run --with pytest-cov pytest tests/integration/ --cov --cov-report=term-missing --cov-report=html -v

# Run with coverage threshold (for future enforcement)
uv run --with pytest-cov pytest tests/integration/ --cov --cov-fail-under=70 -v

# Run a specific test file
uv run --with pytest pytest tests/integration/test_api_endpoints.py -v --tb=short

# Run a specific test
uv run --with pytest pytest tests/integration/test_api_endpoints.py::test_health_check -v
```

## Prerequisites

Integration tests require the full Docker stack running:

```bash
# Start the prod stack (integration tests require Caddy routing)
make prod-up

# Verify services are healthy
docker compose -f docker-compose.prod.yml ps

# Run tests
uv run --with pytest pytest tests/integration/ -v --tb=short

# Stop the stack when done
make prod-down
```

## Viewing Coverage Reports

```bash
# Terminal report (after running with --cov)
# Shows per-file coverage with missing line numbers

# HTML report (after running with --cov-report=html)
start htmlcov/index.html   # Windows
open htmlcov/index.html    # macOS
xdg-open htmlcov/index.html  # Linux
```

## What to Cover (Priority Order)

1. **API endpoint smoke tests** — all services respond to health checks
2. **Supabase connection tests** — database connectivity, RLS policies
3. **LangGraph agent endpoints** — FastAPI routes in `langchain/`
4. **FastMCP server endpoints** — data analysis endpoints in `bloommcp/`
5. **Docker health check verification** — all 16 services start and report healthy
6. **MinIO/storage operations** — bucket creation, presigned URLs

## CI Context

In CI (`compose-health-check` job):
1. Docker images are built by the `docker-build` job
2. Full prod compose stack starts with `.env.ci`
3. Waits up to 180 seconds for all services to be healthy
4. Runs `uv run --with pytest pytest tests/integration/ -v --tb=short`
5. Stack is torn down with `docker compose down -v`

## Adding New Tests

New integration tests go in `tests/integration/`. Follow existing patterns:

```python
# tests/integration/test_new_feature.py
import pytest
import requests

def test_new_endpoint_responds():
    """Verify the new endpoint returns a valid response."""
    response = requests.get("http://localhost:8000/rest/v1/new_table")
    assert response.status_code == 200
```

## Related Commands

- `/run-ci-locally` — run the full CI pipeline locally
- `/validate-env` — verify development environment setup
- `/lint` — run linting checks