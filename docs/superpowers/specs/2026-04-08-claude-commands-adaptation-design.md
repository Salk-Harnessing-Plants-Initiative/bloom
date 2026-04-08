# Claude Commands Adaptation Design

## Purpose

Adapt all `.claude/commands/` files in salk-bloom to accurately reflect the actual project stack, and port three high-value commands from bloom-desktop.

## Background

The existing 15 commands were templated with references to the wrong tools and directories. They reference `pnpm`, `flask/`, Vitest/Jest unit tests, and hypothetical CI jobs that don't exist. The actual stack is:

- **Frontend:** Next.js 16 + React 19 + Tailwind + MUI v7 (in `web/`)
- **Backend:** FastAPI + LangGraph agent (`langchain/`), FastMCP server (`bloommcp/`)
- **Database:** Self-hosted Supabase (PostgreSQL 15, migrations in `supabase/`)
- **Storage:** MinIO (S3-compatible)
- **Reverse proxy:** Caddy (auto-HTTPS)
- **Package manager:** npm workspaces + Turborepo
- **Python tooling:** uv
- **Testing:** Integration tests only (`tests/integration/`, pytest via `uv run pytest`)
- **CI:** `pr-checks.yml` (5 parallel jobs) and `deploy.yml` (build + deploy pipeline)
- **Docker:** `docker-compose.prod.yml` (~15 containers), `docker-compose.dev.yml`
- **GitHub org/repo:** `Salk-Harnessing-Plants-Initiative/bloom`

## Scope

### Fix 15 existing commands

Global find-and-replace patterns across all files:

| Wrong (current) | Correct |
|---|---|
| `pnpm lint` | `npm run lint` |
| `pnpm format` / `pnpm format:check` | `npm run format` / `npm run format:check` |
| `pnpm type-check` | `npm run type-check` |
| `pnpm test` / `pnpm test:coverage` | `npm run test` / `npm run test:coverage` |
| `pnpm install --frozen-lockfile` | `npm ci` |
| `pnpm install` | `npm install` |
| `pnpm -v` | `npm -v` |
| `cd flask` | `cd langchain` or `cd bloommcp` (context-dependent) |
| `flask/` references | `langchain/` and/or `bloommcp/` |
| "Flask" (the framework) | "FastAPI" or "FastAPI + LangGraph" |
| `flask-app` (container name) | `langchain-agent` or `bloommcp` |
| `bloom-desktop` (repo references) | `bloom` |
| Hypothetical/planned CI jobs | Actual jobs from `pr-checks.yml` |
| `npm install -g pnpm` | Remove (npm is already available) |
| Vitest/Jest unit test references | Note: no unit tests yet; integration tests via pytest |
| Prisma/SQLite references | Supabase/PostgreSQL migrations |
| `videoWriter.py`, `test_video.py` | Remove or replace with actual file references |

Additionally, each command needs content review for:
- References to bloom-desktop-specific architecture (Electron, IPC, preload, renderer)
- References to scripts that don't exist in this repo's `package.json`
- Coverage thresholds and test infrastructure that doesn't match reality

### Add 1 new skill

#### `.claude/skills/openspec-review/SKILL.md`
Port from bloom-desktop's `.claude/skills/openspec-review/SKILL.md`. This is a 5-subagent parallel review for OpenSpec proposals (referenced by `new-feature` step 5). Adaptations:

- **Subagent 1 (Spec Quality):** Keep mostly as-is (OpenSpec format rules are universal)
- **Subagent 2 (Code & Architecture):** Replace Electron/IPC/Prisma with Next.js + Supabase + FastAPI + LangGraph + Docker + Caddy architecture
- **Subagent 3 (GitHub Issues):** Change repo context, keep logic identical
- **Subagent 4 (TDD & Testing):** Replace Vitest/Playwright infrastructure with pytest integration tests, note no unit tests yet, CI runs compose-health-check job
- **Subagent 5 (Scientific Rigor):** Shift from desktop scanning/camera/metadata.json to web platform for plant phenotyping — genomic data integrity, Supabase RLS for multi-user data isolation, visualization accuracy (JBrowse, Three.js, D3)

### Add 3 new commands

