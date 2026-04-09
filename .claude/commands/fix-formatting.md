---
name: Fix Formatting
description: Automatically fix formatting issues for TypeScript and Python code
category: Development
tags: [formatting, prettier, eslint, black, ruff, auto-fix]
---

# Fix Formatting Issues

Automatically fix formatting and style issues for both TypeScript and Python code instead of just checking them.

## Quick Start

```bash
# Auto-fix all formatting issues (TypeScript + Python)
npm run format && cd langchain && uv run black . && uv run ruff check --fix . && cd ../bloommcp && uv run black . && uv run ruff check --fix .
```

Individual fixes:

```bash
# TypeScript only (Prettier + ESLint)
npm run format

# Python only — LangGraph agent
cd langchain && uv run black . && uv run ruff check --fix .

# Python only — FastMCP server
cd bloommcp && uv run black . && uv run ruff check --fix .
```

## What Gets Fixed

### TypeScript/JavaScript (Prettier + ESLint)

**Prettier fixes:**

- Line length (100 characters, per `.prettierrc.json`)
- Quote style (single quotes)
- Semicolons (removed)
- Indentation (2 spaces)
- Trailing commas (ES5 style)
- Arrow function parentheses (always)

**ESLint auto-fixes:**

- Unused imports removal
- Import order
- Consistent spacing
- Quote consistency

### Python (Black + Ruff)

**Black fixes:**

- Line length (88 characters)
- Quote style (double quotes)
- Indentation (4 spaces)
- Trailing commas
- Whitespace normalization

**Ruff auto-fixes:**

- Unused imports removal
- Import sorting
- Line length issues
- Trailing whitespace
- Unused variables (when safe)

### Not Auto-Fixed

These require manual fixes:

- Variable names and docstring content
- Logic errors and complex type errors
- Some ESLint rules (requires code changes)

## Commands Executed

### Full Fix (TypeScript + Python)

```bash
# Fix TypeScript/JavaScript
npm run format

# Fix Python — both services
cd langchain && uv run black . && uv run ruff check --fix .
cd ../bloommcp && uv run black . && uv run ruff check --fix .
```

### Pre-commit Auto-fix

```bash
# Run all pre-commit hooks with auto-fix
uv run pre-commit run --all-files
```

## Usage Workflow

### Before Committing

```bash
# 1. Fix formatting automatically
/fix-formatting

# 2. Review changes
git diff

# 3. Stage and commit
git add -u
git commit -m "style: apply code formatting"
```

### After PR Review

```bash
# Reviewer says: "Please fix formatting"
/fix-formatting

# Verify and commit
git diff
git add -u
git commit -m "style: fix formatting per review"
git push
```

### Before Creating PR

```bash
# Clean up formatting before opening PR
/fix-formatting

# Check everything passes
/run-ci-locally

# Create PR
gh pr create --title "feat: description" --body "..."
```

## What to Review After Running

Always review what formatting tools changed:

```bash
# Review all changes
git diff

# Review by area
git diff web/
git diff langchain/
git diff bloommcp/
```

### Verify Tests Still Pass

Formatting should never break tests, but verify:

```bash
# Integration tests (requires Docker stack running)
uv run pytest tests/integration/ -v --tb=short
```

## Comparison with /lint

| Command           | Purpose                               | When to Use                |
| ----------------- | ------------------------------------- | -------------------------- |
| `/lint`           | **Check** formatting without changing | Before push, to verify     |
| `/fix-formatting` | **Fix** formatting automatically      | After changes, to clean up |
| `/run-ci-locally` | Check + test everything               | Before push, comprehensive |

## Configuration Files

### TypeScript/JavaScript (Prettier)

**File**: `.prettierrc.json`

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

### TypeScript/JavaScript (ESLint)

**File**: `.eslintrc.js` — extends Next.js and TypeScript configs with `eslint-config-prettier`.

### Python (Black + Ruff)

Python formatting uses default configs. Black defaults to line-length 88, Python 3.11 target.

### Pre-commit Hooks

**File**: `.pre-commit-config.yaml`

Pre-commit hooks target `^(langchain|bloommcp)/` for Python tools (Black, Ruff, mypy) and `.(js|jsx|ts|tsx|json|md)$` for Prettier.

```bash
# Install pre-commit hooks
uv run pre-commit install

# Run manually on all files
uv run pre-commit run --all-files
```

## Troubleshooting

### "Prettier not found"

```bash
npm install
npx prettier --version
```

### "Black not found"

```bash
cd langchain && uv sync
uv run black --version
```

### "Pre-commit hook fails"

```bash
uv run pre-commit autoupdate
uv run pre-commit clean
uv run pre-commit install
uv run pre-commit run --all-files
```

### "Formatting conflicts between tools"

Prettier and ESLint can conflict. Bloom's config uses `eslint-config-prettier` to disable conflicting ESLint rules.

## Tips

1. **Run frequently** — format as you go, not at the end
2. **Separate commits** — keep formatting changes in their own commit
3. **Review diffs** — make sure tools didn't do anything unexpected
4. **IDE integration** — set up Prettier/Black to format on save in VS Code
5. **Pre-commit hook** — let pre-commit auto-format before each commit
6. **Fix before lint** — run formatting before running linters

## Related Commands

- `/lint` — check formatting without fixing
- `/run-ci-locally` — run all CI checks (includes formatting check)
- `/ci-debug` — debug CI formatting failures
- `/validate-env` — ensure formatters are installed