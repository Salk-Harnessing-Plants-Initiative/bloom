# CI/CD Implementation Proposal

## Executive Summary

This proposal outlines a comprehensive CI/CD framework for the Bloom project, addressing current gaps in testing, linting, coverage requirements, and automated deployment. The implementation will establish a robust quality assurance pipeline for all project components while maintaining developer productivity.

## Current State Analysis

### Existing Infrastructure
- **Monorepo**: Turborepo-managed workspace with web app, Flask API, and shared packages
- **Build System**: Turbo with basic build/dev tasks configured
- **Deployment**: Docker Compose-based deployment (dev/prod)
- **Version Control**: Git with main branch workflow

### Identified Gaps
1. **No Testing Framework**: No unit tests, integration tests, or E2E tests
2. **No Linting**: Missing ESLint, Prettier, Python linters (black, ruff, mypy)
3. **No Code Coverage**: No coverage tracking or enforcement
4. **No CI/CD Pipeline**: No automated testing or deployment
5. **No Pre-commit Hooks**: No local quality gates
6. **Package Manager Inconsistency**: pnpm specified but npm used in practice
7. **Python Package Management**: Using pip/requirements.txt instead of modern uv with pyproject.toml

## Proposed Solution

### 1. Python Package Management with uv

#### 1.1 Migration to uv

**Why uv?**
- **Fast**: 10-100x faster than pip
- **Reliable**: Consistent dependency resolution with lockfile
- **Modern**: Built-in support for PEP 621 (pyproject.toml)
- **Compatible**: Drop-in replacement for pip/pip-tools/virtualenv
- **Simple**: Single tool for package management, environment creation, and project building

**Project Structure**:
```
flask/
├── pyproject.toml           # Project metadata and dependencies
├── uv.lock                  # Lockfile for reproducible installs
├── .python-version          # Python version specification
├── src/                     # Source code (optional restructure)
│   └── app/
│       ├── __init__.py
│       ├── app.py
│       ├── config.py
│       └── videoWriter.py
└── tests/                   # Test directory
    ├── __init__.py
    └── test_*.py
```

**Configuration** (flask/pyproject.toml):
```toml
[project]
name = "bloom-flask"
version = "0.1.0"
description = "Flask API for Bloom - video generation and S3 access"
requires-python = ">=3.11"
dependencies = [
    "flask>=3.1.2",
    "boto3>=1.40.59",
    "numpy>=2.3.4",
    "pillow>=12.0.0",
    "pyjwt>=2.10.1",
    "python-dotenv>=1.2.1",
    "supabase>=2.22.2",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.3",
    "pytest-cov>=4.1.0",
    "pytest-flask>=1.3.0",
    "pytest-mock>=3.12.0",
    "responses>=0.24.1",
    "faker>=20.1.0",
    "black>=24.1.0",
    "ruff>=0.1.9",
    "mypy>=1.8.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.black]
line-length = 88
target-version = ["py311"]
include = '\.pyi?$'
extend-exclude = '''
/(
    \.git
  | \.venv
  | __pycache__
  | \.pytest_cache
)/
'''

[tool.ruff]
line-length = 88
target-version = "py311"
select = [
    "E",   # pycodestyle errors
    "W",   # pycodestyle warnings
    "F",   # pyflakes
    "I",   # isort
    "B",   # flake8-bugbear
    "C4",  # flake8-comprehensions
    "UP",  # pyupgrade
]
ignore = [
    "E501",  # line too long (handled by black)
]

[tool.ruff.pydocstyle]
convention = "google"

[tool.mypy]
python_version = "3.11"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
check_untyped_defs = true
ignore_missing_imports = false
strict_optional = true
warn_redundant_casts = true
warn_unused_ignores = true
warn_no_return = true

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = [
    "--cov=.",
    "--cov-report=term-missing",
    "--cov-report=html",
    "--cov-report=xml",
    "--cov-fail-under=70",
    "-v",
]

[tool.coverage.run]
source = ["."]
omit = [
    "*/tests/*",
    "*/__pycache__/*",
    "*/.venv/*",
]

[tool.coverage.report]
precision = 2
show_missing = true
skip_covered = false
```

**Python Version** (flask/.python-version):
```
3.11
```

