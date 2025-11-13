# Design: Development Workflow Commands

## Overview

This change adds 6 new Claude commands to streamline Bloom's development workflow. The commands are adapted from proven patterns in sibling repositories (Ariadne, Bloom-Desktop, Sleap-Roots) and tailored to Bloom's specific tech stack (Next.js + Flask + Supabase + Docker).

## Command Adaptations

### 1. release.md (from Ariadne)

**Source**: Ariadne's PyPI release automation
**Adaptation**: Modified for npm package releases instead of PyPI

**Key Changes**:

- Replace `poetry version` with `npm version`
- Replace `twine upload` with `npm publish`
- Replace `setup.py` references with `package.json`
- Keep semantic versioning (major.minor.patch)
- Keep changelog generation from git commits
- Keep GitHub release creation workflow

**Bloom-Specific Features**:

- Pre-release checks: `/lint`, `/coverage`, `/docs-review`
- Multi-package releases (web/, packages/\*)
- Turbo cache invalidation after version bump
- Docker image tagging with version

### 2. ci-debug.md (from Bloom-Desktop)

**Source**: Bloom-Desktop's comprehensive CI debugging (10 jobs across 3 platforms)
**Adaptation**: Simplified for Bloom's GitHub Actions (currently none, but planned for Phase 3)

**Key Changes**:

- Remove Electron-specific debugging (code signing, notarization)
- Remove hardware testing scenarios (camera, DAQ)
- Keep platform-specific issues (Linux, macOS, Windows)
- Keep artifact and timeout debugging
- Add Docker layer caching issues
- Add Supabase/MinIO connection debugging

**Bloom-Specific Features**:

- Flask containerization issues
- Next.js build caching
- Supabase self-hosted CI setup
- MinIO bucket initialization in CI

### 3. validate-env.md (from Sleap-Roots)

**Source**: Sleap-Roots conda environment validation
**Adaptation**: Modified for Node.js + Python + Docker stack

**Key Changes**:

- Replace conda checks with Node.js/pnpm checks
- Add Docker and Docker Compose validation
- Add PostgreSQL/MinIO connectivity checks
- Replace pytest checks with both pytest and Jest checks
- Add environment variable validation (.env.dev)
- Add port availability checks (3000, 5002, 8000, etc.)

**Bloom-Specific Features**:

- Validate uv installation (Python package manager)
- Check Supabase CLI availability
- Verify minio_data/ directory permissions
- Validate turbo.json configuration

### 4. run-ci-locally.md (from Sleap-Roots)

**Source**: Sleap-Roots local CI execution
**Adaptation**: Modified for Bloom's dual-stack (Python + TypeScript) and Docker setup

**Key Changes**:

- Add TypeScript linting/type checking (not just Python)
- Add Docker build validation
- Add pre-commit hook execution
- Replace conda activation with uv/pnpm workflows
- Add parallel execution with Turbo

**Bloom-Specific Features**:

- Run Flask linting: `uv run black --check . && uv run ruff check . && uv run mypy .`
- Run Next.js checks: `pnpm lint && pnpm type-check`
- Run pre-commit: `uv run pre-commit run --all-files`
- Docker build test: `docker-compose -f docker-compose.dev.yml build`
- Turbo parallel execution: `pnpm lint && pnpm test:coverage`

### 5. database-migration.md (from Bloom-Desktop)

**Source**: Bloom-Desktop's Prisma SQLite migrations
**Adaptation**: Modified for Supabase PostgreSQL migrations

**Key Changes**:

- Replace Prisma CLI with Supabase CLI
- Replace SQLite with PostgreSQL migration commands
- Keep migration testing procedures
- Keep rollback procedures
- Add RLS policy migration patterns

**Bloom-Specific Features**:

- Supabase migration workflow: `supabase db push`, `supabase migration new`
- RLS policy testing
- Migration status: `supabase db diff`
- Local vs remote migration sync
- Test data seeding with dev_init.ts

### 6. fix-formatting.md (from Sleap-Roots)

**Source**: Sleap-Roots Black/isort auto-fix
**Adaptation**: Modified for Prettier + ESLint + Black + Ruff

**Key Changes**:

- Add Prettier auto-fix (not just Black)
- Add ESLint auto-fix
- Add Ruff auto-fix (modern Python linter)
- Keep Black auto-fix
- Add monorepo-wide commands

**Bloom-Specific Features**:

- Auto-fix all: `pnpm format && cd flask && uv run black . && uv run ruff check --fix .`
- Pre-commit auto-fix: `uv run pre-commit run --all-files` (automatically fixes)
- Turbo parallel: `pnpm format:fix && pnpm lint:fix`

