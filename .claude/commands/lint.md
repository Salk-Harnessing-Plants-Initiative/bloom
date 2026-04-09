---
name: Lint & Format Code
description: Run linting and formatting checks across the monorepo
category: Development
tags: [lint, format, code-quality]
---

# Lint & Format Code

Run linting and formatting checks across the Bloom monorepo.

## Quick Commands

```bash
# TypeScript/JavaScript
npm run lint                   # ESLint via Turborepo
npm run lint:fix               # ESLint auto-fix
npm run format                 # Prettier auto-format
npm run format:check           # Prettier check only

# TypeScript type checking (no script exists — run directly)
cd web && npx tsc --noEmit

# Python — the LangGraph agent (langchain/)
cd langchain && uv run black --check .
cd langchain && uv run ruff check .
cd langchain && uv run mypy . --ignore-missing-imports

# Python — the FastMCP server (bloommcp/)
cd bloommcp && uv run black --check .
cd bloommcp && uv run ruff check .
cd bloommcp && uv run mypy . --ignore-missing-imports

# All Python services at once
cd langchain && uv run black --check . && uv run ruff check . && cd ../bloommcp && uv run black --check . && uv run ruff check .

# Pre-commit hooks (runs all configured checks)
uv run pre-commit run --all-files
```

## TypeScript/JavaScript Linting

### ESLint

```bash
npm run lint        # Check for issues
npm run lint:fix    # Auto-fix issues
```

Runs via Turborepo across all workspaces that define a `lint` script.

**Config:** `.eslintrc.js` at repo root with `@typescript-eslint`, `eslint-plugin-react`, `eslint-plugin-react-hooks`, `eslint-config-next`, `eslint-config-prettier`.

### Prettier

```bash
npm run format         # Auto-format all files
npm run format:check   # Check only (no changes)
```

**Config:** `.prettierrc.json` — no semicolons, single quotes, 2-space indent, trailing commas (es5), 100 char width.

### TypeScript Type Checking

```bash
cd web && npx tsc --noEmit
```

**Note:** There is no `type-check` script in `package.json`. Run `tsc` directly in the `web/` directory.

## Python Linting

Python linting applies to two services: the LangGraph agent (`langchain/`) and the FastMCP server (`bloommcp/`).

### Black (Formatter)

```bash
cd langchain && uv run black --check .   # Check only
cd langchain && uv run black .           # Auto-format

cd bloommcp && uv run black --check .    # Check only
cd bloommcp && uv run black .            # Auto-format
```

### Ruff (Linter)

```bash
cd langchain && uv run ruff check .          # Check only
cd langchain && uv run ruff check --fix .    # Auto-fix

cd bloommcp && uv run ruff check .           # Check only
cd bloommcp && uv run ruff check --fix .     # Auto-fix
```

### mypy (Type Checker)

```bash
cd langchain && uv run mypy . --ignore-missing-imports
cd bloommcp && uv run mypy . --ignore-missing-imports
```

Uses `--ignore-missing-imports` because some data science libraries (scipy, scikit-learn, statsmodels, seaborn, umap-learn) lack type stubs.

## Pre-commit Hooks

`.pre-commit-config.yaml` runs these hooks automatically on `git commit`:

- **General:** trailing-whitespace, end-of-file-fixer, check-yaml, check-added-large-files, check-merge-conflict, check-toml
- **Python:** Black, Ruff, mypy targeting `^(langchain|bloommcp)/`
- **JS/TS:** Prettier on `.(js|jsx|ts|tsx|json|md)$`

```bash
# Run all hooks manually
uv run pre-commit run --all-files

# Install hooks (first time)
uv run pre-commit install
```

## CI Context

**Important:** Python linting (Black, Ruff, mypy) is recommended locally but **NOT currently enforced in CI**.

What CI actually checks:
- `build-and-audit` job: `npm audit --audit-level=critical`, `npx tsc --noEmit` (type check), `npm run build` (Next.js build)
- `python-audit` job: `pip-audit` for CVE scanning only (no Black/Ruff/mypy)

## Common Issues

### ESLint errors

```bash
# See all errors with details
npm run lint 2>&1 | head -50

# Auto-fix what can be fixed
npm run lint:fix
```

### Prettier formatting

```bash
# Check what would change
npm run format:check

# Fix all formatting
npm run format
```

### Python formatting

```bash
# Fix all Python formatting in both services
cd langchain && uv run black . && uv run ruff check --fix . && cd ../bloommcp && uv run black . && uv run ruff check --fix .
```

### Pre-commit hook failures

```bash
# Update hooks
uv run pre-commit autoupdate

# Clean and reinstall
uv run pre-commit clean
uv run pre-commit install
```

## Related Commands

- `/fix-formatting` — auto-fix all formatting issues
- `/run-ci-locally` — run the full CI pipeline locally
- `/pre-merge` — comprehensive pre-merge checklist