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
pnpm format && cd flask && uv run black . && uv run ruff check --fix .

# Or using Makefile
make format
```

Individual fixes:

```bash
# TypeScript only (Prettier + ESLint)
pnpm format

# Python only (Black + Ruff)
cd flask
uv run black .
uv run ruff check --fix .
```

## What Gets Fixed

### TypeScript/JavaScript (Prettier + ESLint)

**Prettier fixes:**

- Line length (100 characters, per [.prettierrc.json](.prettierrc.json))
- Quote style (single quotes)
- Semicolons (removed)
- Indentation (2 spaces)
- Trailing commas (ES5 style)
- Arrow function parentheses (always)

**ESLint auto-fixes:**

- Unused imports removal
- Import order
- Consistent spacing
- Missing semicolons (if any)
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

- Docstring content
- Variable names
- Logic errors
- Missing docstrings
- Complex type errors
- Some ESLint rules (requires code changes)

## Commands Executed

### Full Fix (TypeScript + Python)

```bash
# Fix TypeScript/JavaScript
pnpm format

# Fix Python
cd flask
uv run black .
uv run ruff check --fix .
```

### Pre-commit Auto-fix

```bash
# Run all pre-commit hooks with auto-fix
uv run pre-commit run --all-files

# This includes:
# - Trailing whitespace removal
# - EOF fixing
# - YAML formatting
# - JSON formatting
# - Prettier formatting
# - Black formatting
```

## Expected Output

### ✅ TypeScript Files Reformatted

```
Running Prettier formatter...

web/src/components/VideoPlayer.tsx 120ms
web/src/lib/api.ts 45ms
packages/bloom-fs/src/index.ts 32ms

✨ 3 files reformatted, 87 files unchanged

Running ESLint auto-fix...

/web/src/components/VideoPlayer.tsx
  12:5  ✓ Removed unused import 'React'
  45:10 ✓ Fixed import order

✅ TypeScript formatting fixed!
```

### ✅ Python Files Reformatted

```
Running Black formatter...

reformatted flask/app/video.py
reformatted flask/app/api.py
All done! ✨ 🍰 ✨
2 files reformatted, 43 files left unchanged.

Running Ruff auto-fix...

Fixed 8 errors:
  flask/app/api.py:12:1: F401 [*] Removed unused import 'os'
  flask/app/video.py:5:1: I001 [*] Sorted imports
  flask/app/utils.py:89:1: W291 [*] Removed trailing whitespace

✅ Python formatting fixed!
```

### ✅ No Changes Needed

```
Running Prettier formatter...
All files formatted correctly ✨

Running Black formatter...
All done! ✨ 🍰 ✨
45 files left unchanged.

Running Ruff...
All checks passed!

✅ Code already properly formatted!
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

# Quick fix:
/fix-formatting

# Verify
git diff

# Commit the fixes
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
gh pr create --title "Feature: Add video player" --body "..."
```

### After Resolving Merge Conflicts

```bash
# After resolving conflicts
git add <resolved-files>

# Clean up formatting (conflicts often break it)
/fix-formatting

# Verify resolved correctly
git diff

# Commit merge
git commit
```

## What to Review After Running

### Check Git Diff

Always review what formatting tools changed:

```bash
# Review all changes
git diff

# Review specific workspace
git diff web/
git diff flask/
```

**Look for:**

- Line wrapping changes (long lines split)
- Quote normalization
- Import reordering
- Trailing comma additions
- Whitespace changes

### Common TypeScript Changes

```typescript
// Before
import { VideoPlayer } from './components/VideoPlayer'
import React, { useState, useEffect } from 'react'
import { createClient } from '@supabase/supabase-js'

const videoUrl = 'https://example.com/video.mp4'

function processVideo(url: string, options: { quality: number; format: string }) {
  return fetch(url)
}

// After (Prettier + ESLint formatted)
import React, { useEffect, useState } from 'react'
import { createClient } from '@supabase/supabase-js'

import { VideoPlayer } from './components/VideoPlayer'

const videoUrl = 'https://example.com/video.mp4'

function processVideo(
  url: string,
  options: {
    quality: number
    format: string
  }
) {
  return fetch(url)
}
```

### Common Python Changes

```python
# Before
from flask import Flask, request, jsonify
import os
from app.video import process_video, generate_thumbnail
import boto3

def create_video(experiment_id: str, video_path: str, duration: int = 60, quality: str = "high"):
    return {"id": 123, "path": video_path, "duration": duration}

# After (Black + Ruff formatted)
import boto3
from flask import Flask, jsonify, request

from app.video import generate_thumbnail, process_video


def create_video(
    experiment_id: str,
    video_path: str,
    duration: int = 60,
    quality: str = "high",
):
    return {"id": 123, "path": video_path, "duration": duration}
```

### Verify Tests Still Pass

Formatting should never break tests, but verify:

```bash
# TypeScript tests
pnpm test

