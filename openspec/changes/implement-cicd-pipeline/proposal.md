# Implement CI/CD Pipeline with Modern Tooling

## Why

The Bloom project currently lacks automated quality assurance and deployment processes, creating several critical gaps:

- **No automated testing**: No unit tests, integration tests, or E2E tests to catch bugs before production
- **No code quality enforcement**: Missing linting (ESLint, Prettier, black, ruff, mypy) leading to inconsistent code style
- **No coverage tracking**: No visibility into test coverage or enforcement of minimum thresholds
- **No CI/CD pipeline**: Manual testing and deployment processes are error-prone and slow
- **No pre-commit hooks**: Developers can commit code with formatting/linting issues
- **Inefficient Python package management**: Using pip/requirements.txt instead of modern uv with lockfiles
- **Package manager inconsistency**: pnpm specified but npm used in practice

These gaps lead to:
- Production bugs that could be caught earlier
- Inconsistent code quality and style
- Slower code reviews (reviewers must check style manually)
- Deployment anxiety and manual verification
- Slower dependency installation (pip vs uv is 10-100x difference)
- Dependency version conflicts without lockfiles

## What Changes

### 1. Python Package Management Migration
- **BREAKING**: Migrate from pip/requirements.txt to uv with pyproject.toml
- Add pyproject.toml with PEP 621 project metadata
- Configure black, ruff, mypy in pyproject.toml
- Generate uv.lock lockfile for reproducible installs
- Update Flask Dockerfile to use uv
- Update development workflows to use uv commands

### 2. Testing Framework Implementation
- **Frontend (Next.js/React)**: Jest + React Testing Library + Playwright
- **Backend (Flask/Python)**: pytest + pytest-cov + pytest-flask + pytest-mock
- **Shared Packages**: Jest with TypeScript support
- **Coverage requirements**: 70% minimum across all components
- **Type annotations**: 100% type coverage in Python with Google-style docstrings

### 3. Code Quality & Linting
- **TypeScript/JavaScript**: ESLint + Prettier with Next.js config
- **Python**: black (formatting) + ruff (linting) + mypy (type checking)
- **Configuration files**: .eslintrc.js, .prettierrc.json, pyproject.toml
- **Enforcement**: Pre-commit hooks and CI checks

### 4. Pre-commit Hooks
- Install pre-commit framework
- Configure hooks for trailing whitespace, YAML validation, large files
- Add Python hooks: black, ruff, mypy
- Add JavaScript hooks: prettier
- Automatic formatting on commit

### 5. CI/CD Pipeline (GitHub Actions)
- **CI Workflow**: Lint, test, build for all components in parallel
- **CD Workflow**: Build and push Docker images to GHCR on main branch/tags
- **Dependency Review**: Automated security scanning on PRs
- **E2E Testing**: Playwright tests against Docker Compose stack
- **Coverage Reporting**: Codecov integration with 70% threshold

### 6. Docker Integration
- Update Flask Dockerfile to use uv for faster, reproducible builds
- Multi-stage builds (base, dev) for optimized images
- Cache GitHub Actions runners for faster CI

### 7. Turbo Configuration
- Add test, lint, and coverage tasks to turbo.json
- Configure proper task dependencies and caching
- Enable parallel execution across workspace packages

## Impact

- **Affected specs**: `development-workflow` (new capability spec)
- **Affected code**:
  - **New files**:
    - `flask/pyproject.toml` - Python project configuration
    - `flask/uv.lock` - Python dependency lockfile
    - `flask/.python-version` - Python version specification
    - `.eslintrc.js` - TypeScript/JavaScript linting config
    - `.prettierrc.json` - Code formatting config
    - `.pre-commit-config.yaml` - Git hook configuration
    - `.github/workflows/ci.yml` - CI pipeline
    - `.github/workflows/cd.yml` - CD pipeline
    - `.github/workflows/dependency-review.yml` - Security scanning
    - `.codecov.yml` - Coverage reporting config
    - `flask/tests/` - Python test directory structure
    - `web/jest.config.js` - Frontend test configuration
    - `web/playwright.config.ts` - E2E test configuration
  - **Modified files**:
    - `flask/Dockerfile` - Add uv support
    - `turbo.json` - Add test/lint tasks
    - `web/package.json` - Add test dependencies and scripts
    - `flask/app.py` - Add type annotations
    - `flask/videoWriter.py` - Add type annotations and docstrings
  - **Removed files**:
    - `flask/requirements.txt` - Replaced by pyproject.toml

- **Breaking changes**:
  - Python development workflow changes from pip to uv
  - Developers must run `uv sync` instead of `pip install`
  - Docker build process changes (backward compatible in runtime)
  - Pre-commit hooks will block commits that fail checks
  - CI will fail PRs that don't meet coverage thresholds

- **Dependencies**:
  - uv (Python package manager)
  - pre-commit framework
  - GitHub Actions runners (free tier sufficient)
  - Codecov account (free for open source)

- **Migration required**:
  - Team training on uv commands (1-2 hours)
  - Initial test writing effort (3 weeks, 2 developers 50% time)
  - Existing code formatting pass (1-2 days)
  - Documentation updates (1 week)

- **Benefits**:
  - 10-100x faster Python dependency installation
  - Automated bug detection before production (estimated 50% of bugs caught in CI)
  - Consistent code style (90% reduction in style-related review comments)
  - Deployment confidence (automated testing and validation)
  - Faster code reviews (automated checks handle formatting/linting)
  - 4-5x ROI in first year