**uv Commands**:
```bash
# Initialize new project (if starting fresh)
uv init

# Sync dependencies (install from lockfile)
uv sync

# Add a dependency
uv add flask

# Add a dev dependency
uv add --dev pytest

# Remove a dependency
uv remove flask

# Update dependencies
uv lock --upgrade

# Run command in virtual environment
uv run pytest

# Run Flask app
uv run flask run

# Activate virtual environment
source .venv/bin/activate  # uv creates .venv automatically
```

### 2. Testing Framework

#### 2.1 Frontend Testing (Next.js/TypeScript/React)

**Framework Selection**:
- **Unit/Integration Tests**: Jest + React Testing Library
- **E2E Tests**: Playwright
- **Component Testing**: React Testing Library

**Configuration**:
```json
// web/jest.config.js
module.exports = {
  preset: 'next/jest',
  testEnvironment: 'jest-environment-jsdom',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/$1',
  },
  collectCoverageFrom: [
    'app/**/*.{js,jsx,ts,tsx}',
    'components/**/*.{js,jsx,ts,tsx}',
    '!**/*.d.ts',
    '!**/node_modules/**',
  ],
  coverageThresholds: {
    global: {
      branches: 70,
      functions: 70,
      lines: 70,
      statements: 70,
    },
  },
}
```

**Required Dependencies**:
```json
{
  "devDependencies": {
    "@testing-library/react": "^14.0.0",
    "@testing-library/jest-dom": "^6.1.5",
    "@testing-library/user-event": "^14.5.1",
    "@playwright/test": "^1.40.0",
    "jest": "^29.7.0",
    "jest-environment-jsdom": "^29.7.0"
  }
}
```

**Scripts** (add to web/package.json):
```json
{
  "scripts": {
    "test": "jest",
    "test:watch": "jest --watch",
    "test:coverage": "jest --coverage",
    "test:e2e": "playwright test",
    "test:e2e:ui": "playwright test --ui"
  }
}
```

#### 2.2 Backend Testing (Flask/Python with uv)

**Framework Selection**:
- **Unit/Integration Tests**: pytest + pytest-cov
- **API Testing**: pytest-flask
- **Mocking**: pytest-mock + responses

**Test Directory Structure**:
```
flask/
├── tests/
│   ├── __init__.py
│   ├── conftest.py          # Shared fixtures
│   ├── test_app.py          # App route tests
│   ├── test_videoWriter.py  # VideoWriter unit tests
│   └── test_config.py       # Config validation tests
```

**Example Test with Type Annotations and Google Docstrings**:
```python
"""Tests for the Flask application routes.

This module contains unit tests for all API endpoints in the Flask app,
ensuring correct behavior and error handling.
"""

import pytest
from flask import Flask
from flask.testing import FlaskClient


@pytest.fixture
def client() -> FlaskClient:
    """Create a test client for the Flask app.

    Returns:
        FlaskClient: A test client instance for making requests.
    """
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_index_route(client: FlaskClient) -> None:
    """Test the index route returns correct message.

    Args:
        client: The Flask test client fixture.
    """
    response = client.get("/")
    assert response.status_code == 200
    data = response.get_json()
    assert data["message"] == "Flask app is running!"


def test_supabase_connection(client: FlaskClient) -> None:
    """Test Supabase connection endpoint returns valid data.

    Args:
        client: The Flask test client fixture.
    """
    response = client.get("/supabaseconnection")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
```

**Running Tests with uv**:
```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov

# Run specific test file
uv run pytest tests/test_app.py

# Run in watch mode (requires pytest-watch)
uv run pytest-watch
```

#### 2.3 Shared Packages Testing

**bloom-js & bloom-fs**:
- Use Jest with TypeScript
- Individual test suites per package
- Coverage threshold: 70%

