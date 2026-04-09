---
name: Pre-Merge Checks
description: Comprehensive pre-merge workflow matching actual CI pipeline
category: Git Workflow
tags: [pr, merge, ci, review, checklist]
---

# Pre-Merge Checks

Comprehensive checklist before merging a PR. Phases match the actual CI jobs in `pr-checks.yml`.

## Step 1: Build & Audit (matches `build-and-audit` job)

```bash
npm ci
npm audit --audit-level=critical
cd web && npx tsc --noEmit && npm run build
```

## Step 2: Python Audit (matches `python-audit` job)

```bash
uv run --with pip-audit pip-audit -r langchain/requirements.txt
uv run --with pip-audit pip-audit -r bloommcp/requirements.txt
```

## Step 3: Docker Builds (matches `docker-build` job)

```bash
docker compose -f docker-compose.prod.yml build
```

## Step 4: Integration Tests (matches `compose-health-check` job)

```bash
make dev-up
# Wait for services to be healthy
docker compose -f docker-compose.dev.yml ps
uv run pytest tests/integration/ -v --tb=short
make dev-down
```

## Step 5: PR Status on GitHub

```bash
gh pr checks <PR_NUMBER>
```

Verify these jobs pass:
- `build-and-audit`
- `python-audit`
- `docker-build`
- `compose-health-check`

## Step 6: Review Feedback

```bash
# View PR comments
gh pr view <PR_NUMBER> --comments

# Check for Copilot review comments
/copilot-review
```

Address all review comments before merging.

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
# Ensure branch is up to date with main
git fetch origin main
git merge-base --is-ancestor origin/main HEAD && echo "Up to date" || echo "Needs rebase"

# Final CI check
gh pr checks <PR_NUMBER>
```

## Quick Pre-Merge (Minimum)

For small changes, the minimum checks:

```bash
# TypeScript
cd web && npx tsc --noEmit && npm run build && cd ..

# Integration tests (if applicable)
uv run pytest tests/integration/ -v --tb=short
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