# Add Claude Commands for Development Workflows

## Summary

Add a collection of slash commands (`.claude/commands/`) to streamline common development workflows in the Bloom repository. These commands will provide quick access to testing, linting, code review, changelog management, and branch cleanup operations, improving developer productivity and ensuring consistent practices across the team.

## Motivation

The Bloom repository currently has only OpenSpec-related commands. Developers would benefit from commands that:

1. **Standardize workflows**: Ensure consistent testing, linting, and deployment practices
2. **Reduce cognitive load**: Quick access to common operations without memorizing commands
3. **Improve code quality**: Easy access to coverage analysis and PR review checklists
4. **Streamline Git operations**: Automated branch cleanup and changelog maintenance
5. **Leverage existing CI/CD**: Integrate with Phase 1 CI/CD implementation (pre-commit, uv, pytest, etc.)

## Scope

### In Scope

- Create 6 new Claude command files in `.claude/commands/`:

  - `lint.md` - Run linting and formatting (black, ruff, mypy, prettier, eslint)
  - `coverage.md` - Run test coverage analysis (pytest with coverage, future Jest setup)
  - `pr-description.md` - PR description template and GitHub CLI helpers
  - `review-pr.md` - PR review checklist and workflow guidance
  - `cleanup-merged.md` - Branch cleanup and OpenSpec archival workflow
  - `changelog.md` - Changelog maintenance following Keep a Changelog format

- Adapt commands from cosmos-azul patterns to Bloom's tech stack:
  - Monorepo structure (web/, flask/, packages/)
  - Docker Compose operations (dev/prod environments)
  - Flask + Next.js + Supabase architecture
  - uv package manager (Python) and pnpm (JavaScript)
  - Pre-commit hooks integration

### Out of Scope

- Commands for infrastructure operations (docker-compose managed via Makefile)
- Database migration commands (to be addressed in database-testing proposal)
- Environment setup commands (covered by automate-environment-setup proposal)
- Additional OpenSpec commands (already exist in `.claude/commands/openspec/`)

## User Stories

**As a developer**, I want to quickly run linting checks so that I can ensure code quality before committing.

**As a code reviewer**, I want a standardized PR review checklist so that I don't miss critical issues.

**As a maintainer**, I want to easily update the changelog so that release notes are accurate and complete.

**As a developer**, I want to check test coverage so that I can identify untested code paths.

**As a contributor**, I want a PR description template so that I provide all necessary context for reviewers.

**As a maintainer**, I want to clean up merged branches so that the repository stays organized.

## Success Criteria

- [ ] All 6 command files created and functional
- [ ] Commands adapted to Bloom's specific tech stack
- [ ] Documentation includes both Python (Flask) and JavaScript (Next.js) workflows
- [ ] Commands integrate with Phase 1 CI/CD tooling (pre-commit, uv, pytest config)
- [ ] Examples and checklists updated for Bloom's architecture
- [ ] Commands tested and validated in actual workflows

## Dependencies

- Phase 1 CI/CD implementation (feature/implement-cicd-pipeline branch)
  - Pre-commit hooks (.pre-commit-config.yaml)
  - Python package management (uv, pyproject.toml)
  - Linting/formatting tools (black, ruff, mypy, prettier, eslint)
  - Test configuration (pytest with coverage)

## Risks & Mitigations

**Risk**: Commands reference tools not yet merged to main
**Mitigation**: Clearly document which commands require Phase 1 CI/CD to be merged first

**Risk**: Commands become outdated as tooling evolves
**Mitigation**: Include version comments and reference configuration files for source of truth

**Risk**: Commands assume specific GitHub workflow
**Mitigation**: Make GitHub CLI (`gh`) optional and provide alternative git commands

## Alternatives Considered

1. **Shell scripts instead of markdown commands**

   - Rejected: Markdown commands are more discoverable and easier to maintain
   - Markdown integrates better with Claude Code's slash command system

2. **Combining all commands into single file**

   - Rejected: Separate files allow focused workflows and easier navigation
   - Follows established pattern from cosmos-azul

3. **Creating infrastructure commands (docker-compose)**
   - Rejected: Makefile already provides good interface (`make dev-up`, etc.)
   - Would duplicate existing, well-documented commands

## Open Questions

None - scope is clear and pattern is established from cosmos-azul reference.

## References

- cosmos-azul repository: `/Users/elizabethberrigan/repos/cosmos-azul/.claude/commands/`
- Phase 1 CI/CD: `openspec/changes/implement-cicd-pipeline/`
- Keep a Changelog: https://keepachangelog.com/en/1.0.0/
- Semantic Versioning: https://semver.org/
