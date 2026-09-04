---
name: Pre-Merge Checks
description: Comprehensive pre-merge workflow matching actual CI pipeline
category: Git Workflow
tags: [pr, merge, ci, review, checklist]
---

# Pre-Merge Checks

Comprehensive checklist before merging a PR. Phases match the actual CI jobs in `pr-checks.yml`.

`pr-checks.yml` runs on every push to an open PR (via the default `pull_request` `synchronize` activity type), but it does NOT run on pushes to feature branches with no PR open yet. Run these checks locally before opening a PR — and rely on the `uv-lock-check` pre-commit hook to catch lockfile drift on every commit.

## Preflight

```bash
command -v uv >/dev/null || { echo "Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
command -v docker >/dev/null || { echo "Install Docker Desktop"; exit 1; }
command -v gh >/dev/null || { echo "Install gh: https://cli.github.com/"; exit 1; }
```

## Step 1: Build & Audit (matches `build-and-audit` job)

```bash
npm ci
npm audit --audit-level=critical
cd web && npx tsc --noEmit && npm run build && cd ..
```

## Step 2: Python Audit (matches `python-audit` job)

Audits each service's full transitive dependency tree via its lockfile. A temp file is used for local runs because `/dev/stdin` is not portable on Windows/MSYS; CI runs `| uvx pip-audit@2.10.0 -r /dev/stdin` directly because it's on Linux. The `@2.10.0` pin is required by the `python-dependency-management` spec ("CI security-scanning tools SHALL be pinned to specific versions") so local runs don't drift from CI behavior.

```bash
# Subshell + EXIT trap so /tmp/reqs.txt is cleaned up on both success and
# failure paths (the earlier trailing `rm -f` only ran on success and leaked
# the temp file when `exit 1` fired mid-loop).
(
  trap "rm -f /tmp/reqs.txt" EXIT
  for svc in langchain bloommcp services/video-worker bloomcli; do
    echo "=== Auditing $svc ==="
    (cd "$svc" && uv export --frozen --no-hashes > /tmp/reqs.txt && uvx pip-audit@2.10.0 -r /tmp/reqs.txt) || exit 1
  done
)
```

