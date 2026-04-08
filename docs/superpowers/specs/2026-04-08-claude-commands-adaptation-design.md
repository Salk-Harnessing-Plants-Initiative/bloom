# Claude Commands Adaptation Design

## Purpose

Adapt all `.claude/commands/` files in salk-bloom to accurately reflect the actual project stack, and port high-value commands and skills from bloom-desktop.

## Background

The existing 15 commands were templated with references to the wrong tools and directories. They reference `pnpm`, `flask/`, Vitest/Jest unit tests, and hypothetical CI jobs that don't exist. The actual stack is:

- **Frontend:** Next.js 16 + React 19 + Tailwind + MUI v7 (in `web/`)
- **Backend:** FastAPI + LangGraph agent (`langchain/`), FastMCP server (`bloommcp/`)
- **Database:** Self-hosted Supabase via Docker Compose (PostgreSQL 15, migrations in `supabase/`)
- **Storage:** MinIO (S3-compatible), container name `supabase-minio`
- **Reverse proxy:** Caddy (auto-HTTPS)
- **Package manager:** npm workspaces + Turborepo (`pnpm-lock.yaml` exists but is unused; CI uses `npm ci`)
- **Python tooling:** uv
- **Testing:** Integration tests only (`tests/integration/`, pytest via `uv run pytest tests/integration/ -v --tb=short`)
- **CI (`pr-checks.yml`):** 7+ jobs: `build-and-audit`, `python-audit`, `docker-build`, `compose-health-check`, `extract-pinned-images`, `scan-pinned-images` (matrix), `pinned-images-summary`
- **CI (`deploy.yml`):** Build + verify pipeline; `deploy-staging` and `deploy-production` are stubs (TODO)
- **Docker:** `docker-compose.prod.yml` (~16 containers), `docker-compose.dev.yml`
- **GitHub org/repo:** `Salk-Harnessing-Plants-Initiative/bloom`

### Critical facts discovered during review

1. **`npm run type-check` does not exist.** No `type-check` script in any `package.json` or `turbo.json`. CI type-checks via `cd web && npx tsc --noEmit`.
2. **`npm run test` is effectively a no-op.** Turbo task exists but no workspace defines a `test` script. Integration tests run via `uv run pytest`, not npm.
3. **Python linting (Black/Ruff/mypy) is NOT in CI.** CI only runs `pip-audit` for CVE scanning. Commands must not claim Python lint failures block CI.
4. **CI does NOT run `npm run lint`.** CI runs `npx tsc --noEmit` and `npm run build` for type/build checking, plus `npm audit` for CVE scanning.
5. **Pre-commit config is broken.** `.pre-commit-config.yaml` targets `^flask/` which doesn't exist. Python hooks silently match no files.
6. **This project uses self-hosted Supabase via Docker Compose**, not the Supabase CLI local dev stack. Commands like `supabase start`, `supabase status`, `supabase db reset` are NOT applicable.
7. **CI uses `docker compose` (v2, no hyphen)**, not `docker-compose` (v1).

## Canonical Glossary

All adapted commands MUST use these exact terms consistently:

| Concept | Canonical name | Path/container |
|---|---|---|
| LangGraph agent service | "the LangGraph agent" | `langchain/`, container `langchain-agent` |
| FastMCP data analysis server | "the FastMCP server" | `bloommcp/`, container `bloommcp` |
| Both Python services collectively | "the Python services (`langchain/`, `bloommcp/`)" | — |
| Next.js web app | "the web app" | `web/`, container `bloom-web` |
| Supabase database | "the Supabase database" | container `db-prod` |
| MinIO storage | "MinIO" | container `supabase-minio` |
| Reverse proxy | "Caddy" | container `caddy` |
| CI build + audit job | `build-and-audit` | — |
| CI Python audit job | `python-audit` | — |
| CI Docker build job | `docker-build` | — |
| CI integration test job | `compose-health-check` | — |
| CI image scanning jobs | `extract-pinned-images`, `scan-pinned-images`, `pinned-images-summary` | — |

## Scope

### Fix 15 existing commands

#### Replacement table (comprehensive)

