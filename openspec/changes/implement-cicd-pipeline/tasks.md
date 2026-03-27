# Implementation Tasks

## Phase 1: Foundation (Week 1-2)

### 1.1 Python Package Management Migration

- [ ] 1.1.1 Create flask/pyproject.toml with all current dependencies from requirements.txt
- [ ] 1.1.2 Add dev dependencies (pytest, black, ruff, mypy) to pyproject.toml
- [ ] 1.1.3 Configure tool settings (black, ruff, mypy, pytest, coverage) in pyproject.toml
- [ ] 1.1.4 Create flask/.python-version file with "3.11"
- [ ] 1.1.5 Run `uv init` and `uv sync` to generate uv.lock
- [ ] 1.1.6 Test Flask app runs with `uv run flask run`
- [ ] 1.1.7 Update flask/Dockerfile to use uv
- [ ] 1.1.8 Remove flask/requirements.txt
- [ ] 1.1.9 Update documentation with uv commands

### 1.2 Linting & Formatting Configuration

- [ ] 1.2.1 Create root .eslintrc.js for TypeScript/JavaScript
- [ ] 1.2.2 Create root .prettierrc.json for code formatting
- [ ] 1.2.3 Add linting dependencies to root package.json
- [ ] 1.2.4 Run initial formatting pass on all code: `pnpm run format`
- [ ] 1.2.5 Fix all ESLint errors in web/ and packages/
- [ ] 1.2.6 Run `uv run black .` and `uv run ruff check --fix .` on flask/
- [ ] 1.2.7 Add lint and format scripts to package.json

### 1.3 Pre-commit Hooks Setup

- [ ] 1.3.1 Create .pre-commit-config.yaml with hooks configuration
- [ ] 1.3.2 Install pre-commit: `uv tool install pre-commit` or `pip install pre-commit`
- [ ] 1.3.3 Install git hooks: `pre-commit install`
- [ ] 1.3.4 Test hooks: `pre-commit run --all-files`
- [ ] 1.3.5 Fix any issues found by hooks
- [ ] 1.3.6 Document pre-commit setup in contributing guidelines

## Phase 2: Testing Infrastructure (Week 3-4)

### 2.1 Frontend Testing Setup

- [ ] 2.1.1 Install Jest and React Testing Library: `pnpm add -D @testing-library/react @testing-library/jest-dom jest jest-environment-jsdom`
- [ ] 2.1.2 Create web/jest.config.js with coverage thresholds
- [ ] 2.1.3 Create web/jest.setup.js for test environment setup
- [ ] 2.1.4 Add test scripts to web/package.json
- [ ] 2.1.5 Install Playwright: `pnpm add -D @playwright/test`
- [ ] 2.1.6 Create web/playwright.config.ts
- [ ] 2.1.7 Write sample component tests (2-3 components)
- [ ] 2.1.8 Write sample E2E test (1 critical path)

### 2.2 Backend Testing Setup

- [ ] 2.2.1 pytest and plugins already in pyproject.toml dev dependencies
- [ ] 2.2.2 Create flask/tests/ directory structure
- [ ] 2.2.3 Create flask/tests/conftest.py with shared fixtures
- [ ] 2.2.4 Write sample tests for flask/tests/test_app.py (3-5 endpoint tests)
- [ ] 2.2.5 Write sample tests for flask/tests/test_videoWriter.py (if applicable)
- [ ] 2.2.6 Run tests: `uv run pytest` and verify coverage reporting works
- [ ] 2.2.7 Adjust coverage thresholds if needed

### 2.3 Shared Packages Testing

- [ ] 2.3.1 Create packages/bloom-js/jest.config.js
- [ ] 2.3.2 Create packages/bloom-fs/jest.config.js
- [ ] 2.3.3 Add test scripts to packages/\*/package.json
- [ ] 2.3.4 Write sample tests for bloom-js (2-3 function tests)
- [ ] 2.3.5 Write sample tests for bloom-fs (2-3 function tests)

## Phase 3: CI/CD Pipeline (Week 5)

### 3.1 GitHub Actions CI Workflow

- [ ] 3.1.1 Create .github/workflows/ci.yml
- [ ] 3.1.2 Configure lint-frontend job (ESLint + Prettier)
- [ ] 3.1.3 Configure lint-backend job (black + ruff + mypy)
- [ ] 3.1.4 Configure test-frontend job (Jest with coverage)
- [ ] 3.1.5 Configure test-backend job (pytest with coverage)
- [ ] 3.1.6 Configure test-packages job (matrix for bloom-js, bloom-fs)
- [ ] 3.1.7 Configure build-frontend job (Next.js build)
- [ ] 3.1.8 Configure build-backend job (Docker image build)
- [ ] 3.1.9 Configure e2e-tests job (Playwright with Docker Compose)
- [ ] 3.1.10 Test CI workflow on a test branch

### 3.2 GitHub Actions CD Workflow

- [ ] 3.2.1 Create .github/workflows/cd.yml
- [ ] 3.2.2 Configure Docker build and push for web image
- [ ] 3.2.3 Configure Docker build and push for flask image
- [ ] 3.2.4 Set up GHCR authentication and permissions
- [ ] 3.2.5 Test CD workflow with a tag push

