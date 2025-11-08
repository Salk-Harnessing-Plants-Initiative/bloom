---
name: Lint & Format Code
description: Run linting and formatting checks across the monorepo
category: Development
tags: [lint, format, code-quality]
---

# Lint & Format Code

Run linting and formatting checks across the Bloom monorepo to ensure code quality and consistent style.

## Quick Commands

### Python (Flask API)

```bash
# Format Python code with Black
cd flask && uv run black .

# Check formatting without making changes
cd flask && uv run black --check .

# Run Ruff linter (with auto-fix)
cd flask && uv run ruff check --fix .

# Run Ruff linter (check only)
cd flask && uv run ruff check .

# Run mypy type checking
cd flask && uv run mypy .

# Run all Python checks
cd flask && uv run black . && uv run ruff check --fix . && uv run mypy .
```

### JavaScript/TypeScript (Next.js Web)

```bash
# Format code with Prettier
pnpm format

# Check formatting without making changes
pnpm format:check

# Run ESLint
pnpm lint

# Fix ESLint issues automatically
pnpm lint:fix

# Run TypeScript type checking
pnpm type-check
```

### Pre-commit Hooks

```bash
# Run all pre-commit hooks manually
pre-commit run --all-files

# Run pre-commit on staged files only
pre-commit run

# Update pre-commit hook versions
pre-commit autoupdate
```

## Configuration Files

Our linting configuration is defined in:

- **Python**:

  - `flask/pyproject.toml` - Black, Ruff, mypy, pytest config
  - `.pre-commit-config.yaml` - Pre-commit hook definitions

- **JavaScript/TypeScript**:
  - `.eslintrc.js` - ESLint rules for Next.js, React, TypeScript
  - `.prettierrc.json` - Prettier formatting rules
  - `turbo.json` - Monorepo task definitions

## Python Linting Details

### Black (Code Formatter)

- **Line length**: 88 characters
- **Target**: Python 3.11
- **Config**: `[tool.black]` in `flask/pyproject.toml`

### Ruff (Linter)

- **Checks**: pycodestyle (E/W), pyflakes (F), isort (I), bugbear (B), comprehensions (C4), pyupgrade (UP)
- **Line length**: 88 characters
- **Config**: `[tool.ruff]` in `flask/pyproject.toml`

### mypy (Type Checker)

- **Strict mode**: Enabled
- **Checks**: return types, unused configs, untyped defs
- **Config**: `[tool.mypy]` in `flask/pyproject.toml`

## JavaScript/TypeScript Linting Details

### ESLint

- **Extends**: Next.js, React, TypeScript recommended configs
- **Prettier integration**: Disabled conflicting rules
- **Config**: `.eslintrc.js`

### Prettier

- **Line length**: 100 characters
- **Style**: Single quotes, no semicolons, trailing commas (ES5)
- **Config**: `.prettierrc.json`

## Monorepo Context

Our Turborepo workspace has several packages that linting applies to:

- **flask/**: Python Flask API (black, ruff, mypy)
- **web/**: Next.js frontend (prettier, eslint, tsc)
- **packages/bloom-fs/**: File system utilities (prettier, eslint, tsc)
- **packages/bloom-js/**: Shared JavaScript utilities (prettier, eslint, tsc)
- **packages/bloom-nextjs-auth/**: Auth helpers (prettier, eslint, tsc)

## Common Issues & Fixes

### Python

**Issue**: Import order errors from Ruff

```bash
# Auto-fix with Ruff
cd flask && uv run ruff check --fix .
```

**Issue**: Type errors from mypy

```bash
# Add type hints to functions
def process_image(image_path: str) -> bool:
    # ... implementation
```

**Issue**: Line too long

```bash
# Black will auto-format, or break the line manually
# Before: some_function(argument1, argument2, argument3, argument4, argument5)
# After:
some_function(
    argument1,
    argument2,
    argument3,
    argument4,
    argument5,
)
```

### JavaScript/TypeScript

**Issue**: Prettier formatting conflicts

```bash
# Run Prettier to auto-fix
pnpm format
```

**Issue**: ESLint errors

```bash
# Auto-fix where possible
pnpm lint:fix

# For remaining errors, fix manually
```

**Issue**: TypeScript type errors

```bash
# Run type-check to see all errors
pnpm type-check

# Fix by adding proper type annotations
```

## Pre-commit Hook Integration

When you commit code, pre-commit hooks automatically run:

1. **Trailing whitespace**: Removed
2. **End of file fixer**: Ensures newline at EOF
3. **YAML/TOML check**: Validates syntax
4. **Black**: Formats Python code
5. **Ruff**: Lints and fixes Python code
6. **mypy**: Type checks Python code (Flask only)
7. **Prettier**: Formats JS/TS/JSON/MD files

If any hook fails, the commit is blocked. Fix the issues and try again.

## Workflow

### Before Committing

1. **Run linters manually** to catch issues early
2. **Fix any errors** reported by linters
3. **Run pre-commit** to ensure hooks will pass
4. **Stage changes** with `git add`
5. **Commit** - hooks run automatically

### Continuous Integration

Once Phase 1 CI/CD is merged, GitHub Actions will:

- Run linting on all PRs
- Block merge if linting fails
- Ensure consistent code quality across the team

## Tips

1. **Use uv for Python tools**: Always use `uv run` to ensure correct virtual environment
2. **Run linters frequently**: Catch issues early, don't wait for pre-commit
3. **Auto-fix when possible**: Use `--fix` flags to save time
4. **Configure your editor**: Set up Black/Prettier/ESLint in your IDE for real-time feedback
5. **Don't fight the formatter**: Accept Black/Prettier's decisions to maintain consistency

## Editor Integration

### VS Code

Install these extensions:

- **Python**: ms-python.python (includes Black, Ruff, mypy support)
- **ESLint**: dbaeumer.vscode-eslint
- **Prettier**: esbenp.prettier-vscode

Configure settings:

```json
{
  "editor.formatOnSave": true,
  "python.formatting.provider": "black",
  "python.linting.ruffEnabled": true,
  "python.linting.mypyEnabled": true,
  "editor.defaultFormatter": "esbenp.prettier-vscode",
  "[python]": {
    "editor.defaultFormatter": "ms-python.python"
  }
}
```
