# Tasks

## Phase 1: Release Automation (Highest Priority)

- [ ] Create `.claude/commands/release.md` adapted from Ariadne
  - [ ] Add semantic versioning workflow (major.minor.patch)
  - [ ] Document changelog generation from git commits
  - [ ] Include npm package publishing steps
  - [ ] Add GitHub release creation with release notes
  - [ ] Document version bumping in package.json and related files
  - [ ] Include pre-release checks (lint, coverage, docs-review)
  - [ ] Add rollback procedures for failed releases
  - [ ] Test release workflow on feature branch

## Phase 2: CI & Environment (High Priority)

- [ ] Create `.claude/commands/ci-debug.md` adapted from Bloom-Desktop

  - [ ] Add platform-specific troubleshooting (Linux, macOS, Windows)
  - [ ] Document artifact upload/download debugging
  - [ ] Include timeout and resource limit debugging
  - [ ] Add Docker layer caching issues
  - [ ] Document GitHub Actions secret debugging
  - [ ] Include matrix job failure analysis
  - [ ] Add common CI failure patterns and fixes

- [ ] Create `.claude/commands/validate-env.md` adapted from Sleap-Roots

  - [ ] Add Node.js version validation
  - [ ] Check pnpm installation and version
  - [ ] Validate Python 3.11 and uv installation
  - [ ] Check Docker and Docker Compose versions
  - [ ] Validate environment variables (.env.dev)
  - [ ] Check port availability (3000, 5002, 8000, etc.)
  - [ ] Verify PostgreSQL and MinIO connectivity
  - [ ] Add automated fix suggestions for common issues

- [ ] Create `.claude/commands/run-ci-locally.md` adapted from Sleap-Roots
  - [ ] Document running linting checks locally
  - [ ] Add type checking execution
  - [ ] Include test suite execution
  - [ ] Document Docker build validation
  - [ ] Add pre-commit hook validation
  - [ ] Include coverage threshold checks
  - [ ] Document parallel execution options

## Phase 3: Database & Formatting (Medium Priority)

- [ ] Create `.claude/commands/database-migration.md` adapted from Bloom-Desktop

  - [ ] Add migration creation workflow
  - [ ] Document Supabase migration commands
  - [ ] Include migration testing procedures
  - [ ] Add rollback procedures
  - [ ] Document data seeding for migrations
  - [ ] Include schema diff generation
  - [ ] Add migration status checking

- [ ] Create `.claude/commands/fix-formatting.md` adapted from Sleap-Roots
  - [ ] Add Prettier auto-fix for TypeScript/JavaScript
  - [ ] Include ESLint auto-fix
  - [ ] Add Black auto-fix for Python
  - [ ] Include Ruff auto-fix
  - [ ] Document pre-commit auto-fix
  - [ ] Add monorepo-wide formatting command

## Phase 4: Integration & Validation

- [ ] Update existing commands to reference new commands

  - [ ] Update `lint.md` to reference `fix-formatting.md`
  - [ ] Update `pr-description.md` to reference `run-ci-locally.md`
  - [ ] Update `docs-review.md` to reference `validate-env.md`

- [ ] Validate all commands work correctly

  - [ ] Test release.md workflow on dummy package
  - [ ] Test ci-debug.md scenarios
  - [ ] Test validate-env.md on clean environment
  - [ ] Test run-ci-locally.md matches CI results
  - [ ] Test database-migration.md with test migration
  - [ ] Test fix-formatting.md on sample files

- [ ] Document commands in README.md
  - [ ] Add "Development Commands" section
  - [ ] List all available commands with brief descriptions
  - [ ] Group commands by category (quality, workflow, deployment)