### 3.3 Dependency Review Workflow

- [ ] 3.3.1 Create .github/workflows/dependency-review.yml
- [ ] 3.3.2 Configure dependency scanning on PRs
- [ ] 3.3.3 Test with a PR that modifies dependencies

### 3.4 Coverage Integration

- [ ] 3.4.1 Sign up for Codecov account (https://codecov.io)
- [ ] 3.4.2 Add repository to Codecov
- [ ] 3.4.3 Set CODECOV_TOKEN in GitHub secrets (if private repo)
- [ ] 3.4.4 Create .codecov.yml with coverage thresholds
- [ ] 3.4.5 Verify coverage uploads work in CI
- [ ] 3.4.6 Add coverage badges to README

## Phase 4: Turbo Configuration (Week 5)

### 4.1 Update Turbo Tasks

- [ ] 4.1.1 Add "lint" task to turbo.json
- [ ] 4.1.2 Add "lint:fix" task to turbo.json
- [ ] 4.1.3 Add "test" task to turbo.json
- [ ] 4.1.4 Add "test:coverage" task to turbo.json
- [ ] 4.1.5 Add "test:watch" task to turbo.json
- [ ] 4.1.6 Test turbo tasks: `pnpm run lint`, `pnpm run test`

## Phase 5: Type Annotations & Documentation (Week 6)

### 5.1 Add Python Type Annotations

- [ ] 5.1.1 Add type annotations to flask/app.py (all functions)
- [ ] 5.1.2 Add type annotations to flask/config.py
- [ ] 5.1.3 Add type annotations to flask/videoWriter.py
- [ ] 5.1.4 Add Google-style docstrings to all public functions
- [ ] 5.1.5 Run mypy and fix all type errors: `uv run mypy .`
- [ ] 5.1.6 Verify 100% type coverage

### 5.2 Documentation Updates

- [ ] 5.2.1 Update main README with CI/CD badges
- [ ] 5.2.2 Document uv commands in README or CONTRIBUTING.md
- [ ] 5.2.3 Document testing practices (how to write tests, run tests)
- [ ] 5.2.4 Document pre-commit hook setup
- [ ] 5.2.5 Create uv cheatsheet (common commands)
- [ ] 5.2.6 Update CONTRIBUTING.md with new workflow

## Phase 6: Test Writing (Week 6-8)

### 6.1 Frontend Test Coverage

- [ ] 6.1.1 Write tests for all components in web/components/
- [ ] 6.1.2 Write tests for app/ routes and page components
- [ ] 6.1.3 Write integration tests for critical user flows
- [ ] 6.1.4 Achieve 70% coverage threshold for web package
- [ ] 6.1.5 Write E2E tests for authentication flow
- [ ] 6.1.6 Write E2E tests for main application features

### 6.2 Backend Test Coverage

- [ ] 6.2.1 Write tests for all Flask routes in app.py
- [ ] 6.2.2 Write tests for videoWriter functionality
- [ ] 6.2.3 Write tests for config validation
- [ ] 6.2.4 Write tests for S3 integration (mocked)
- [ ] 6.2.5 Write tests for Supabase integration (mocked)
- [ ] 6.2.6 Achieve 70% coverage threshold for flask package

### 6.3 Shared Packages Test Coverage

- [ ] 6.3.1 Write comprehensive tests for bloom-js package
- [ ] 6.3.2 Write comprehensive tests for bloom-fs package
- [ ] 6.3.3 Achieve 70% coverage threshold for both packages

## Phase 7: Team Training & Rollout (Week 9)

### 7.1 Developer Training

- [ ] 7.1.1 Schedule team walkthrough session (1-2 hours)
- [ ] 7.1.2 Present uv best practices and common commands
- [ ] 7.1.3 Demonstrate pre-commit hook workflow
- [ ] 7.1.4 Show how to write and run tests
- [ ] 7.1.5 Explain CI/CD pipeline and how to read failures
- [ ] 7.1.6 Q&A session for team questions

### 7.2 Rollout & Monitoring

- [ ] 7.2.1 Merge CI/CD implementation to main branch
- [ ] 7.2.2 Monitor first few PRs for CI/CD issues
- [ ] 7.2.3 Provide support for developers encountering issues
- [ ] 7.2.4 Collect feedback and adjust processes as needed
- [ ] 7.2.5 Schedule retrospective after 2 weeks

## Success Metrics

### Technical Metrics (tracked after rollout)

- [ ] Code coverage ≥70% across all components
- [ ] CI build time <15 minutes for full pipeline
- [ ] Zero linting errors in main branch
- [ ] Test reliability: <1% flaky test rate
- [ ] 100% type annotations in Python code

### Process Metrics

- [ ] PR review time reduced by automated checks
- [ ] 50% of bugs caught in CI before production (track over 3 months)
- [ ] Deployment frequency increased by 3x

### Quality Metrics

- [ ] Production incidents reduced by 40% (track over 6 months)
- [ ] Code review feedback on style/formatting reduced by 90%
- [ ] Developer confidence survey shows >80% confidence in deployments