**Configuration** (packages/*/jest.config.js):
```javascript
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/src'],
  testMatch: ['**/__tests__/**/*.ts', '**/?(*.)+(spec|test).ts'],
  collectCoverageFrom: ['src/**/*.ts', '!src/**/*.d.ts'],
  coverageThresholds: {
    global: {
      branches: 70,
      functions: 70,
      lines: 70,
      statements: 70,
    },
  },
}
```

**Required Dependencies** (add to packages/*/package.json):
```json
{
  "devDependencies": {
    "@types/jest": "^29.5.11",
    "jest": "^29.7.0",
    "ts-jest": "^29.1.1"
  },
  "scripts": {
    "test": "jest",
    "test:watch": "jest --watch",
    "test:coverage": "jest --coverage"
  }
}
```

### 3. Code Quality & Linting

#### 3.1 TypeScript/JavaScript Linting

**ESLint Configuration** (root .eslintrc.js):
```javascript
module.exports = {
  root: true,
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react/recommended',
    'plugin:react-hooks/recommended',
    'next/core-web-vitals',
    'prettier', // Must be last
  ],
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 2021,
    sourceType: 'module',
    ecmaFeatures: {
      jsx: true,
    },
  },
  plugins: ['@typescript-eslint', 'react', 'react-hooks'],
  rules: {
    '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    '@typescript-eslint/explicit-module-boundary-types': 'off',
    '@typescript-eslint/no-explicit-any': 'warn',
    'react/react-in-jsx-scope': 'off',
    'react/prop-types': 'off',
  },
  settings: {
    react: {
      version: 'detect',
    },
  },
}
```

**Prettier Configuration** (root .prettierrc.json):
```json
{
  "semi": false,
  "singleQuote": true,
  "tabWidth": 2,
  "trailingComma": "es5",
  "printWidth": 100,
  "arrowParens": "always"
}
```

**Required Dependencies** (root package.json):
```json
{
  "devDependencies": {
    "@typescript-eslint/eslint-plugin": "^6.15.0",
    "@typescript-eslint/parser": "^6.15.0",
    "eslint": "^8.56.0",
    "eslint-config-next": "^16.0.0",
    "eslint-config-prettier": "^9.1.0",
    "eslint-plugin-react": "^7.33.2",
    "eslint-plugin-react-hooks": "^4.6.0",
    "prettier": "^3.1.1"
  }
}
```

**Scripts** (root package.json):
```json
{
  "scripts": {
    "lint": "turbo run lint",
    "lint:fix": "turbo run lint:fix",
    "format": "prettier --write \"**/*.{js,jsx,ts,tsx,json,md}\"",
    "format:check": "prettier --check \"**/*.{js,jsx,ts,tsx,json,md}\""
  }
}
```

#### 3.2 Python Linting & Formatting (with uv)

**Tools**:
- **black**: Code formatting (line length 88, Google docstrings)
- **ruff**: Fast linter (replaces flake8, isort, and more)
- **mypy**: Static type checking with strict annotations

**All configuration is in pyproject.toml** (shown in section 1.1)

**Running Linters with uv**:
```bash
# Format code with black
uv run black .

# Check formatting without changes
uv run black --check .

# Run ruff linter
uv run ruff check .

# Fix auto-fixable issues
uv run ruff check --fix .

# Run type checker
uv run mypy .

# Run all checks
uv run black --check . && uv run ruff check . && uv run mypy .
```

**Makefile Commands**:
```makefile
.PHONY: lint-python
lint-python:
	@echo "🔍 Checking Python code formatting..."
	cd flask && uv run black --check .
	@echo "🔍 Running ruff linter..."
	cd flask && uv run ruff check .
	@echo "🔍 Running type checker..."
	cd flask && uv run mypy .

.PHONY: format-python
format-python:
	@echo "✨ Formatting Python code with black..."
	cd flask && uv run black .
	@echo "✨ Fixing auto-fixable ruff issues..."
	cd flask && uv run ruff check --fix .

.PHONY: test-python
test-python:
	@echo "🧪 Running Python tests..."
	cd flask && uv run pytest
```

### 4. Pre-commit Hooks

**Configuration** (.pre-commit-config.yaml):

```yaml
# See https://pre-commit.com for more information
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
      - id: check-merge-conflict
      - id: check-toml

  - repo: https://github.com/psf/black
    rev: 24.1.1
    hooks:
      - id: black
        files: ^flask/
        language_version: python3.11

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.1.9
    hooks:
      - id: ruff
        files: ^flask/
        args: [--fix]
      - id: ruff-format
        files: ^flask/

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.8.0
    hooks:
      - id: mypy
        files: ^flask/
        additional_dependencies: [types-all]

  - repo: https://github.com/pre-commit/mirrors-prettier
    rev: v3.1.0
    hooks:
      - id: prettier
        files: \.(js|jsx|ts|tsx|json|md)$
        exclude: ^(web/package-lock\.json|pnpm-lock\.yaml)$
