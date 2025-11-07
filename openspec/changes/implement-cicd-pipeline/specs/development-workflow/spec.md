# Development Workflow Capability Specification

## ADDED Requirements

### Requirement: Python Package Management with uv

The project SHALL use uv for Python package management with pyproject.toml configuration, replacing pip and requirements.txt, to ensure fast, reproducible dependency installation.

#### Scenario: Developer installs Python dependencies

- **WHEN** a developer clones the repository and runs `uv sync`
- **THEN** all dependencies are installed from the uv.lock lockfile
- **AND** a virtual environment is created in `.venv/`
- **AND** installation completes in <30 seconds (vs 5+ minutes with pip)
- **AND** the exact same dependency versions are installed as on other machines

#### Scenario: Developer adds a new Python dependency

- **WHEN** a developer runs `uv add flask-cors`
- **THEN** the dependency is added to pyproject.toml `[project.dependencies]`
- **AND** uv.lock is updated with the resolved version
- **AND** the package is installed in the virtual environment
- **AND** the change can be committed for team use

#### Scenario: Docker build uses uv for faster builds

- **WHEN** Docker builds the Flask image
- **THEN** the Dockerfile uses `uv sync --frozen --no-dev` for production dependencies
- **AND** the build completes 10-100x faster than with pip
- **AND** the exact lockfile versions are installed (reproducible)

### Requirement: Automated Code Formatting

The project SHALL automatically format code using black (Python) and Prettier (TypeScript/JavaScript) to eliminate style discussions and ensure consistency.

#### Scenario: Developer formats Python code

- **WHEN** a developer runs `uv run black .` in the flask/ directory
- **THEN** all Python files are formatted to 88-character line length
- **AND** imports are organized consistently
- **AND** Google-style docstrings are preserved
- **AND** the formatting matches the project standard

#### Scenario: Developer formats TypeScript/JavaScript code

- **WHEN** a developer runs `pnpm run format` in the root directory
- **THEN** all `.js`, `.jsx`, `.ts`, `.tsx`, `.json`, and `.md` files are formatted
- **AND** formatting follows the Prettier configuration (2-space indents, single quotes, no semicolons)
- **AND** the formatting is consistent across all packages

#### Scenario: Pre-commit hook auto-formats code

- **WHEN** a developer commits code with `git commit`
- **THEN** pre-commit hooks run black and prettier automatically
- **AND** code is formatted before the commit is created
- **AND** the commit succeeds with formatted code
- **AND** no manual formatting step is required

### Requirement: Code Linting and Type Checking

The project SHALL enforce code quality standards using ruff (Python linting), mypy (Python type checking), and ESLint (TypeScript/JavaScript linting).

#### Scenario: Python code passes linting

- **WHEN** a developer runs `uv run ruff check .`
- **THEN** all Python code is checked for common errors (unused imports, undefined variables, etc.)
- **AND** violations are reported with file locations and rule names
- **AND** auto-fixable issues can be fixed with `ruff check --fix`

#### Scenario: Python code has full type annotations

- **WHEN** a developer runs `uv run mypy .`
- **THEN** all Python functions have type annotations for parameters and return values
- **AND** no implicit `Any` types are allowed
- **AND** type errors are reported with clear messages
- **AND** 100% type coverage is enforced

#### Scenario: TypeScript/JavaScript code passes linting

- **WHEN** a developer runs `pnpm run lint`
- **THEN** ESLint checks all TypeScript and JavaScript files
- **AND** Next.js best practices are enforced
- **AND** TypeScript recommended rules are applied
- **AND** unused variables and imports are flagged

### Requirement: Automated Testing with Coverage

The project SHALL require automated tests for all components with minimum 70% code coverage across branches, functions, lines, and statements.

#### Scenario: Frontend tests achieve 70% coverage

