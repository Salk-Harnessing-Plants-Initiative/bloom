# developer-commands Specification

## Purpose

TBD - created by archiving change add-claude-commands. Update Purpose after archive.

## Requirements

### Requirement: Linting Command

The system SHALL provide a `/lint` slash command that runs code quality checks across the monorepo.

#### Scenario: Python linting in Flask API

**Given** a developer working on Flask API code
**When** they run `/lint`
**Then** the command documentation shows how to run black, ruff, and mypy on `flask/` directory
**And** the commands use `uv run` for tool execution
**And** the commands reference `.pre-commit-config.yaml` settings

#### Scenario: JavaScript linting in Next.js web app

**Given** a developer working on Next.js frontend code
**When** they run `/lint`
**Then** the command documentation shows how to run prettier and eslint on `web/` directory
**And** the commands use `pnpm` for script execution
**And** the commands reference `.eslintrc.js` and `.prettierrc.json` settings

#### Scenario: Monorepo-wide linting

**Given** a developer wants to lint the entire codebase
**When** they run `/lint`
**Then** the command documentation shows how to run linting across all packages
**And** the output explains which checks apply to which directories

---

### Requirement: Coverage Command

The system SHALL provide a `/coverage` slash command that runs test coverage analysis.

#### Scenario: Flask API coverage analysis

**Given** a developer wants to check Flask API test coverage
**When** they run `/coverage`
**Then** the command documentation shows how to run `uv run pytest --cov`
**And** the documentation explains the 70% coverage threshold from `pyproject.toml`
**And** the documentation shows how to view HTML coverage reports in `htmlcov/`

#### Scenario: Understanding coverage results

**Given** a developer has run coverage analysis
**When** they review the `/coverage` command documentation
**Then** the documentation explains coverage metrics (statements, branches, functions, lines)
**And** the documentation provides guidance on what to test (core logic vs integration)

---

### Requirement: PR Description Command

The system SHALL provide a `/pr-description` slash command with a pull request template.

#### Scenario: Creating a Flask API feature PR

**Given** a developer has implemented a new Flask API feature
**When** they run `/pr-description`
**Then** the command provides a template with sections for summary, changes, testing, and deployment
**And** the template includes Flask-specific checklist items (pytest, coverage, type hints)
**And** the template shows GitHub CLI commands for PR creation

#### Scenario: Creating a frontend feature PR

**Given** a developer has implemented a new Next.js frontend feature
**When** they run `/pr-description`
**Then** the template includes frontend-specific checklist items (TypeScript, ESLint, component testing)
**And** the template includes sections for screenshots and UI changes

#### Scenario: Creating a database migration PR

**Given** a developer has created a Supabase migration
**When** they run `/pr-description`
**Then** the template includes database-specific checklist items (migrations, RLS policies, data encryption)
**And** the template includes deployment notes for running migrations

---

### Requirement: PR Review Command

The system SHALL provide a `/review-pr` slash command with a code review checklist.

#### Scenario: Reviewing Flask API changes

**Given** a reviewer is examining Flask API code changes
**When** they run `/review-pr`
**Then** the command provides a checklist for Python code quality (type hints, error handling, naming)
**And** the checklist includes Flask-specific items (route security, JWT validation, S3 operations)
**And** the documentation shows GitHub CLI commands for leaving review comments

#### Scenario: Reviewing Next.js changes

**Given** a reviewer is examining Next.js frontend changes
**When** they run `/review-pr`
**Then** the checklist includes React-specific items (component structure, hooks usage, re-renders)
**And** the checklist includes TypeScript type safety checks

#### Scenario: Reviewing Supabase schema changes

**Given** a reviewer is examining database schema changes
**When** they run `/review-pr`
**Then** the checklist includes database-specific items (RLS policies, encryption, migrations)
**And** the documentation emphasizes security and data privacy considerations

---

### Requirement: Cleanup Merged Command

The system SHALL provide a `/cleanup-merged` slash command for post-merge branch cleanup.

#### Scenario: Cleaning up a merged feature branch

**Given** a developer has merged a feature PR
**When** they run `/cleanup-merged`
**Then** the command shows how to verify merge status with `gh pr list --state merged`
**And** the command shows how to safely delete the branch with `git branch -d`
**And** the command shows how to clean up remote tracking with `git remote prune origin`

#### Scenario: Archiving an OpenSpec change

**Given** a merged PR was tracked with OpenSpec
**When** they run `/cleanup-merged`
**Then** the command shows how to run `openspec archive <id> --yes`
**And** the command shows how to update the archive README
**And** the command provides a commit message template for the archival

---

### Requirement: Changelog Command

The system SHALL provide a `/changelog` slash command for maintaining CHANGELOG.md.

#### Scenario: Adding changes to unreleased section

**Given** a developer wants to document recent changes
**When** they run `/changelog`
**Then** the command explains the Keep a Changelog format
**And** the command shows git log commands to find changes since last release
**And** the command provides templates for categorizing changes (Added, Changed, Fixed, Security)

#### Scenario: Documenting monorepo changes

**Given** a change affects multiple packages (flask/, web/, packages/)
**When** they run `/changelog`
**Then** the documentation shows how to label changes by package (**flask**: ..., **web**: ...)
**And** the documentation provides examples specific to Bloom (video generation, scan data, Supabase)

#### Scenario: Creating a new release

**Given** a maintainer is preparing a release
**When** they run `/changelog`
**Then** the command shows how to move [Unreleased] to a versioned section
**And** the command explains semantic versioning (major.minor.patch)
**And** the command provides a release checklist (version, date, links, breaking changes)