# Python tests
cd flask && uv run pytest
```

If tests fail after formatting, you likely have a syntax error (rare).

## Comparison with /lint

| Command           | Purpose                               | When to Use                |
| ----------------- | ------------------------------------- | -------------------------- |
| `/lint`           | **Check** formatting without changing | Before push, to verify     |
| `/fix-formatting` | **Fix** formatting automatically      | After changes, to clean up |
| `/run-ci-locally` | Check + test everything               | Before push, comprehensive |

**Workflow:**

1. Write code
2. Run `/fix-formatting` to auto-fix style
3. Run `/lint` to verify all rules pass
4. Run tests
5. Commit

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

**File**: `.eslintrc.js` (root and workspaces)

ESLint extends Next.js and TypeScript configs with custom rules.

### Python (Black)

**File**: `flask/pyproject.toml`

```toml
[tool.black]
line-length = 88
target-version = ['py311']
```

### Python (Ruff)

**File**: `flask/pyproject.toml`

```toml
[tool.ruff]
line-length = 88
select = ["E", "F", "I", "W"]
```

### Pre-commit Hooks

**File**: `.pre-commit-config.yaml`

Bloom uses pre-commit hooks to auto-format on commit.

## Manual Fixes Still Needed

Some issues require manual fixing:

### TypeScript Type Errors

```typescript
// ESLint can't fix this
const value: number = 'string' // ❌ Type error

// You must fix manually:
const value: string = 'string' // ✅ Correct
```

### Missing TypeScript Types

```typescript
// ESLint warns but can't add types
function processVideo(url) {
  // ❌ Missing parameter type
  return fetch(url)
}

// You must add:
function processVideo(url: string): Promise<Response> {
  // ✅ Correct
  return fetch(url)
}
```

### Python Docstring Issues

```python
# Black won't fix this
def process_video(path: str):
    """Process video"""  # ❌ Missing period, missing Args/Returns

# You must fix manually:
def process_video(path: str):
    """Process video file.

    Args:
        path: Path to video file.

    Returns:
        Video processing result.
    """
```

### Complex Logic Issues

```python
# Ruff can't fix unused variable in complex case
for item in items:
    result = process(item)  # ❌ 'result' unused
    continue

# You must refactor:
for item in items:
    process(item)  # ✅ Correct
```

Run `/lint` after `/fix-formatting` to find remaining issues.

## Common Scenarios

### 1. "CI says formatting failed"

```bash
# Fix it
/fix-formatting

# Verify
pnpm lint
cd flask && uv run black --check . && uv run ruff check .

# Commit
git add -u
git commit -m "style: apply code formatting"
git push
```

### 2. "I made lots of changes"

```bash
# Before committing, clean up formatting
/fix-formatting

# Review changes
git diff

# Commit formatting separately from logic
git add -u
git commit -m "style: apply code formatting"

# Then commit your actual changes
git add <your-files>
git commit -m "feat: your actual change"
```

### 3. "Multiple workspaces changed"

```bash
# Fix all workspaces at once
pnpm format

# Review by workspace
git diff web/
git diff packages/bloom-fs/
git diff flask/

# Commit
git add -u
git commit -m "style: format all workspaces"
```

### 4. "Pre-commit hook failed"

```bash
# Pre-commit runs automatically, but you can run manually
uv run pre-commit run --all-files

# This will auto-fix most issues
# Then commit again
git commit
```

## IDE Integration

### VSCode (Recommended)

**Workspace settings** (`.vscode/settings.json`):

```json
{
  // TypeScript/JavaScript (Prettier)
  "editor.defaultFormatter": "esbenp.prettier-vscode",
  "editor.formatOnSave": true,
  "[typescript]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode",
    "editor.codeActionsOnSave": {
      "source.fixAll.eslint": true
    }
  },
  "[typescriptreact]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode",
    "editor.codeActionsOnSave": {
      "source.fixAll.eslint": true
    }
  },

  // Python (Black)
  "[python]": {
    "editor.defaultFormatter": "ms-python.black-formatter",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.organizeImports": true
    }
  },

  // Prettier
  "prettier.requireConfig": true,
  "prettier.useEditorConfig": false
}
```

**Required extensions:**

- Prettier - Code formatter (esbenp.prettier-vscode)
- ESLint (dbaeumer.vscode-eslint)
- Black Formatter (ms-python.black-formatter)
- Ruff (charliermarsh.ruff)

### Cursor (Same as VSCode)

Cursor uses VSCode settings format.

### JetBrains (PyCharm/WebStorm)

**TypeScript/JavaScript:**

- Settings → Languages & Frameworks → JavaScript → Prettier
- Enable "On save"

**Python:**

- Settings → Tools → Black → Enable Black formatter
- Settings → Tools → External Tools → Add Ruff

### Vim/Neovim

```lua
-- Using null-ls or conform.nvim
require('conform').setup({
  formatters_by_ft = {
    typescript = { "prettier", "eslint" },
    python = { "black", "ruff" },
  },
  format_on_save = {
    timeout_ms = 500,
    lsp_fallback = true,
  },
})
```

## Pre-Commit Hook Integration

Bloom already has pre-commit configured:

**`.pre-commit-config.yaml`**:

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files

  - repo: https://github.com/pre-commit/mirrors-prettier
    rev: v3.1.0
    hooks:
      - id: prettier
        types_or: [javascript, jsx, ts, tsx, json, yaml, markdown]

  - repo: https://github.com/psf/black
    rev: 24.1.1
    hooks:
      - id: black
        language_version: python3.11

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.1.15
    hooks:
      - id: ruff
        args: [--fix, --exit-non-zero-on-fix]
```