- **WHEN** a developer runs `pnpm --filter @bloom/web run test:coverage`
- **THEN** Jest runs all unit and integration tests for the web application
- **AND** coverage report shows ≥70% for branches, functions, lines, statements
- **AND** tests pass without errors
- **AND** coverage report is generated in `web/coverage/`

#### Scenario: Backend tests achieve 70% coverage

- **WHEN** a developer runs `uv run pytest --cov`
- **THEN** pytest runs all unit and integration tests for the Flask API
- **AND** coverage report shows ≥70% for all metrics
- **AND** tests pass without errors
- **AND** coverage reports are generated in HTML and XML formats

#### Scenario: Shared package tests achieve 70% coverage

- **WHEN** a developer runs `pnpm --filter "@salk-hpi/bloom-js" run test:coverage`
- **THEN** Jest runs all tests for the bloom-js package
- **AND** coverage report shows ≥70% for all metrics
- **AND** tests pass without errors

### Requirement: Pre-commit Quality Gates

The project SHALL use pre-commit hooks to enforce code quality standards before commits are created, providing fast feedback to developers.

#### Scenario: Pre-commit hooks run on changed files

- **WHEN** a developer runs `git commit`
- **THEN** pre-commit hooks run only on staged files (fast)
- **AND** trailing whitespace is removed
- **AND** end-of-file newlines are ensured
- **AND** YAML/TOML files are validated
- **AND** large files (>5MB) are blocked with a warning

#### Scenario: Python pre-commit hooks enforce quality

- **WHEN** a developer commits Python files
- **THEN** black formats the code automatically
- **AND** ruff lints the code and auto-fixes issues
- **AND** mypy checks type annotations
- **AND** the commit fails if type errors exist

#### Scenario: JavaScript pre-commit hooks enforce formatting

- **WHEN** a developer commits TypeScript/JavaScript files
- **THEN** prettier formats the code automatically
- **AND** the commit succeeds with formatted code

### Requirement: Continuous Integration Pipeline

The project SHALL run automated CI checks on all pull requests, including linting, testing, building, and coverage reporting.

#### Scenario: PR triggers CI pipeline

- **WHEN** a developer opens a pull request
- **THEN** GitHub Actions CI workflow runs automatically
- **AND** lint, test, and build jobs run in parallel
- **AND** the PR cannot be merged until all checks pass
- **AND** status checks are visible in the PR UI

#### Scenario: Frontend CI checks pass

- **WHEN** the CI pipeline runs for frontend changes
- **THEN** ESLint and Prettier checks pass
- **AND** Jest tests pass with ≥70% coverage
- **AND** Next.js build succeeds
- **AND** coverage report is uploaded to Codecov

#### Scenario: Backend CI checks pass

- **WHEN** the CI pipeline runs for backend changes
- **THEN** black, ruff, and mypy checks pass
- **AND** pytest tests pass with ≥70% coverage
- **AND** Docker image builds successfully
- **AND** coverage report is uploaded to Codecov

#### Scenario: E2E tests run in CI

- **WHEN** all lint, test, and build jobs pass
- **THEN** Playwright E2E tests run against Docker Compose stack
- **AND** critical user flows are tested (authentication, main features)
- **AND** test artifacts (screenshots, videos) are uploaded on failure
- **AND** the pipeline fails if E2E tests fail

### Requirement: Continuous Deployment Pipeline

The project SHALL automatically build and push Docker images to GitHub Container Registry when code is merged to main or when version tags are created.

#### Scenario: Main branch push triggers deployment

- **WHEN** code is merged to the main branch
- **THEN** CD workflow builds Docker images for web and flask
- **AND** images are tagged with `latest` and git commit SHA
- **AND** images are pushed to GHCR (ghcr.io/org/repo)
- **AND** build cache is used for faster subsequent builds

#### Scenario: Version tag triggers release

- **WHEN** a git tag matching `v*` is pushed (e.g., `v1.2.3`)
- **THEN** CD workflow builds Docker images
- **AND** images are tagged with the semantic version (`1.2.3`, `1.2`, `1`)
- **AND** images are pushed to GHCR
- **AND** release artifacts are available for deployment