`python-audit`'s per-PR CI run excludes bloommcp's `integration`-marked tests (`bloommcp/tests/test_oracle.py`'s full-fixture `statsmodels`/`umap` heritability/UMAP oracles — known to intermittently stall in CI containers, #454). Run them here instead so numeric drift is still caught before merge:

```bash
cd bloommcp && uv run --extra test pytest tests/ -m integration -v --tb=short
```

## Step 3: Docker Builds (matches `docker-build` job)

Build each image individually. `docker compose build` against `docker-compose.prod.yml` needs a populated `.env.dev` to resolve volume paths, which is not always available locally — the individual `docker build` commands don't.

```bash
docker build -f web/Dockerfile.bloom-web.prod \
  --build-arg NEXT_PUBLIC_SUPABASE_URL=http://localhost:8000 \
  --build-arg NEXT_PUBLIC_SUPABASE_ANON_KEY=placeholder \
  --build-arg NEXT_PUBLIC_SUPABASE_COOKIE_NAME=sb-localhost-auth-token \
  -t bloom-web:test .
docker build -f langchain/Dockerfile -t langchain:test ./langchain
docker build -f bloommcp/Dockerfile -t bloommcp:test ./bloommcp
docker build -f bloomcli/Dockerfile -t bloomcli:test ./bloomcli
```

Smoke-test that each Python image's non-root user can import its key packages (catches venv ownership / PATH issues before CI):

```bash
docker run --rm --entrypoint python langchain:test -c "import langchain; import langgraph; import fastapi"
docker run --rm --entrypoint python bloommcp:test -c "import fastmcp; import statsmodels; import umap"
```

`bloomcli`'s image has `ENTRYPOINT ["bloomctl"]` (a CLI, not a service) — smoke-test it
directly rather than overriding the entrypoint to `python`:

```bash
docker run --rm bloomcli:test --version
```

## Step 4: Integration Tests (matches `compose-health-check` job)

Requires a populated `.env.dev` — if missing, run `/validate-env` first.

```bash
make prod-up
docker compose -f docker-compose.prod.yml ps
uv run --extra test pytest tests/integration/ -v --tb=short
make prod-down
```

### Step 4b: bloommcp live-persistence smoke (bloommcp PRs only — matches the `dev-stack-smoke` job)

Drives a workflow end-to-end through the **real** `SupabaseReader`/`SupabaseResultStore`
against the dev stack and asserts the committed run is a v3 manifest whose
`output_sha256` matches the bytes actually stored (issue #326). Same `make bloommcp-smoke`
target CI runs, so local and CI never drift. `make bloommcp-plot-smoke` similarly calls a
real plotting tool through the container's actual MCP transport (issue #472) — CI already
runs both; do the same locally.

```bash
make dev-up && make migrate-local && make check && make bloommcp-smoke && make bloommcp-plot-smoke
make dev-down
```

### Step 4c: bloommcp granular tool smoke — full `live_smoke` set (bloommcp PRs only, #483)

Runs every `live_smoke`-marked test under `bloommcp/tests/smoke/` — the CI-safe subset
`dev-stack-smoke` already runs, **plus** the `live_smoke_slow` cases CI skips
(mahalanobis/gmm on cylinder, correlation-matrix / histograms / boxplots on cylinder).
The per-trait MixedLM heritability and variance-decomposition plot tools used to be in
this list; bloom#462 retired both into `heritability_analysis`, whose smoke runs in the
CI-safe subset — it reads the DB-seeded smoke experiments rather than the 846-trait
cylinder CSV those tools loaded, so the cost that made them slow no longer applies. Requires `BLOOMMCP_PORT` / `BLOOMMCP_API_KEY`
from `.env.dev` (same as the Makefile targets above).

```bash
make dev-up && make migrate-local && make check
cd bloommcp && \
  BLOOMMCP_PORT=$(sed -n 's/^BLOOMMCP_PORT=//p' ../.env.dev | head -n1 | tr -d '\r') \
  BLOOMMCP_API_KEY=$(sed -n 's/^BLOOMMCP_API_KEY=//p' ../.env.dev | head -n1 | tr -d '\r') \
  uv run --extra test pytest tests/smoke/ -m live_smoke -v --tb=short
cd .. && make dev-down
```

## Step 5: PR Status on GitHub

```bash
unset GITHUB_TOKEN
gh pr checks <PR_NUMBER>
```

Verify these jobs pass:
- `build-and-audit`
- `python-audit`
- `docker-build`
- `compose-health-check`
- `dev-stack-smoke` (includes the bloommcp live-persistence smoke)

## Step 6: Review Feedback

```bash
unset GITHUB_TOKEN
gh pr view <PR_NUMBER> --comments
```

Also run `/copilot-review` to fetch inline Copilot comments. Address all review comments before merging.

## Step 7: Optional Local Python Linting

Python linting is recommended but **NOT enforced in CI**:

```bash
cd langchain && uv run black --check . && uv run ruff check .
cd ../bloommcp && uv run black --check . && uv run ruff check .
```

## Step 8: Documentation & Changelog

- [ ] README updated if new features/commands added
- [ ] Breaking changes documented
- [ ] CHANGELOG.md updated (use `/changelog`)
- [ ] Environment variables documented if changed
- [ ] Database migrations documented if schema changed

## Step 9: Final Verification

This repo is staging-first — most PRs target `staging`, except for consolidation rollups (e.g. `staging → main`) which target `main`. Detect the actual base branch from the PR rather than hard-coding it, so the rebase check matches what GitHub will compare against:

```bash
unset GITHUB_TOKEN
BASE=$(gh pr view <PR_NUMBER> --json baseRefName -q .baseRefName)
git fetch origin "$BASE"
git merge-base --is-ancestor "origin/$BASE" HEAD && echo "Up to date with origin/$BASE" || echo "Needs rebase against origin/$BASE"

gh pr checks <PR_NUMBER>
```

## Quick Pre-Merge (Minimum)

For small changes, the minimum checks:

```bash
# TypeScript (if web/ touched)
cd web && npx tsc --noEmit && npm run build && cd ..

# Python audit (if any pyproject.toml/uv.lock touched). pip-audit pinned to match
# CI per the python-dependency-management spec.
# Subshell + EXIT trap so /tmp/reqs.txt is cleaned up on success AND failure.
(
  trap "rm -f /tmp/reqs.txt" EXIT
  for svc in langchain bloommcp services/video-worker bloomcli; do
    (cd "$svc" && uv export --frozen --no-hashes > /tmp/reqs.txt && uvx pip-audit@2.10.0 -r /tmp/reqs.txt) || exit 1
  done
)

# bloommcp integration-marked oracle tests (if bloommcp/tests/test_oracle.py or
# delegated statsmodels/umap code touched) — per-PR CI excludes these (#454).
cd bloommcp && uv run --extra test pytest tests/ -m integration -v --tb=short && cd ..
```

## Pre-Merge Checklist

- [ ] All CI jobs pass (`gh pr checks`)
- [ ] bloommcp integration-marked oracle tests run if `bloommcp/tests/test_oracle.py` or delegated statsmodels/umap code touched (excluded from per-PR CI, see Step 2)
- [ ] Code reviewed and approved
- [ ] Review comments addressed
- [ ] Branch up to date with the PR's base branch (`gh pr view <PR_NUMBER> --json baseRefName` — usually `staging`)
- [ ] No merge conflicts
- [ ] Documentation updated (if applicable)
- [ ] CHANGELOG updated (if applicable)
- [ ] Database migrations tested (if applicable)

## Related Commands

- `/run-ci-locally` — run full CI suite locally
- `/validate-env` — verify environment setup
- `/coverage` — check test coverage
- `/lint` — run linting
- `/fix-formatting` — auto-fix formatting
- `/ci-debug` — debug CI failures
- `/review-pr` — review a PR
- `/docs-review` — review documentation
- `/changelog` — update changelog
- `/database-migration` — manage migrations