| Wrong (current) | Correct | Notes |
|---|---|---|
| `pnpm lint` | `npm run lint` | |
| `pnpm format` / `pnpm format:check` | `npm run format` / `npm run format:check` | |
| `pnpm type-check` | `cd web && npx tsc --noEmit` | **No `type-check` script exists** |
| `pnpm test` / `pnpm test:coverage` | `uv run pytest tests/integration/ -v --tb=short` | **No npm test scripts exist; only integration tests** |
| `pnpm install --frozen-lockfile` | `npm ci` | |
| `pnpm install` | `npm install` | |
| `pnpm -v` | `npm -v` | |
| `npm install -g pnpm` | Remove entirely | npm is already available |
| `cd flask` | `cd langchain` or `cd bloommcp` | Context-dependent; see glossary |
| `flask/` directory references | `langchain/` and/or `bloommcp/` | |
| `flask/pyproject.toml` | `langchain/requirements.txt` and/or `bloommcp/requirements.txt` | |
| "Flask" (the framework) | "FastAPI" or "FastAPI + LangGraph" | |
| `flask-app` (container name) | `langchain-agent` or `bloommcp` | See glossary |
| `bloom-flask-app` | `langchain-agent` or `bloommcp` | |
| `bloom-minio` | `supabase-minio` | |
| `bloom-desktop` (repo references) | `bloom` | |
| `FLASK_ENV=test` | Remove or replace with actual env vars from `.env.example` | |
| `SECRET_KEY=test-secret-key` | Remove or replace with actual env vars | |
| `DOMAIN_FLASK` | Remove (vestigial) | |
| Port `5002` as "Flask API" | Remove; Caddy handles routing | |
| `supabase start` / `supabase status` / `supabase db reset` | `docker compose` equivalents (self-hosted) | **NOT using Supabase CLI** |
| `docker-compose` (v1 syntax) | `docker compose` (v2 syntax) | Match CI usage |
| Hypothetical CI jobs (`lint-typescript`, `lint-python`, `type-check`, `test-unit-frontend`, `test-unit-backend`) | Actual jobs: `build-and-audit`, `python-audit`, `docker-build`, `compose-health-check` | |
| Vitest/Jest unit test references | "No frontend unit tests exist yet" | Document current state accurately |
| Prisma/SQLite references | Supabase/PostgreSQL migrations | |
| `videoWriter.py`, `test_video.py`, `VideoWriter`, `VideoPlayer.tsx` | Remove entirely | bloom-desktop specific |
| `pytest-flask` | Remove | Not used in this repo |
| `packages/bloom-fs`, `packages/bloom-js`, `packages/bloom-nextjs-auth` as workspace examples | Use actual workspace names from this repo's `package.json` | |
| "Phase 1 CI/CD", "Phase 2", "Once Phase 1 is merged" | Remove (bloom-desktop phasing) | |
| `Codecov` references | Remove | Not configured |
| Claims that Black/Ruff/mypy block CI | Correct to: "recommended locally but NOT currently enforced in CI" | |
| Makefile CI targets (`make ci`, `make ci-quick`) | Check if Makefile exists at repo root; remove if not | |

#### Content review requirements

Each command also needs deep content review for:
- Fabricated output samples (fake test counts, fake coverage numbers) — must be removed or marked as illustrative
- Embedded shell scripts that hardcode wrong paths/tools
- References to bloom-desktop-specific architecture (Electron, IPC, preload, renderer)
- Coverage thresholds that don't match reality (no unit tests exist)

### Reclassified file change scope

| File | Classification | Rationale |
|---|---|---|
| `changelog.md` | Minor fixes | Flask -> langchain/bloommcp in examples |
| `ci-debug.md` | **Full rewrite** | Zero salvageable structure; 8 fictional CI jobs, fabricated output |
| `cleanup-merged.md` | Minor fixes | |
| `coverage.md` | **Full rewrite** | VideoWriter examples throughout, flask-specific patterns, wrong thresholds |
| `database-migration.md` | Moderate fixes | Already Supabase-focused; fix `supabase start` → Docker Compose |
| `docs-review.md` | Moderate fixes | |
| `fix-formatting.md` | Moderate fixes | pnpm -> npm, flask -> langchain/bloommcp, fix pre-commit section |
| `lint.md` | **Major rewrite** | Monorepo context section wrong, config file paths wrong, CI claims wrong |
| `pr-description.md` | **Major rewrite** | Flask examples throughout, wrong test commands, wrong coverage targets |
| `pre-merge.md` | **Major rewrite** | References fictional CI jobs, wrong test commands, depends on other commands |
| `release.md` | Moderate fixes | pnpm -> npm; verify no bloom-desktop publish assumptions |
| `run-ci-locally.md` | **Full rewrite** | Zero salvageable structure; 7-phase fictional pipeline, fake Makefile |
| `validate-env.md` | **Full rewrite** | Embedded shell script hardcodes pnpm, wrong ports, wrong container names, `supabase start` |

New files:
| File | Type |
|---|---|
| `copilot-review.md` | NEW command |
| `new-feature.md` | NEW command |
| `review-pr.md` | REPLACE with subagent version |
| `.claude/skills/openspec-review/SKILL.md` | NEW skill |

### Add 1 new skill

