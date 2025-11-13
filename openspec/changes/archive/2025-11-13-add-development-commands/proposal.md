# Add Development Workflow Commands

## Why

Bloom currently has 8 Claude commands focused on code quality (lint, coverage), documentation (docs-review), and git workflow (PR management, cleanup). However, it lacks commands for critical development workflows that exist in other projects: **release automation**, **CI debugging**, **environment validation**, **database migrations**, and **local CI execution**. These gaps slow down development, deployment, and onboarding processes.

Adding these commands will:

- **Streamline releases**: Automate version bumping, changelog generation, and npm publishing
- **Reduce CI failures**: Debug platform-specific issues, timeouts, and artifacts locally
- **Improve onboarding**: Validate development environment setup with clear error messages
- **Simplify migrations**: Provide systematic database migration workflows
- **Catch issues earlier**: Run full CI suite locally before pushing

## What Changes

Add 6 new Claude commands adapted from proven patterns in sibling repositories (Ariadne, Bloom-Desktop, Sleap-Roots):

1. **`release.md`** - Complete release automation with semantic versioning, changelog generation, and package publishing
2. **`ci-debug.md`** - Comprehensive CI troubleshooting guide for platform-specific issues
3. **`validate-env.md`** - Development environment validation with automated fix suggestions
4. **`run-ci-locally.md`** - Execute full CI checks locally before pushing
5. **`database-migration.md`** - Database migration workflows (create, test, rollback)
6. **`fix-formatting.md`** - Auto-fix all formatting issues (Prettier, ESLint, Black, Ruff)

Update existing command:

- **`release.md`** will reference `lint`, `coverage`, `docs-review` as pre-release checks

## Impact

### Affected Specs

- **New spec**: `development-workflow` - defines requirements for release, CI, environment, migration, and formatting commands

### Affected Code

- `.claude/commands/` - add 6 new command files (~2,000 lines total)
- Existing commands unchanged (no modifications needed)

### Affected Documentation

- `README.md` - optionally mention new commands in development workflow section
- `.claude/commands/release.md` - will document release process

### Breaking Changes

None - purely additive. All new commands are opt-in.