#### 1. `new-feature.md`
Port from bloom-desktop. Adaptations:
- Change persona from "Electron + React + TypeScript + Python" to "Next.js + Supabase + FastAPI + Docker web platform for plant phenotyping"
- Keep the OpenSpec and TDD workflow intact
- Keep the guardrails and step sequence

#### 2. `copilot-review.md`
Port from bloom-desktop. Adaptations:
- Change repo name from `bloom-desktop` to `bloom` in all GraphQL/REST queries
- Keep all functionality identical

#### 3. `review-pr.md` (replace existing)
Replace the current checklist-style review with bloom-desktop's 5-subagent parallel review. Adaptations:

**Intro:** Change from "Electron + React + TypeScript + Python" to "Next.js + Supabase + FastAPI + Docker web platform for plant phenotyping"

**Step 1 (Gather PR Context):** Change repo name in GraphQL query to `bloom`

**Subagent 1 (Code Quality & Architecture):**
- Replace Electron architecture description with: `Next.js app (web/) -> Supabase (auth, DB, storage, realtime) -> FastAPI + LangGraph agent (langchain/) -> FastMCP data analysis server (bloommcp/) -> Docker Compose orchestration -> Caddy reverse proxy`
- Replace IPC/preload checks with: API route patterns, Supabase client usage, server vs client components, Docker networking
- Keep naming convention checks (camelCase TS, snake_case Python, kebab-case filenames)

**Subagent 2 (Testing Strategy):**
- Replace Vitest/Playwright/pytest infrastructure with: `pytest integration tests (tests/integration/), run via uv run pytest, executed in CI compose-health-check job after full stack is healthy`
- Note: no unit tests yet, no frontend tests yet
- CI runs on Linux only (no matrix)

**Subagent 3 (Scientific Rigor, Metadata & UX):**
- Adapt from desktop scanning app to web platform context
- Focus on: genomic data integrity, scan data visualization accuracy, Supabase RLS for multi-user data isolation, JBrowse genome browser integration, 3D visualization (Three.js) correctness
- Keep data integrity and reproducibility principles

**Subagent 4 (Security & Cross-Platform Safety):**
- Replace Electron security (context bridge, preload) with: Supabase RLS policies, JWT validation, Docker security (no-new-privileges, cap_drop ALL, read_only), Caddy TLS, API input validation, MinIO presigned URL security
- Remove cross-platform section (server-side only)

**Subagent 5 (Behavioural Correctness & Edge Cases):**
- Adapt from Electron IPC call chains to: Next.js server actions/API routes -> Supabase -> FastAPI -> response flow
- Focus on: concurrent user scenarios, Supabase realtime subscriptions, Docker container health/restart behavior, file upload edge cases

**Step 3 (Synthesize):** Keep identical structure, just update the team description line.

## Files Changed

New skill:
- `.claude/skills/openspec-review/SKILL.md` - NEW (5-subagent OpenSpec proposal review)

All in `.claude/commands/`:
- `changelog.md` - minor fixes (flask -> langchain/bloommcp references)
- `ci-debug.md` - major rewrite (match actual CI from pr-checks.yml)
- `cleanup-merged.md` - minor fixes
- `copilot-review.md` - NEW
- `coverage.md` - moderate fixes (flask -> langchain/bloommcp, correct test commands)
- `database-migration.md` - moderate fixes (already Supabase-focused, fix flask refs)
- `docs-review.md` - moderate fixes
- `fix-formatting.md` - moderate fixes (pnpm -> npm run, flask -> langchain/bloommcp)
- `lint.md` - moderate fixes (pnpm -> npm run, flask -> langchain/bloommcp)
- `new-feature.md` - NEW
- `pr-description.md` - moderate fixes (pnpm -> npm run, flask refs, test commands)
- `pre-merge.md` - moderate fixes (pnpm -> npm run, flask refs)
- `release.md` - moderate fixes (pnpm -> npm run)
- `review-pr.md` - REPLACE with subagent version
- `run-ci-locally.md` - major rewrite (match actual stack and CI)
- `validate-env.md` - moderate fixes (pnpm -> npm, flask -> langchain/bloommcp, correct ports/services)

## Non-goals

- Not adding new commands beyond the 3 specified (plus 1 skill)
- Not changing the OpenSpec commands (they're managed by the openspec tool)
- Not modifying CLAUDE.md or other config files
- Not adding unit test infrastructure (just documenting current state accurately)
