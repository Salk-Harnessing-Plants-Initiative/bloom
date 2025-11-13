# Tasks

## Phase 1: Core Development Commands

- [ ] Create `.claude/commands/lint.md`

  - [ ] Add commands for Python linting (black, ruff, mypy in flask/)
  - [ ] Add commands for JavaScript linting (prettier, eslint in web/)
  - [ ] Include monorepo context (web/, flask/, packages/)
  - [ ] Document pre-commit hook integration
  - [ ] Add examples for fixing common issues

- [ ] Create `.claude/commands/coverage.md`
  - [ ] Add pytest coverage commands for Flask API
  - [ ] Document 70% coverage threshold from pyproject.toml
  - [ ] Include future Jest setup placeholder for web/
  - [ ] Explain coverage report locations (htmlcov/, coverage/)
  - [ ] Add commands to view coverage by package

## Phase 2: Git & PR Workflow Commands

- [ ] Create `.claude/commands/pr-description.md`

  - [ ] Adapt template for Bloom's monorepo structure
  - [ ] Include checklist for Flask API changes
  - [ ] Include checklist for Next.js frontend changes
  - [ ] Include checklist for Supabase/database changes
  - [ ] Add Docker Compose deployment notes section
  - [ ] Document GitHub CLI commands for PR creation
  - [ ] Add examples for feature PRs and bug fix PRs

- [ ] Create `.claude/commands/review-pr.md`
  - [ ] Create review checklist for code quality
  - [ ] Add type safety checks for TypeScript and Python
  - [ ] Include testing requirements (pytest, future Jest)
  - [ ] Add monorepo-specific checks (dependencies, build order)
  - [ ] Include security checks (env vars, secrets, RLS policies)
  - [ ] Add performance considerations (React re-renders, DB queries)
  - [ ] Document GitHub CLI review commands
  - [ ] Add response workflow for reviewers and authors

## Phase 3: Maintenance Commands

- [ ] Create `.claude/commands/cleanup-merged.md`

  - [ ] Add branch verification steps (gh pr list)
  - [ ] Document safe branch deletion (git branch -d)
  - [ ] Include OpenSpec archival workflow (openspec archive)
  - [ ] Add archive README update template
  - [ ] Document commit message format for cleanup
  - [ ] Include verification steps
  - [ ] Add scenarios (with/without OpenSpec)

- [ ] Create `.claude/commands/changelog.md`
  - [ ] Document Keep a Changelog format
  - [ ] Add git log commands for finding changes
  - [ ] Create changelog template with Bloom examples
  - [ ] Include monorepo package labeling (**flask**: ..., **web**: ...)
  - [ ] Document semantic versioning guidelines
  - [ ] Add breaking change documentation format
  - [ ] Include release checklist
  - [ ] Add Bloom-specific examples (video generation, scan data, Supabase changes)

## Phase 4: Validation & Documentation

- [ ] Test all commands in actual workflows

  - [ ] Test lint command on flask/ and web/
  - [ ] Test coverage command with pytest
  - [ ] Test PR workflow with test PR
  - [ ] Test cleanup-merged on merged branch
  - [ ] Test changelog update with recent commits

- [ ] Update main README to mention slash commands

  - [ ] Add section documenting available commands
  - [ ] Link to `.claude/commands/` directory
  - [ ] Explain how to use slash commands in Claude Code

- [ ] Validate commands integrate with Phase 1 CI/CD
  - [ ] Verify lint commands match pre-commit config
  - [ ] Verify coverage commands match pytest config
  - [ ] Verify Python commands use uv
  - [ ] Verify JavaScript commands use pnpm

## Acceptance Criteria

- All 6 command files exist in `.claude/commands/`
- Commands reference correct tools (uv, pnpm, pytest, black, ruff, mypy, prettier, eslint)
- Commands adapted for Bloom's monorepo structure (web/, flask/, packages/)
- Examples use Bloom-specific context (video generation, scan data, Supabase)
- Documentation is clear and actionable
- Commands tested in real workflows
- README updated with command documentation
