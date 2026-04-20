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

Audits each service's full transitive dependency tree via its lockfile. A temp file is used for local runs because `/dev/stdin` is not portable on Windows/MSYS; CI runs `| uvx pip-audit -r /dev/stdin` directly because it's on Linux.

```bash
# Subshell + EXIT trap so /tmp/reqs.txt is cleaned up on both success and
# failure paths (the earlier trailing `rm -f` only ran on success and leaked
# the temp file when `exit 1` fired mid-loop).
(
  trap "rm -f /tmp/reqs.txt" EXIT
  for svc in langchain bloommcp services/video-worker; do
    echo "=== Auditing $svc ==="
    (cd "$svc" && uv export --frozen --no-hashes > /tmp/reqs.txt && uvx pip-audit -r /tmp/reqs.txt) || exit 1
  done
)
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
```

Smoke-test that each Python image's non-root user can import its key packages (catches venv ownership / PATH issues before CI):

```bash
docker run --rm --entrypoint python langchain:test -c "import langchain; import langgraph; import fastapi"
docker run --rm --entrypoint python bloommcp:test -c "import fastmcp; import statsmodels; import umap"
```

## Step 4: Integration Tests (matches `compose-health-check` job)

Requires a populated `.env.dev` — if missing, run `/validate-env` first.

```bash
make prod-up
docker compose -f docker-compose.prod.yml ps
uv run --with pytest pytest tests/integration/ -v --tb=short
make prod-down
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

```bash
git fetch origin main
git merge-base --is-ancestor origin/main HEAD && echo "Up to date" || echo "Needs rebase"

unset GITHUB_TOKEN
gh pr checks <PR_NUMBER>
```

## Quick Pre-Merge (Minimum)

For small changes, the minimum checks:

```bash
# TypeScript (if web/ touched)
cd web && npx tsc --noEmit && npm run build && cd ..

# Python audit (if any pyproject.toml/uv.lock touched)
# Subshell + EXIT trap so /tmp/reqs.txt is cleaned up on success AND failure.
(
  trap "rm -f /tmp/reqs.txt" EXIT
  for svc in langchain bloommcp services/video-worker; do
    (cd "$svc" && uv export --frozen --no-hashes > /tmp/reqs.txt && uvx pip-audit -r /tmp/reqs.txt) || exit 1
  done
)
```

## Pre-Merge Checklist

- [ ] All CI jobs pass (`gh pr checks`)
- [ ] Code reviewed and approved
- [ ] Review comments addressed
- [ ] Branch up to date with main
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