**Auto-format on commit:**

```bash
# Install pre-commit hooks
uv run pre-commit install

# Now formatting runs automatically on git commit

# To run manually on all files
uv run pre-commit run --all-files
```

## Turborepo Caching

Bloom uses Turborepo, which caches formatting results:

```bash
# First run: formats and caches
pnpm format

# Subsequent runs: uses cache if no changes
pnpm format  # Much faster!
```

Cache is invalidated when files change.

## Troubleshooting

### "Prettier not found"

```bash
# Install dependencies
pnpm install

# Verify Prettier is installed
pnpm prettier --version
```

### "Black not found"

```bash
# Install Python dependencies
cd flask
uv sync

# Verify Black is installed
uv run black --version
```

### "Ruff not found"

```bash
# Install with uv
cd flask
uv sync

# Or install globally
pip install ruff
```

### "Pre-commit hook fails"

```bash
# Update pre-commit
uv run pre-commit autoupdate

# Clean and reinstall hooks
uv run pre-commit clean
uv run pre-commit install

# Run manually to see errors
uv run pre-commit run --all-files
```

### "Formatting conflicts between tools"

Prettier and ESLint can conflict. Bloom's config resolves conflicts by:

- Using `eslint-config-prettier` to disable conflicting ESLint rules
- Prettier runs first, ESLint fixes remaining issues

If you see conflicts:

```bash
# Check ESLint config
cat .eslintrc.js

# Should extend 'prettier' last
```

### "Formatting broke my code"

Very rare, but if it happens:

```bash
# Revert
git checkout -- .

# Report issue
# - For TypeScript: Check Prettier GitHub issues
# - For Python: Check Black GitHub issues
```

### "Git diff shows huge changes"

If formatting shows many changes:

```bash
# Commit formatting separately
git add -u
git commit -m "style: apply code formatting"

# Then commit actual changes
git add <your-files>
git commit -m "feat: actual changes"
```

## Makefile Integration

Bloom's Makefile includes formatting targets:

```bash
# Fix all formatting
make format

# Check formatting without fixing
make lint
```

**Makefile commands:**

```makefile
.PHONY: format
format:
	pnpm format
	cd flask && uv run black . && uv run ruff check --fix .

.PHONY: lint
lint:
	pnpm lint
	cd flask && uv run black --check . && uv run ruff check . && uv run mypy .
```

## Tips

1. **Run frequently**: Format as you go, not at the end
2. **Separate commits**: Keep formatting changes in their own commit
3. **Review diffs**: Make sure tools didn't do anything unexpected
4. **IDE integration**: Set up formatters to run on save
5. **Pre-commit hook**: Let pre-commit auto-format before each commit
6. **Use `/run-ci-locally`**: Comprehensive check before pushing
7. **Cache awareness**: Turborepo caches formatting, subsequent runs are fast
8. **Fix before lint**: Run formatting before running linters

## Git Alias (Optional)

Add aliases for quick formatting:

```bash
# Add to ~/.gitconfig
[alias]
    format = "!pnpm format && cd flask && uv run black . && uv run ruff check --fix ."
    format-check = "!pnpm lint && cd flask && uv run black --check . && uv run ruff check ."

# Usage
git format        # Fix formatting
git format-check  # Check formatting
```

## Related Commands

- `/lint` - Check formatting without fixing
- `/run-ci-locally` - Run all CI checks (includes formatting check)
- `/ci-debug` - Debug CI formatting failures
- `/validate-env` - Ensure formatters are installed

## Success Metrics

After implementing auto-formatting workflow:

- ✅ Fewer "fix formatting" PR comments
- ✅ Consistent code style across team
- ✅ Faster PR reviews (no style bikeshedding)
- ✅ Less time spent on manual formatting
- ✅ Cleaner git diffs (formatting separate from logic)

## Additional Resources

- **Prettier**: https://prettier.io/docs/en/
- **ESLint**: https://eslint.org/docs/latest/
- **Black**: https://black.readthedocs.io/
- **Ruff**: https://docs.astral.sh/ruff/
- **Pre-commit**: https://pre-commit.com/