### Requirement: Coverage Tracking and Reporting

The project SHALL track code coverage over time using Codecov and enforce minimum thresholds on pull requests.

#### Scenario: Coverage report is uploaded to Codecov

- **WHEN** CI pipeline runs tests with coverage
- **THEN** coverage reports are uploaded to Codecov
- **AND** separate flags are used for frontend, backend, and packages
- **AND** Codecov processes the reports and updates statistics

#### Scenario: PR shows coverage change

- **WHEN** a pull request is created
- **THEN** Codecov comments on the PR with coverage changes
- **AND** the comment shows coverage diff (increase/decrease)
- **AND** the comment shows which files have low coverage
- **AND** the PR fails if coverage drops below 70%

### Requirement: Type Annotation Enforcement

The project SHALL require 100% type annotation coverage in Python code with Google-style docstrings for all public functions, classes, and modules.

#### Scenario: Python function has complete type annotations

- **WHEN** a developer writes a Python function
- **THEN** all parameters have type annotations
- **AND** the return type is annotated
- **AND** a Google-style docstring describes the function, arguments, returns, and raises
- **AND** mypy verifies the annotations are correct

#### Scenario: Python module has documentation

- **WHEN** a Python module (file) is created
- **THEN** a module-level docstring describes the module's purpose
- **AND** all public classes have docstrings
- **AND** all public functions have docstrings with argument descriptions
- **AND** examples are provided where helpful

### Requirement: Monorepo Task Orchestration

The project SHALL use Turborepo to orchestrate tasks across packages, enabling parallel execution and caching for improved developer productivity.

#### Scenario: Developer runs lint across all packages

- **WHEN** a developer runs `pnpm run lint`
- **THEN** Turbo runs lint tasks for web, bloom-js, and bloom-fs in parallel
- **AND** build dependencies are satisfied first (packages build before lint)
- **AND** results are cached for unchanged packages
- **AND** the command completes faster than running lint sequentially

#### Scenario: Developer runs tests across all packages

- **WHEN** a developer runs `pnpm run test`
- **THEN** Turbo runs test tasks for all packages in parallel
- **AND** build dependencies are satisfied first
- **AND** tests are not cached (always run fresh)
- **AND** failures in one package don't block others

### Requirement: Developer Documentation

The project SHALL provide comprehensive documentation for the development workflow, including setup, testing, and CI/CD processes.

#### Scenario: New developer onboards successfully

- **WHEN** a new developer joins the project
- **THEN** README or CONTRIBUTING.md explains uv installation and setup
- **AND** documentation covers how to run tests locally
- **AND** documentation explains pre-commit hook setup
- **AND** documentation describes CI/CD pipeline and how to debug failures
- **AND** the developer can set up their environment and run tests within 1 hour

#### Scenario: Developer finds uv command reference

- **WHEN** a developer needs to manage dependencies
- **THEN** a uv cheatsheet is available with common commands
- **AND** examples are provided for adding, removing, and updating packages
- **AND** documentation explains uv.lock and why it's important

### Requirement: Dependency Security Scanning

The project SHALL automatically scan pull requests for dependency vulnerabilities and alert on security issues.

#### Scenario: PR with vulnerable dependency is flagged

- **WHEN** a pull request modifies package.json or pyproject.toml
- **THEN** GitHub Actions dependency review workflow runs
- **AND** dependencies are scanned for known vulnerabilities
- **AND** vulnerabilities with moderate or higher severity fail the check
- **AND** a comment explains which dependencies have issues and how to fix them

#### Scenario: Security advisory is detected

- **WHEN** a new security advisory is published for a dependency
- **THEN** GitHub Dependabot creates an alert
- **AND** team is notified of the vulnerability
- **AND** an automatic update PR is created (if available)