#### `.claude/skills/openspec-review/SKILL.md`
Port from bloom-desktop. Adaptations:

- **Subagent 1 (Spec Quality):** Keep mostly as-is (OpenSpec format rules are universal)
- **Subagent 2 (Code & Architecture):** Replace Electron/IPC/Prisma with: Next.js + Supabase + FastAPI + LangGraph + Docker + Caddy. Specific checks: API route patterns, Supabase client usage (server vs client), Docker networking, Caddy config
- **Subagent 3 (GitHub Issues):** Change repo context to `Salk-Harnessing-Plants-Initiative/bloom`, keep logic identical
- **Subagent 4 (TDD & Testing):** Replace Vitest/Playwright with pytest integration tests. **Critical guardrail:** "This repo currently has NO unit tests and NO frontend tests. The only tests are pytest integration tests in `tests/integration/`. TDD for this project means: write integration tests before implementation. Do NOT flag absence of unit tests as BLOCKING — that is the documented current state. DO flag if tasks.md does not include integration tests for new behavior."
- **Subagent 5 (Scientific Rigor):** Rebuild from scratch for web platform phenotyping context. Concrete checklist items:
  1. Does the proposal affect Supabase RLS policies? Can a user see another user's data?
  2. Are scan data writes atomic? Can a partial upload corrupt a dataset?
  3. Are genome reference versions pinned and recorded with analysis results?
  4. Are JBrowse track configurations validated before rendering?
  5. Are Three.js 3D scene coordinate systems documented? (wrong scale = wrong measurements)
  6. Are D3 visualization data transformations unit-tested or validated?
  7. If defaults change, is there a migration path for existing data?
  8. Are units explicitly specified in all scientific parameters?

**Subagent failure handling (applies to both openspec-review and review-pr):** If any subagent fails to return or returns an error, note it explicitly in the synthesis: "Subagent N (Description): DID NOT COMPLETE — treat as BLOCKED pending re-run." Do NOT synthesize a passing verdict if any subagent failed. If a subagent result is suspiciously short (< 100 words), flag it for re-run.

### Add 3 new commands

#### 1. `new-feature.md`
Port from bloom-desktop. Adaptations:
- Change persona to "Next.js + Supabase + FastAPI + Docker web platform for plant phenotyping"
- Keep the OpenSpec and TDD workflow intact
- **Strengthen branch guardrail:** Step 1 must read: "If on `main`, STOP. Do not proceed to Step 2. Ask the user for a branch name, create and check out the branch, and confirm with `git branch --show-current` before continuing."
- **Add empty arguments guard:** If `$ARGUMENTS` is empty, ask the user to describe the feature before proceeding to Step 2.

#### 2. `copilot-review.md`
Port from bloom-desktop. Adaptations:
- Change repo name from `bloom-desktop` to `bloom` in all GraphQL/REST queries
- Keep all functionality identical

#### 3. `review-pr.md` (replace existing)
Replace the current checklist-style review with bloom-desktop's 5-subagent parallel review. Adaptations:

**Intro:** Change from "Electron + React + TypeScript + Python" to "Next.js + Supabase + FastAPI + Docker web platform for plant phenotyping"

**Step 1 (Gather PR Context):** Change repo name in GraphQL query to `bloom`

**Subagent 1 (Code Quality & Architecture):**
- Architecture description: `Next.js app (web/) -> Supabase (auth, DB, storage, realtime) -> FastAPI + LangGraph agent (langchain/) -> FastMCP data analysis server (bloommcp/) -> Docker Compose orchestration -> Caddy reverse proxy`
- Specific checks: Are Next.js server components fetching data server-side or leaking to client? Are Supabase client calls made server-side where they should be? Do new API routes follow existing patterns? Are Docker networking changes consistent across dev/prod compose files?
- Keep naming convention checks (camelCase TS, snake_case Python, kebab-case filenames)

**Subagent 2 (Testing Strategy):**
- Infrastructure: `pytest integration tests (tests/integration/), run via uv run pytest, executed in CI compose-health-check job after full stack is healthy`
- **Critical guardrail:** "This repo has NO unit tests and NO frontend tests. Do NOT flag their absence as BLOCKING. DO flag if new behavior lacks integration test coverage."
- CI runs on Linux only (no matrix)

**Subagent 3 (Scientific Rigor, Metadata & UX):**
- Concrete adversarial checklist:
  1. Does the PR affect Supabase RLS policies? Could a user see another user's genomic/scan data?
  2. Are data writes atomic? Can a partial upload or failed request corrupt a dataset?
  3. Are genome reference versions or analysis parameters recorded with results?
  4. Could any race condition in Supabase realtime subscriptions cause stale data display?
  5. Are visualization transformations (D3, Three.js, JBrowse) scientifically accurate?
  6. Are destructive actions (delete dataset, remove organism) guarded with confirmation?
  7. Are error messages meaningful to scientists (not just developers)?
  8. If the PR changes data schemas, is there a migration path for existing records?

