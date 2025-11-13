# development-workflow Specification

## Purpose

TBD - created by archiving change add-development-commands. Update Purpose after archive.

## Requirements

### Requirement: Release automation command MUST provide complete release workflow

The release command MUST automate the entire release process from version bumping to package publishing, ensuring consistency and reducing manual errors.

#### Scenario: Developer releases new minor version with automatic changelog generation

Given the developer is on the main branch with clean working tree
When they run the release command with minor version bump
Then the system should bump package.json version from 1.2.3 to 1.3.0
And the system should generate changelog entries from git commits since last release
And the system should create a git tag v1.3.0
And the system should push the tag to GitHub
And the system should create a GitHub release with generated changelog
And the system should optionally publish packages to npm registry

#### Scenario: Developer validates release prerequisites before publishing

Given the developer is preparing a release
When they run pre-release validation
Then the system should execute linting checks on all packages
And the system should run test coverage analysis
And the system should verify documentation is up to date
And the system should check for uncommitted changes
And the system should report any blocking issues

#### Scenario: Developer rolls back failed release

Given a release process failed during npm publish
When the developer initiates rollback
Then the system should delete the git tag locally and remotely
And the system should revert version bump commit
And the system should provide instructions for manual cleanup if needed

### Requirement: CI debugging command MUST provide systematic troubleshooting workflows

The CI debugging command MUST help developers quickly identify and resolve platform-specific CI failures, timeout issues, and artifact problems.

#### Scenario: Developer debugs platform-specific test failure

Given tests pass locally on macOS but fail on GitHub Actions Linux
When the developer consults the CI debugging guide
Then the guide should provide platform-specific troubleshooting steps
And the guide should explain differences in file system behavior
And the guide should suggest using Docker to reproduce Linux environment locally

#### Scenario: Developer resolves CI timeout issue

Given a CI job times out after 30 minutes
When the developer investigates timeout causes
Then the debugging guide should list common timeout causes (npm install, Docker build, tests)
And the guide should provide commands to measure step durations
And the guide should suggest optimization strategies (caching, parallelization)

#### Scenario: Developer fixes artifact upload failure

Given artifact upload fails with "no files found" error
When the developer checks artifact configuration
Then the debugging guide should explain artifact path resolution
And the guide should provide commands to verify file existence
And the guide should suggest using wildcard patterns correctly

### Requirement: Environment validation command MUST detect and suggest fixes for configuration issues

The environment validation command MUST check all required tools, versions, ports, and configurations, providing actionable fix suggestions for any issues found.

#### Scenario: New developer validates fresh environment setup

Given a developer just cloned the repository
When they run environment validation
Then the system should check Node.js version (>= 20)
And the system should verify pnpm installation
And the system should check Python version (>= 3.11)
And the system should verify uv installation
And the system should check Docker and Docker Compose versions
And the system should verify port availability (3000, 5002, 8000, 9100, 5432, 55323)
And the system should report all issues with fix suggestions

#### Scenario: Developer diagnoses environment variable issues

Given required environment variables are missing
When the developer runs environment validation
Then the system should check for .env.dev file existence
And the system should validate all required variables are set
And the system should verify variable formats (URLs, secrets, ports)
And the system should suggest copying from .env.dev.example if missing

#### Scenario: Developer verifies service connectivity

Given Docker services are running
When the developer validates connectivity
Then the system should ping Supabase at localhost:8000
And the system should verify MinIO accessibility at localhost:9100
And the system should check PostgreSQL connection on port 5432
And the system should report any connection failures with diagnostic steps

### Requirement: Local CI execution command MUST mirror GitHub Actions workflow

The local CI command MUST execute the exact same checks that run in GitHub Actions, providing fast feedback before pushing code.

#### Scenario: Developer runs full CI suite locally before pushing

Given the developer has made changes to TypeScript and Python code
When they execute the local CI command
Then the system should run Prettier formatting check on TypeScript files
And the system should run ESLint on all JavaScript/TypeScript files
And the system should run TypeScript type checking
And the system should run Black formatting check on Python files
And the system should run Ruff linting on Python files
And the system should run mypy type checking on Python files
And the system should run pre-commit hooks on all files
And the system should report all failures with file locations and line numbers

#### Scenario: Developer runs only Python checks for faster iteration

Given the developer is working on Flask code only
When they execute Python-only local CI checks
Then the system should run Black, Ruff, and mypy on Flask directory
And the system should complete in under 10 seconds
And the system should report results in same format as full CI

#### Scenario: Developer validates Docker builds match CI

Given CI builds Docker images for deployment
When the developer validates Docker builds locally
Then the system should build dev image with target "dev"
And the system should build prod image with target "prod"
And the system should verify image sizes are reasonable
And the system should report any build failures with layer information

### Requirement: Database migration command MUST provide safe migration workflows

The database migration command MUST guide developers through creating, testing, and deploying database schema changes safely.

#### Scenario: Developer creates new migration for table addition

Given the developer needs to add a new table
When they create a new migration
Then the system should generate migration file with timestamp
And the system should provide template for CREATE TABLE statement
And the system should remind about RLS policies
And the system should suggest adding indexes for foreign keys

#### Scenario: Developer tests migration on local database

Given a migration file is created
When the developer applies migration locally
Then the system should run `supabase db push`
And the system should verify migration applied successfully
And the system should show schema diff before applying
And the system should allow rollback if issues found

#### Scenario: Developer rolls back problematic migration

Given a migration caused data integrity issues
When the developer initiates rollback
Then the system should provide down migration instructions
And the system should verify data can be restored
And the system should suggest creating fix migration instead of rollback in production

### Requirement: Auto-formatting command MUST fix all style issues across codebase

The auto-formatting command MUST automatically fix formatting issues in TypeScript, JavaScript, and Python files, using consistent style rules.

#### Scenario: Developer auto-fixes all formatting issues before commit

Given the codebase has formatting violations
When the developer runs auto-formatting command
Then the system should run Prettier with --write on all TypeScript/JavaScript files
And the system should run Black on all Python files
And the system should run Ruff with --fix on all Python files
And the system should run ESLint with --fix on TypeScript files
And the system should report how many files were modified

#### Scenario: Developer fixes only staged files

Given the developer has staged changes for commit
When they run auto-format on staged files only
Then the system should run pre-commit hooks with auto-fix enabled
And the system should only modify staged files
And the system should re-stage fixed files automatically

#### Scenario: Developer verifies formatting matches CI requirements

Given auto-formatting was applied
When the developer runs formatting check
Then the system should run formatters in check mode
And the system should report zero violations
And the system should match exactly what CI will check