```

**Installation**:
```bash
# Install pre-commit (can use uv)
uv tool install pre-commit

# Or use pip/pipx
pip install pre-commit

# Install the git hooks
pre-commit install

# Run manually on all files
pre-commit run --all-files
```

### 5. CI/CD Pipeline (GitHub Actions)

#### 5.1 CI Workflow

**File**: `.github/workflows/ci.yml`

```yaml
name: CI

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

env:
  NODE_VERSION: '20'
  PYTHON_VERSION: '3.11'
  PNPM_VERSION: '10.19.0'
  UV_VERSION: '0.1.9'

jobs:
  # Job 1: Lint TypeScript/JavaScript
  lint-frontend:
    name: Lint Frontend (TypeScript/JavaScript)
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}

      - name: Setup pnpm
        uses: pnpm/action-setup@v3
        with:
          version: ${{ env.PNPM_VERSION }}

      - name: Install dependencies
        run: pnpm install --frozen-lockfile

      - name: Run ESLint
        run: pnpm run lint

      - name: Check formatting with Prettier
        run: pnpm run format:check

  # Job 2: Lint Python
  lint-backend:
    name: Lint Backend (Python)
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: flask
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}

      - name: Install uv
        uses: astral-sh/setup-uv@v1
        with:
          version: ${{ env.UV_VERSION }}

      - name: Install dependencies
        run: uv sync

      - name: Check formatting with black
        run: uv run black --check .

      - name: Run ruff linter
        run: uv run ruff check .

      - name: Run mypy type checker
        run: uv run mypy .

  # Job 3: Test Frontend
  test-frontend:
    name: Test Frontend
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}

      - name: Setup pnpm
        uses: pnpm/action-setup@v3
        with:
          version: ${{ env.PNPM_VERSION }}

      - name: Install dependencies
        run: pnpm install --frozen-lockfile

      - name: Build shared packages
        run: pnpm --filter "@salk-hpi/*" run build

      - name: Run unit tests
        run: pnpm --filter @bloom/web run test:coverage

      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v4
        with:
          files: ./web/coverage/coverage-final.json
          flags: frontend
          name: frontend-coverage

  # Job 4: Test Backend
  test-backend:
    name: Test Backend
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: flask
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}

      - name: Install uv
        uses: astral-sh/setup-uv@v1
        with:
          version: ${{ env.UV_VERSION }}

      - name: Install dependencies
        run: uv sync

      - name: Run pytest with coverage
        run: uv run pytest --cov --cov-report=xml

      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v4
        with:
          files: ./flask/coverage.xml
          flags: backend
          name: backend-coverage

  # Job 5: Test Shared Packages
  test-packages:
    name: Test Shared Packages
    runs-on: ubuntu-latest
    strategy:
      matrix:
        package: [bloom-js, bloom-fs]
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}

      - name: Setup pnpm
        uses: pnpm/action-setup@v3
        with:
          version: ${{ env.PNPM_VERSION }}

      - name: Install dependencies
        run: pnpm install --frozen-lockfile

      - name: Run tests
        run: pnpm --filter "@salk-hpi/${{ matrix.package }}" run test:coverage

      - name: Upload coverage
        uses: codecov/codecov-action@v4
        with:
          files: ./packages/${{ matrix.package }}/coverage/coverage-final.json
          flags: packages-${{ matrix.package }}
          name: ${{ matrix.package }}-coverage

  # Job 6: Build Frontend
  build-frontend:
    name: Build Frontend
    runs-on: ubuntu-latest
    needs: [lint-frontend, test-frontend]
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}

      - name: Setup pnpm
        uses: pnpm/action-setup@v3
        with:
          version: ${{ env.PNPM_VERSION }}

      - name: Install dependencies
        run: pnpm install --frozen-lockfile

      - name: Build packages
        run: pnpm --filter "@salk-hpi/*" run build

      - name: Build web application
        run: pnpm --filter @bloom/web run build
        env:
          NEXT_PUBLIC_SUPABASE_URL: ${{ secrets.NEXT_PUBLIC_SUPABASE_URL }}
          NEXT_PUBLIC_SUPABASE_ANON_KEY: ${{ secrets.NEXT_PUBLIC_SUPABASE_ANON_KEY }}

  # Job 7: Build Backend (Docker with uv)
  build-backend:
    name: Build Backend Docker Image
    runs-on: ubuntu-latest
    needs: [lint-backend, test-backend]
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build Flask Docker image
        uses: docker/build-push-action@v5
        with:
          context: ./flask
          file: ./flask/Dockerfile
          push: false
          tags: bloom-flask:test
          cache-from: type=gha
          cache-to: type=gha,mode=max

  # Job 8: E2E Tests
  e2e-tests:
    name: E2E Tests
    runs-on: ubuntu-latest
    needs: [build-frontend, build-backend]
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}

      - name: Setup pnpm
        uses: pnpm/action-setup@v3
        with:
          version: ${{ env.PNPM_VERSION }}

      - name: Install dependencies
        run: pnpm install --frozen-lockfile

      - name: Install Playwright browsers
        run: pnpm exec playwright install --with-deps

      - name: Start Docker services
        run: |
          cp .env.dev.example .env.dev
          docker compose -f docker-compose.dev.yml up -d
          # Wait for services to be healthy
          sleep 30

      - name: Run Playwright tests
        run: pnpm --filter @bloom/web run test:e2e

      - name: Upload Playwright report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-report
          path: web/playwright-report/
          retention-days: 7

      - name: Stop Docker services
        if: always()
        run: docker compose -f docker-compose.dev.yml down