**Subagent 4 (Security):**
- Replace Electron security with: Supabase RLS policies, JWT validation, Docker security (no-new-privileges, cap_drop ALL, read_only), Caddy TLS, API input validation, MinIO presigned URL security
- Remove cross-platform section (server-side deployment only)

**Subagent 5 (Behavioural Correctness & Edge Cases):**
- Trace call chains: Next.js server actions/API routes -> Supabase -> FastAPI -> response
- Focus on: concurrent user scenarios, Supabase realtime subscriptions, Docker container health/restart, file upload edge cases, LangGraph agent streaming responses

**Step 3 (Synthesize):** Keep structure. Add guardrail: "Before posting APPROVE, verify: (a) at least one subagent checked Supabase RLS coverage for any schema changes, (b) no subagent result was empty or suspiciously short, (c) all 5 subagents returned successfully."

## Implementation Ordering

Commands have dependencies. Implementation must follow this order:

1. **Full rewrites first** (no dependencies): `ci-debug.md`, `run-ci-locally.md`, `validate-env.md`, `coverage.md`
2. **Major rewrites** (may reference phase 1 commands): `lint.md`, `pr-description.md`, `pre-merge.md`
3. **Moderate fixes**: `database-migration.md`, `docs-review.md`, `fix-formatting.md`, `release.md`
4. **Minor fixes**: `changelog.md`, `cleanup-merged.md`
5. **New files**: `review-pr.md`, `new-feature.md`, `copilot-review.md`, `.claude/skills/openspec-review/SKILL.md`

## Parallel Execution Partitioning

If using parallel subagents, partition files so no two agents touch files that reference each other:

- **Agent A** (independent, minor/moderate): `changelog.md`, `cleanup-merged.md`, `release.md`, `docs-review.md`
- **Agent B** (Python tooling): `lint.md`, `fix-formatting.md`, `coverage.md`
- **Agent C** (full rewrites, CI-focused): `ci-debug.md`, `run-ci-locally.md`
- **Agent D** (infra/setup, depends on C): `validate-env.md`, `database-migration.md`, `pre-merge.md`, `pr-description.md`
- **Agent E** (new files): `review-pr.md`, `new-feature.md`, `copilot-review.md`, `openspec-review/SKILL.md`

All agents receive the canonical glossary and replacement table.

## Verification Plan

After ALL files are adapted, run these grep checks. All must return zero results:

```bash
# Prohibited strings — NONE should appear in adapted files
grep -ri "pnpm" .claude/commands/ .claude/skills/
grep -ri "flask" .claude/commands/ .claude/skills/
grep -ri "test_video\|videoWriter\|VideoWriter\|VideoPlayer" .claude/commands/ .claude/skills/
grep -ri "FLASK_ENV\|flask-app\|bloom-flask" .claude/commands/ .claude/skills/
grep -ri "bloom-desktop" .claude/commands/ .claude/skills/
grep -ri "vitest\|jest" .claude/commands/ .claude/skills/ # (unless explicitly noting they don't exist)
grep -ri "pytest-flask" .claude/commands/ .claude/skills/
grep -ri "supabase start\|supabase status\|supabase db reset" .claude/commands/ .claude/skills/
grep -ri "bloom-minio" .claude/commands/ .claude/skills/ # should be supabase-minio
grep -ri "port 5002\|localhost:5002" .claude/commands/ .claude/skills/
grep -ri "Phase 1\|Phase 2\|Phase 3" .claude/commands/ .claude/skills/
grep -ri "Codecov" .claude/commands/ .claude/skills/
```

Additionally verify:
- Every CI job name referenced matches actual jobs in `pr-checks.yml`
- Every `npm run <script>` referenced exists in root or workspace `package.json`
- Every container name referenced exists in `docker-compose.prod.yml` or `docker-compose.dev.yml`
- Every file path referenced (e.g., `langchain/requirements.txt`) actually exists

## Rollback Plan

- All work done on a feature branch (not `main`)
- One commit per logical group (matching the parallel execution partitioning)
- Pre-adaptation state restorable via `git checkout main -- .claude/commands/ .claude/skills/`

## Non-goals

- Not adding new commands beyond the 3 specified (plus 1 skill)
- Not changing the OpenSpec commands (they're managed by the openspec tool)
- Not fixing `.pre-commit-config.yaml` (broken `^flask/` patterns) — that's a separate bug
- Not modifying CLAUDE.md or other config files
- Not adding unit test infrastructure (just documenting current state accurately)
