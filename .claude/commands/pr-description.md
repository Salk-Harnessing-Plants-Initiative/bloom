---
name: PR Description Template
description: Template for creating comprehensive pull request descriptions
category: Git Workflow
tags: [pr, github, template]
---

# PR Description Template

Standardized template for Bloom pull request descriptions.

## Template

```markdown
## Summary

[1-3 sentences describing what this PR does and why]

## Changes

- [Bullet list of specific changes]

## Testing

- [ ] Integration tests pass: `uv run --extra test pytest tests/integration/ -v --tb=short`
- [ ] TypeScript type check passes: `cd web && npx tsc --noEmit`
- [ ] Next.js build succeeds: `cd web && npm run build`
- [ ] Docker images build: `docker compose -f docker-compose.prod.yml build`

## Type Checking & Linting

- [ ] `npm audit --audit-level=critical` — no critical CVEs
- [ ] `cd web && npx tsc --noEmit` — no type errors
- [ ] `npm run lint` — no ESLint errors (optional, not in CI)
- [ ] Python linting clean (optional, not in CI):
  - `cd langchain && uv run black --check . && uv run ruff check .`
  - `cd bloommcp && uv run black --check . && uv run ruff check .`

## Breaking Changes

[None / Describe any breaking changes and migration path]

## Related Issues

Closes #<issue_number>
```

> **Use real closing keywords — one per issue.** Issues only auto-close when the
> PR body contains a keyword (`Closes`/`Fixes`/`Resolves`, etc.) immediately
> before each `#N` — a bare `(#305)` is just a mention and leaves the issue
> open. Because this repo's flow merges feature PRs into `staging` (not the
> default branch), GitHub's native auto-close doesn't fire; the
> `auto-close-issues-on-staging` workflow replicates it, but it reads the **same
> literal keywords**, so the convention is what makes both work. Notes:
> - **One keyword per issue:** `Closes #1, closes #2` (not `Closes #1, #2`,
>   which only closes #1).
> - **Keywords are literal:** avoid `does not close #5`, and don't put
>   `Closes #N` inside code blocks or future-work checklists — they'll still
>   trigger a close.
> - **Needs a space:** `Closes: #5` works, `Closes:#5` does not.

## Package-Specific Checklists

### LangGraph Agent / FastMCP Changes

If the PR modifies `langchain/` or `bloommcp/`:

- [ ] FastAPI routes follow existing patterns
- [ ] Python dependencies updated in `pyproject.toml` with `uv add` and `uv lock`
- [ ] `uv export | uvx pip-audit` passes on updated requirements
- [ ] Integration tests cover new endpoints
- [ ] Error responses return appropriate HTTP status codes

### Next.js Frontend Changes

If the PR modifies `web/`:

- [ ] Server vs client components correctly separated
- [ ] Supabase client calls use SSR pattern where appropriate
- [ ] `npx tsc --noEmit` passes
- [ ] `npm run build` succeeds
- [ ] Responsive design verified
- [ ] Accessibility: ARIA labels, keyboard navigation

### Database/Supabase Changes

If the PR includes migrations in `supabase/migrations/`:

- [ ] Migration tested locally: `make migrate-local`
- [ ] RLS policies added for new tables
- [ ] TypeScript types regenerated: `make gen-types`
- [ ] Rollback SQL documented (if destructive)
- [ ] Indexes added for queried columns

### Docker/Infrastructure Changes

If the PR modifies Dockerfiles or compose files:

- [ ] Both `docker-compose.dev.yml` and `docker-compose.prod.yml` updated consistently
- [ ] Caddy config updated if routing changes
- [ ] Health checks configured for new services
- [ ] Security: `no-new-privileges`, `cap_drop: ALL`, `read_only` where applicable
- [ ] Environment variables documented

## GitHub CLI Commands

```bash
# Create PR
gh pr create --title "feat: description" --body "$(cat <<'EOF'
## Summary
...
EOF
)"

# View PR
gh pr view <number>

# View PR diff
gh pr diff <number>

# Check CI status
gh pr checks <number>

# Edit PR description
gh pr edit <number> --body "..."
```

## Example PRs

### Feature PR

```markdown
## Summary

Add organism management API endpoints to the LangGraph agent, allowing users to create, read, update, and delete organisms via the web interface.

## Changes

- Add CRUD endpoints in `langchain/routes/organisms.py`
- Add Supabase migration for organisms table with RLS policies
- Add organism list component in `web/app/organisms/page.tsx`
- Add integration test for organism endpoints

## Testing

- [x] Integration tests pass
- [x] TypeScript type check passes
- [x] Docker images build
- [x] Manually tested organism CRUD in dev environment

## Breaking Changes

None
```

### Bug Fix PR

```markdown
## Summary

Fix race condition in Supabase realtime subscription causing stale scan data to display after concurrent uploads.

## Changes

- Add debounce to realtime subscription handler in `web/app/scans/use-scan-subscription.ts`
- Add cleanup on component unmount to prevent memory leak

## Testing

- [x] Integration tests pass
- [x] TypeScript type check passes
- [x] Manually verified with concurrent uploads

## Breaking Changes

None

Closes #87
```

## Related Commands

- `/pre-merge` — comprehensive pre-merge checklist
- `/review-pr` — review a PR
- `/run-ci-locally` — run CI checks before creating PR
- `/changelog` — update changelog