## Cross-Command Integration

### release.md References:

- `/lint` - Run before release
- `/coverage` - Ensure tests pass before release
- `/docs-review` - Update documentation before release
- `/run-ci-locally` - Validate release locally

### lint.md References:

- `/fix-formatting` - Auto-fix issues instead of just reporting

### pr-description.md References:

- `/run-ci-locally` - Run before creating PR

### docs-review.md References:

- `/validate-env` - Ensure environment is correct for docs testing

## Technical Decisions

### Decision 1: Semantic Versioning for All Packages

**Rationale**: Bloom is a monorepo with multiple packages (web/, packages/\*). Using semantic versioning (major.minor.patch) provides clear communication about breaking changes and helps manage interdependencies.

**Implementation**: `release.md` uses `npm version` with `--workspaces` flag to bump all package versions together, maintaining version consistency.

### Decision 2: Local CI Execution Without Docker

**Rationale**: Running CI locally should be fast for quick feedback. Running linting, type checking, and tests directly (without Docker) is 10x faster than rebuilding Docker containers.

**Implementation**: `run-ci-locally.md` runs checks natively (uv, pnpm) and only validates Docker builds as a final step.

### Decision 3: Pre-commit as Single Source of Truth

**Rationale**: Bloom already has pre-commit hooks configured. Rather than duplicating validation logic, leverage pre-commit for consistency.

**Implementation**: `run-ci-locally.md` and `fix-formatting.md` both use `pre-commit run --all-files` as the comprehensive check.

### Decision 4: Supabase CLI for Migrations

**Rationale**: Bloom uses self-hosted Supabase, which provides its own migration CLI. Using Supabase CLI keeps migrations consistent with the Supabase ecosystem.

**Implementation**: `database-migration.md` uses `supabase migration` commands instead of raw SQL or ORM migrations.

## Migration Strategy

### Phase 1: High-Value Commands First

Start with `release.md`, `ci-debug.md`, and `validate-env.md` - these provide immediate value for deployment and onboarding.

### Phase 2: Developer Experience

Add `run-ci-locally.md` and `fix-formatting.md` - these improve day-to-day development experience.

### Phase 3: Database Workflows

Add `database-migration.md` once database schema changes become more frequent.

## Validation Strategy

### Manual Testing

Each command must be manually tested before merge:

1. **release.md**: Test on dummy package with version 0.0.1, bump to 0.1.0, verify changelog generation
2. **ci-debug.md**: Intentionally break CI, use guide to debug, verify fixes
3. **validate-env.md**: Run on clean machine (or Docker container), verify all checks pass
4. **run-ci-locally.md**: Run locally, compare results with GitHub Actions (once CI exists)
5. **database-migration.md**: Create test migration, apply, rollback, verify data integrity
6. **fix-formatting.md**: Introduce formatting errors, run command, verify all fixed

### Automated Validation

OpenSpec validation with `--strict` mode:

```bash
openspec validate add-development-commands --strict
```

## Future Enhancements

### Potential Future Commands (Not in This Change)

Based on other repositories, these could be added later if needed:

1. **e2e-testing.md** - Playwright E2E testing (if E2E tests added)
2. **integration-testing.md** - Multi-service integration tests
3. **performance-testing.md** - Load testing, profiling
4. **security-scan.md** - Dependency scanning, SAST
5. **pre-merge-check.md** - Automated PR review analysis (from Ariadne)

### Command Evolution

As Bloom's workflow evolves, commands should be updated:

- Add GitHub Actions workflows → update `ci-debug.md`
- Add E2E tests → create `e2e-testing.md`
- Add monitoring → create `debug-prod.md`

## Risks & Mitigations

### Risk 1: Command Staleness

**Risk**: Commands become outdated as Bloom's tech stack evolves.

**Mitigation**: Add `/docs-review` checklist item to verify command accuracy during major changes. Include "Last Updated" date in each command.

### Risk 2: Over-Documentation

**Risk**: Too many commands overwhelm developers.

**Mitigation**: Group commands by use case (daily development, release, troubleshooting). Add README.md section with command index.

### Risk 3: Command Complexity

**Risk**: Commands become too complex for new developers to understand.

**Mitigation**: Each command includes "Quick Commands" section with copy-paste examples before detailed explanations.

## Success Metrics

### Quantitative

- Release time reduced from ~30 minutes (manual) to ~5 minutes (automated)
- CI debugging time reduced by 50% (faster issue identification)
- Onboarding time reduced by 20% (environment validation catches issues early)

### Qualitative

- Developers report increased confidence in release process
- Fewer "environment works on my machine" issues
- Faster PR review cycles (run-ci-locally catches issues before push)