```

#### 5.2 CD Workflow (Deployment)

**File**: `.github/workflows/cd.yml`

```yaml
name: CD (Deployment)

on:
  push:
    branches: [main]
    tags:
      - 'v*'

env:
  REGISTRY: ghcr.io
  IMAGE_NAME_WEB: ${{ github.repository }}/web
  IMAGE_NAME_FLASK: ${{ github.repository }}/flask

jobs:
  deploy:
    name: Build and Deploy
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')
    permissions:
      contents: read
      packages: write

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Log in to Container Registry
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata for Web
        id: meta-web
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME_WEB }}
          tags: |
            type=ref,event=branch
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=sha

      - name: Build and push Web image
        uses: docker/build-push-action@v5
        with:
          context: .
          file: ./web/Dockerfile.bloom-web.prod
          push: true
          tags: ${{ steps.meta-web.outputs.tags }}
          labels: ${{ steps.meta-web.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: Extract metadata for Flask
        id: meta-flask
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME_FLASK }}
          tags: |
            type=ref,event=branch
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=sha

      - name: Build and push Flask image
        uses: docker/build-push-action@v5
        with:
          context: ./flask
          file: ./flask/Dockerfile
          push: true
          tags: ${{ steps.meta-flask.outputs.tags }}
          labels: ${{ steps.meta-flask.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

      # Add deployment steps here (e.g., update Kubernetes, trigger webhook, etc.)
      # - name: Deploy to production
      #   run: |
      #     # Your deployment commands
```

#### 5.3 Dependency Update Workflow

**File**: `.github/workflows/dependency-review.yml`

```yaml
name: Dependency Review

on:
  pull_request:
    branches: [main]

permissions:
  contents: read
  pull-requests: write

jobs:
  dependency-review:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Dependency Review
        uses: actions/dependency-review-action@v4
        with:
          fail-on-severity: moderate
```

### 6. Docker Integration with uv

**Updated Dockerfile for Flask** (flask/Dockerfile):

```dockerfile
# syntax=docker/dockerfile:1

FROM python:3.11-slim as base

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml uv.lock ./

# Install dependencies using uv
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

# Expose port
EXPOSE 5002

# Run the application
CMD ["uv", "run", "flask", "run", "--host=0.0.0.0", "--port=5002"]


# Development stage
FROM base as dev

# Install dev dependencies
RUN uv sync --frozen

# Run in development mode
CMD ["uv", "run", "flask", "run", "--host=0.0.0.0", "--port=5002", "--debug"]
```

### 7. Coverage Requirements

#### 7.1 Coverage Thresholds

**Minimum Coverage Requirements**:
- **Frontend (web)**: 70% across branches, functions, lines, statements
- **Backend (flask)**: 70% across all metrics
- **Shared Packages**: 70% across all metrics

#### 7.2 Coverage Reporting

**Integration with Codecov**:
1. Sign up for Codecov at https://codecov.io
2. Add repository to Codecov
3. Set `CODECOV_TOKEN` in GitHub secrets
4. Coverage reports automatically uploaded via CI workflow

**Coverage Configuration** (.codecov.yml):
```yaml
coverage:
  status:
    project:
      default:
        target: 70%
        threshold: 1%
    patch:
      default:
        target: 70%

comment:
  layout: "reach, diff, flags, files"
  behavior: default

ignore:
  - "**/*.test.ts"
  - "**/*.test.tsx"
  - "**/*.spec.ts"
  - "**/test_*.py"
  - "**/__tests__/**"
  - "**/tests/**"
```

### 8. Turbo Configuration Updates

**Updated turbo.json**:
```json
{
  "$schema": "https://turbo.build/schema.json",
  "tasks": {
    "build": {
      "dependsOn": ["^build"],
      "outputs": [".next/**", "dist/**"]
    },
    "dev": {
      "cache": false,
      "persistent": true
    },
    "lint": {
      "dependsOn": ["^build"],
      "outputs": []
    },
    "lint:fix": {
      "dependsOn": ["^build"],
      "cache": false
    },
    "test": {
      "dependsOn": ["^build"],
      "outputs": ["coverage/**"],
      "cache": false
    },
    "test:coverage": {
      "dependsOn": ["^build"],
      "outputs": ["coverage/**"]
    },
    "test:watch": {
      "cache": false,
      "persistent": true
    }
  }
}
```

## Implementation Roadmap

### Phase 1: Foundation (Week 1-2)
1. **Standardize Package Manager**
   - Migrate all npm usage to pnpm
   - Update Dockerfiles and Makefile
   - Update documentation

2. **Migrate Python to uv**
   - Create pyproject.toml with all dependencies
   - Run `uv init` and `uv add` for all packages
   - Generate uv.lock file
   - Update Dockerfile to use uv
   - Test local development with uv

3. **Setup Linting & Formatting**
   - Install ESLint, Prettier (frontend)
   - Configure black, ruff, mypy in pyproject.toml
   - Run initial formatting pass
   - Fix all linting errors

4. **Configure Pre-commit Hooks**
   - Install pre-commit framework
   - Configure hooks for all languages
   - Test locally

### Phase 2: Testing Infrastructure (Week 3-4)
1. **Frontend Testing Setup**
   - Install Jest, React Testing Library, Playwright
   - Configure test environments
   - Create test utilities and fixtures
   - Write sample tests for 2-3 components

2. **Backend Testing Setup**
   - Add pytest and plugins to pyproject.toml
   - Create test directory structure
   - Write fixtures and test utilities
   - Write sample tests for 2-3 endpoints

3. **Package Testing Setup**
   - Configure Jest for bloom-js and bloom-fs
   - Write sample tests

### Phase 3: CI/CD Pipeline (Week 5)
1. **GitHub Actions Setup**
   - Create CI workflow with uv support
   - Create CD workflow
   - Create dependency review workflow
   - Test all workflows

2. **Coverage Integration**
   - Setup Codecov account
   - Configure coverage thresholds
   - Add coverage badges to README

### Phase 4: Test Coverage (Week 6-8)
1. **Write Tests**
   - Frontend components: Achieve 70% coverage
   - Backend endpoints: Achieve 70% coverage
   - Shared packages: Achieve 70% coverage

2. **E2E Tests**
   - Write critical path E2E tests
   - Test user authentication flow
   - Test video generation workflow

### Phase 5: Documentation & Training (Week 9)
1. **Update Documentation**
   - Document testing practices
   - Document CI/CD workflows
   - Document uv usage
   - Update contribution guidelines

2. **Developer Training**
   - Team walkthrough of new processes
   - uv best practices session
   - Pair programming sessions for test writing
   - Q&A session

## Success Metrics

### Technical Metrics
- **Code Coverage**: ≥70% across all components
- **CI Build Time**: <15 minutes for full pipeline
- **Linting Errors**: 0 errors in main branch
- **Test Reliability**: <1% flaky test rate
- **Type Coverage**: 100% type annotations in Python code

### Process Metrics
- **PR Review Time**: Reduced by automated checks
- **Bug Detection**: 50% of bugs caught in CI before production
- **Deployment Frequency**: Increase by 3x with automated CD

### Quality Metrics
- **Production Incidents**: Reduce by 40%
- **Code Review Feedback**: Reduce style/formatting comments by 90%
- **Developer Confidence**: Survey shows >80% confidence in deployments

## Required Resources

### Time Commitment
- **Initial Setup**: 2 weeks (1 senior developer full-time)
- **Test Writing**: 3 weeks (2 developers, 50% time)
- **Documentation**: 1 week (1 developer, 50% time)
- **Total**: ~6 developer-weeks

### Infrastructure
- **GitHub Actions**: Free tier sufficient (2000 minutes/month)
- **Codecov**: Free for open source, $10/month for private repos
- **Additional Compute**: None required

### Training
- **Team Training Sessions**: 4 hours
- **Documentation Review**: 2 hours per developer
- **Ongoing Support**: 2-4 hours/week for first month

## Risks & Mitigation

### Risk 1: Developer Resistance
**Mitigation**:
- Involve team early in decision-making
- Provide comprehensive training
- Show quick wins with automated formatting

### Risk 2: Initial Slowdown
**Mitigation**:
- Phase rollout to manage learning curve
- Provide ample documentation
- Pair junior developers with seniors

### Risk 3: CI Pipeline Instability
**Mitigation**:
- Start with basic checks, add complexity gradually
- Monitor and optimize slow tests
- Implement retry logic for flaky tests

### Risk 4: Coverage Requirements Too Strict
**Mitigation**:
- Start with 70%, adjust based on team feedback
- Allow exceptions for legacy code with documentation
- Focus on meaningful tests over coverage numbers

### Risk 5: uv Adoption Learning Curve
**Mitigation**:
- Provide uv cheatsheet and best practices doc
- uv is very similar to pip, minimal learning curve
- Strong community support and documentation

## Cost-Benefit Analysis

### Costs
- **Initial Setup**: 6 developer-weeks (~$12,000-18,000)
- **Infrastructure**: $120/year (Codecov)
- **Ongoing Maintenance**: 2-4 hours/week (~$5,000/year)
- **Total First Year**: ~$17,000-23,000

### Benefits
- **Bug Prevention**: Catch issues before production (estimated 20-30 hours/month saved) = ~$60,000/year
- **Faster Reviews**: Automated checks reduce review time by 30% = ~$15,000/year
- **Reduced Tech Debt**: Consistent code quality reduces refactoring needs = ~$10,000/year
- **Faster Dependency Management**: uv is 10-100x faster than pip = $5,000/year
- **Developer Confidence**: Fewer production incidents and rollbacks = priceless

**ROI**: 4-5x in first year

## Conclusion

This CI/CD implementation proposal provides a comprehensive framework for establishing robust quality assurance processes across the Bloom project using modern tooling. The migration to uv for Python package management, combined with strict type annotations and Google-style docstrings, will significantly improve code quality and developer experience. The phased approach ensures manageable implementation while delivering incremental value.

## Next Steps

1. **Review & Approval**: Team review of this proposal (1 week)
2. **Resource Allocation**: Assign developers and timeline (1 week)
3. **Kickoff Meeting**: Launch Phase 1 implementation
4. **Weekly Check-ins**: Monitor progress and adjust as needed

## Appendix

### A. uv Quick Start Guide

**Installation**:
```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or via pip
pip install uv

# Or via Homebrew
brew install uv
```

**Common Commands**:
```bash
# Initialize a new project
uv init

# Create virtual environment (automatic with uv sync)
uv venv

# Add a dependency
uv add flask

# Add a dev dependency
uv add --dev pytest

# Remove a dependency
uv remove flask

# Install all dependencies from lockfile
uv sync

# Install with dev dependencies
uv sync --all-extras

# Update dependencies
uv lock --upgrade

# Update specific package
uv lock --upgrade-package flask

# Run a command in the virtual environment
uv run python app.py
uv run pytest
uv run black .

# Compile requirements (for backwards compatibility)
uv pip compile pyproject.toml -o requirements.txt
```

### B. Python Code Style Examples

**Example with Type Annotations and Google Docstrings**:

```python
"""Video writer module for generating videos from cylindrical scan images.

This module provides functionality to create MP4 videos from sequences of
images stored in S3, with support for decimation and custom frame rates.
"""

from typing import List, Optional, Tuple
import numpy as np
from PIL import Image


class VideoWriter:
    """Handles video generation from image sequences.

    This class manages the conversion of cylindrical scan images into
    MP4 video files with configurable settings.

    Attributes:
        output_path: Path where the video file will be saved.
        fps: Frames per second for the output video.
        decimate: Frame decimation factor to reduce video size.
    """

    def __init__(
        self, output_path: str, fps: int = 30, decimate: int = 1
    ) -> None:
        """Initialize the VideoWriter.

        Args:
            output_path: Full path to the output video file.
            fps: Target frames per second. Defaults to 30.
            decimate: Frame decimation factor. Defaults to 1 (no decimation).

        Raises:
            ValueError: If fps or decimate are not positive integers.
        """
        if fps <= 0 or decimate <= 0:
            raise ValueError("fps and decimate must be positive integers")

        self.output_path = output_path
        self.fps = fps
        self.decimate = decimate

    def add_frame(self, image: Image.Image) -> None:
        """Add a single frame to the video.

        Args:
            image: PIL Image object to add as a frame.

        Raises:
            ValueError: If image is None or invalid format.
        """
        if image is None:
            raise ValueError("Image cannot be None")
        # Implementation here
        pass

    def process_images(
        self, image_paths: List[str], progress_callback: Optional[callable] = None
    ) -> Tuple[int, float]:
        """Process a list of images and create a video.

        Args:
            image_paths: List of file paths to images.
            progress_callback: Optional callback function called with progress
                percentage (0-100).

        Returns:
            A tuple containing:
                - Number of frames processed
                - Total duration of the video in seconds

        Raises:
            FileNotFoundError: If any image path does not exist.
            IOError: If video writing fails.
        """
        total = len(image_paths)
        processed = 0

        for i, path in enumerate(image_paths):
            # Process image
            processed += 1

            if progress_callback:
                progress_callback((i + 1) / total * 100)

        duration = processed / self.fps
        return processed, duration


def generate_video_from_scan(
    scan_id: int, output_path: str, decimate: int = 4
) -> dict[str, any]:
    """Generate a video from a cylindrical scan.

    This is the main entry point for video generation from scan data.

    Args:
        scan_id: Database ID of the scan to process.
        output_path: Path where the output video will be saved.
        decimate: Frame decimation factor. Defaults to 4.

    Returns:
        Dictionary containing:
            - 'frames': Number of frames processed
            - 'duration': Video duration in seconds
            - 'path': Path to the generated video

    Raises:
        ValueError: If scan_id is invalid.
        DatabaseError: If scan data cannot be retrieved.
    """
    # Implementation here
    return {"frames": 0, "duration": 0.0, "path": output_path}
```

### C. Useful Commands Cheatsheet

```bash
# Python (uv)
uv sync                    # Install dependencies
uv add flask              # Add dependency
uv add --dev pytest       # Add dev dependency
uv run pytest             # Run tests
uv run black .            # Format code
uv run ruff check .       # Lint code
uv run mypy .             # Type check

# JavaScript/TypeScript (pnpm)
pnpm install              # Install dependencies
pnpm run lint             # Lint all packages
pnpm run lint:fix         # Fix linting issues
pnpm run format           # Format all files
pnpm run test             # Run all tests
pnpm run build            # Build all packages

# Makefile shortcuts
make lint-python          # Lint Python code
make format-python        # Format Python code
make test-python          # Run Python tests
make dev-up               # Start dev environment
make dev-down             # Stop dev environment

# Pre-commit
pre-commit run --all-files  # Run all hooks
pre-commit autoupdate      # Update hook versions

# CI/CD
act -j lint-frontend      # Run GitHub Actions locally
act -j test-backend       # Run backend tests locally
```

### D. Recommended VSCode Extensions

```json
{
  "recommendations": [
    "dbaeumer.vscode-eslint",
    "esbenp.prettier-vscode",
    "ms-python.python",
    "ms-python.black-formatter",
    "charliermarsh.ruff",
    "ms-python.mypy-type-checker",
    "orta.vscode-jest",
    "firsttris.vscode-jest-runner",
    "astral-sh.ruff"
  ]
}
```

### E. VSCode Settings for Python

```json
{
  "[python]": {
    "editor.defaultFormatter": "ms-python.black-formatter",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.organizeImports": true
    }
  },
  "black-formatter.args": ["--line-length", "88"],
  "ruff.lint.args": ["--line-length", "88"],
  "python.analysis.typeCheckingMode": "basic",
  "python.linting.mypyEnabled": true
}
```
