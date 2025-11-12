---
name: Run CI Locally
description: Run full CI checks locally before pushing (TypeScript + Python + Docker)
category: Testing
tags: [ci, testing, linting, validation]
---

# Run CI Checks Locally

Run the exact same CI checks locally that will run on GitHub Actions before pushing your code. This command validates both TypeScript (Next.js) and Python (Flask) code, plus Docker builds.

## Quick Start

```bash
# Run all CI checks (linting + type checking + tests + Docker builds)
# This will mirror what runs in GitHub Actions CI
```

Alternatively, run commands manually:

```bash
# Quick CI check (linting + type checking only, ~30 seconds)
pnpm lint && pnpm type-check && cd flask && uv run black --check . && uv run ruff check . && uv run mypy .

# Full CI check (includes tests, ~2-5 minutes)
pnpm lint && pnpm type-check && pnpm test:coverage && cd flask && uv run black --check . && uv run ruff check . && uv run mypy . && uv run pytest --cov

# With Docker build validation (~5-10 minutes)
pnpm lint && pnpm type-check && docker-compose -f docker-compose.prod.yml build
```

## What This Command Does

This command runs the complete CI workflow that would run in GitHub Actions:

### Phase 1: Linting (TypeScript)

```bash
# ESLint + Prettier for all workspaces
pnpm lint
```

**Checks:**

- ESLint rules compliance
- Prettier formatting
- Import order (via eslint-plugin-import)
- Unused variables and imports
- TypeScript-specific rules

### Phase 2: Type Checking (TypeScript)

```bash
# Type check all workspaces
pnpm type-check
```

**Checks:**

- TypeScript compilation errors
- Type mismatches
- Missing type definitions
- Workspace dependency types

### Phase 3: Linting (Python)

```bash
cd flask

# Black formatter check
uv run black --check .

# Ruff linter
uv run ruff check .

# mypy type checker
uv run mypy .
```

**Checks:**

- PEP 8 code style (via Black)
- Import sorting (via Ruff)
- Unused imports and variables (via Ruff)
- Type annotations (via mypy)
- Flask-specific patterns

### Phase 4: Unit Tests (TypeScript)

```bash
# Run tests with coverage
pnpm test:coverage
```

**Checks:**

- Vitest/Jest unit tests pass
- Code coverage meets threshold (70%)
- Component tests pass
- API client tests pass

### Phase 5: Unit Tests (Python)

```bash
cd flask

# Run tests with coverage
uv run pytest --cov --cov-fail-under=80
```

**Checks:**

- pytest unit tests pass
- Code coverage meets threshold (80%)
- Flask route tests pass
- Video processing tests pass

### Phase 6: Pre-commit Hooks

```bash
# Run all pre-commit hooks
uv run pre-commit run --all-files
```

**Checks:**

- Trailing whitespace
- YAML syntax
- Large files check
- Merge conflict markers
- All configured hooks

### Phase 7: Docker Builds (Optional)

```bash
# Build production Docker images
docker-compose -f docker-compose.prod.yml build
```

**Checks:**

- Flask production image builds
- Next.js production image builds
- Multi-stage build optimization
- Layer caching works correctly

## Why Use This?

**Benefits:**

- ✅ Catch CI failures before pushing
- ✅ Faster feedback loop (run locally in ~2-5 min vs waiting 10-15 min for CI)
- ✅ Exactly matches CI environment
- ✅ Prevents "oops, forgot to run lint" commits
- ✅ Reduces CI build queue time
- ✅ Tests both TypeScript and Python code
- ✅ Validates Docker builds locally

**When to use:**

- Before every `git push`
- Before creating a PR
- After making significant changes
- After updating dependencies
- When you want confidence your PR will pass CI

## Expected Output

### ✅ Success (All Checks Pass)

```
================================
Running CI Checks Locally
================================

[1/7] TypeScript linting...
✨ ESLint: 0 errors, 0 warnings
✨ Prettier: All files formatted correctly
✅ TypeScript linting passed (web, packages/bloom-fs, packages/bloom-js, packages/bloom-nextjs-auth)

[2/7] TypeScript type checking...
✨ No type errors found
✅ Type checking passed (4 workspaces)

[3/7] Python linting...
All done! ✨ 🍰 ✨
45 files would be left unchanged.
✅ Black formatting passed
✨ Ruff: 0 errors, 0 warnings
✅ Ruff linting passed
✅ mypy type checking passed

[4/7] TypeScript tests...
Test Files  12 passed (12)
     Tests  134 passed (134)
  Duration  3.45s

✅ TypeScript tests passed

[5/7] TypeScript coverage...
------------------------|---------|----------|---------|---------|-------------------
File                    | % Stmts | % Branch | % Funcs | % Lines | Uncovered Lines
------------------------|---------|----------|---------|---------|-------------------
All files               |   78.23 |    71.45 |   82.11 |   78.23 |
 src/components         |   85.12 |    80.34 |   89.23 |   85.12 |
 src/lib                |   72.34 |    65.23 |   76.12 |   72.34 |
 packages/bloom-fs      |   82.45 |    78.56 |   85.67 |   82.45 |
------------------------|---------|----------|---------|---------|-------------------
✅ Coverage: 78.23% (meets 70% threshold)

[6/7] Python tests...
================================ test session starts =================================
collected 67 items

tests/test_video.py .........                                               [ 13%]
tests/test_api.py ...........                                               [ 30%]
tests/test_utils.py .....                                                   [ 37%]
...
================================ 67 passed in 8.34s ==================================

Coverage: 82% (meets 80% threshold)
✅ Python tests passed

[7/7] Pre-commit hooks...
Trim Trailing Whitespace.................................................Passed
Fix End of Files.........................................................Passed
Check Yaml...............................................................Passed
Check for added large files..............................................Passed
Check for merge conflicts................................................Passed
✅ Pre-commit hooks passed

================================
✅ ALL CI CHECKS PASSED!
================================

Your code is ready to push! 🚀

Summary:
- TypeScript linting: ✅ 0 errors
- TypeScript type checking: ✅ No errors
- Python linting: ✅ 0 errors
- TypeScript tests: ✅ 134 passed
- Python tests: ✅ 67 passed
- Coverage (TS): ✅ 78.23%
- Coverage (Python): ✅ 82%
- Pre-commit hooks: ✅ All passed

Time: ~3 minutes
```

### ❌ Failure (Checks Failed)

```
================================
Running CI Checks Locally
================================

[1/7] TypeScript linting...
/Users/you/bloom/web/src/components/VideoPlayer.tsx
  12:5   error  'videoRef' is assigned a value but never used  @typescript-eslint/no-unused-vars
  23:10  error  Unexpected console statement                   no-console
❌ TypeScript linting FAILED (2 errors)

[2/7] TypeScript type checking...
web/src/lib/api.ts:45:12 - error TS2345: Argument of type 'string' is not assignable to parameter of type 'number'.
❌ Type checking FAILED (1 error)

[3/7] Python linting...
would reformat flask/app/video.py
Oh no! 💥 💔 💥
1 file would be reformatted, 44 files would be left unchanged.
❌ Black formatting FAILED

[4/7] Python linting (Ruff)...
flask/app/api.py:12:1: F401 [*] `os` imported but unused
flask/app/utils.py:45:89: E501 Line too long (102 > 88 characters)
❌ Ruff linting FAILED (2 errors)

Stopping checks (4 failures detected)

================================
❌ CI CHECKS FAILED
================================

Fix the issues above before pushing.

Quick fixes:
- TypeScript: Run 'pnpm lint --fix' to auto-fix linting issues
- Python: Run 'cd flask && uv run black .' to auto-fix formatting
- Python: Run 'cd flask && uv run ruff check --fix .' to auto-fix linting issues
- Or use: /fix-formatting to auto-fix all issues

Time: ~1 minute (stopped early)
```

## Integration with Git Workflow

### Before Pushing

```bash
# 1. Make your changes
git add web/src/components/VideoPlayer.tsx

# 2. Run CI checks locally
/run-ci-locally

# 3. If checks pass, commit and push
git commit -m "feat: add video player component"
git push

# 4. CI on GitHub will pass ✅
```

### Before Creating PR

```bash
# Ensure your branch is ready for PR
git checkout -b feature/new-feature

# ... make changes ...

# Run full CI suite
/run-ci-locally

# If all checks pass, create PR
gh pr create --title "Add new feature" --body "Description..."
```

### Pre-Commit Hook Integration

Bloom already uses `pre-commit` hooks. The command runs them as part of validation:

```bash
# Pre-commit hooks run automatically on git commit
git commit -m "feat: add feature"

# Or run manually on all files
uv run pre-commit run --all-files
```

## Command Variants

### Quick Check (Linting + Type Checking Only, ~30 seconds)

```bash
# Skip tests, just validate code style and types
pnpm lint && pnpm type-check
cd flask && uv run black --check . && uv run ruff check . && uv run mypy .
```

**Use when:**

- Quick validation before commit
- Just checking if formatting is correct
- Don't need full test coverage

### Standard Check (With Tests, ~2-5 minutes)

```bash
# Full CI check without Docker builds
pnpm lint && pnpm type-check && pnpm test:coverage
cd flask && uv run black --check . && uv run ruff check . && uv run mypy . && uv run pytest --cov
```

**Use when:**

- Before pushing to remote
- Before creating PR
- After significant code changes

### Full Check (With Docker, ~5-10 minutes)

```bash
# Everything including Docker production builds
pnpm lint && pnpm type-check && pnpm test:coverage
cd flask && uv run black --check . && uv run ruff check . && uv run mypy . && uv run pytest --cov
docker-compose -f docker-compose.prod.yml build
```

**Use when:**

- Before release
- After changing Dockerfiles
- Validating production deployment

## Comparison with Individual Commands

| Command               | What it does                         | When to use            | Duration     |
| --------------------- | ------------------------------------ | ---------------------- | ------------ |
| `/lint`               | Just linting (TS + Python)           | Quick code style check | ~20-30s      |
| `/coverage`           | Just tests with coverage             | Check test coverage    | ~1-2 min     |
| `/validate-env`       | Validate environment setup           | First time setup       | ~30s         |
| **`/run-ci-locally`** | **All checks (lint + tests + more)** | **Before pushing/PR**  | **~2-5 min** |
| `/ci-debug`           | Debug CI failures                    | When CI fails          | N/A (guide)  |

## Platform Notes

### macOS

- All checks run natively
- Docker builds may be slower than Linux
- Use `--no-cache` flag for Docker if builds are inconsistent

### Ubuntu/Linux

- Fastest for Docker builds
- Native container support
- Recommended for full CI simulation

### Windows

- Use WSL2 for best performance
- Docker Desktop required
- Git Bash or PowerShell supported
- May need to run `pnpm` commands differently

## CI Configuration Reference

This command mirrors the planned `.github/workflows/ci.yml`:

```yaml
# TypeScript linting
- name: Lint TypeScript
  run: pnpm lint

# TypeScript type checking
- name: Type check
  run: pnpm type-check

# Python linting
- name: Lint Python
  run: |
    cd flask
    uv run black --check .
    uv run ruff check .
    uv run mypy .

# TypeScript tests
- name: Test TypeScript
  run: pnpm test:coverage

# Python tests
- name: Test Python
  run: |
    cd flask
    uv run pytest --cov --cov-fail-under=80

# Docker builds
- name: Build Docker images
  run: docker-compose -f docker-compose.prod.yml build
```

## Troubleshooting

### "pnpm not found"

```bash
# Install pnpm globally
npm install -g pnpm

# Verify installation
pnpm -v
```

### "uv not found"

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or with Homebrew
brew install uv

# Verify installation
uv --version
```

### "Tests fail locally but pass in CI"

Possible causes:

- Different Node/Python versions
- Environment variables not set
- Services not running (Supabase, MinIO)
- Stale dependencies

**Fix:**

```bash
# Check versions match CI
node -v  # Should be 18+
python --version  # Should be 3.11+

# Reinstall dependencies
pnpm install --frozen-lockfile
cd flask && uv sync

# Start services
supabase start
docker-compose -f docker-compose.dev.yml up -d

# Re-run tests
pnpm test
cd flask && uv run pytest
```

### "Docker build fails"

```bash
# Clear Docker cache
docker system prune -af

# Build without cache
docker-compose -f docker-compose.prod.yml build --no-cache

# Check Dockerfile syntax
docker-compose -f docker-compose.prod.yml config
```

### "Pre-commit hooks fail"

```bash
# Update pre-commit
uv run pre-commit autoupdate

# Clean and reinstall hooks
uv run pre-commit clean
uv run pre-commit install

# Run manually
uv run pre-commit run --all-files
```

### "Command takes too long"

**Optimization strategies:**

1. **Skip tests for quick checks:**

   ```bash
   pnpm lint && pnpm type-check
   cd flask && uv run black --check . && uv run ruff check .
   ```

2. **Skip Docker builds:**

   ```bash
   # Don't run Docker build step
   ```

3. **Use Turborepo caching:**

   ```bash
   # Turbo caches test results
   pnpm test  # Cached if no code changes
   ```

4. **Run specific tests:**
   ```bash
   # Just test one workspace
   cd web && pnpm test
   # Just test one file
   cd flask && uv run pytest tests/test_video.py
   ```

### "Coverage fails below threshold"

**TypeScript coverage (70% threshold):**

```bash
# View coverage report
open coverage/index.html

# Add tests to increase coverage
# Focus on untested files shown in report
```

**Python coverage (80% threshold):**

```bash
# View coverage report
cd flask
open htmlcov/index.html

# Add tests to increase coverage
# Focus on untested functions
```

## Parallel Execution

Turborepo runs tasks in parallel where possible:

```bash
# pnpm lint runs in parallel across workspaces
pnpm lint

# pnpm test runs in parallel across workspaces
pnpm test

# This is much faster than sequential execution
```

## Environment Variables

For tests to pass, ensure these are set:

```bash
# Database
DATABASE_URL=postgresql://postgres:postgres@localhost:54322/postgres
SUPABASE_URL=http://localhost:54321
SUPABASE_ANON_KEY=...

# MinIO/S3
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
AWS_ENDPOINT=http://localhost:9000

# Next.js
NEXT_PUBLIC_SUPABASE_URL=http://localhost:54321
NEXT_PUBLIC_SUPABASE_ANON_KEY=...

# Flask
FLASK_ENV=test
SECRET_KEY=test-secret-key
```

Check `.env.example` for full list.

## Tips

1. **Run frequently**: Don't wait until you're done - run after each logical change
2. **Fix formatting first**: Formatting failures are fastest to fix with `/fix-formatting`
3. **Use `/fix-formatting`**: Auto-fixes most issues instead of just checking
4. **Parallel development**: Run this while working on next task
5. **CI queue optimization**: Running locally reduces wasted CI cycles
6. **Cache awareness**: Turborepo caches results, subsequent runs are faster
7. **Incremental checks**: Run just `pnpm lint` for quick checks, full suite before push
8. **Pre-commit integration**: Hooks run automatically, but this command is more comprehensive

## Git Alias (Optional)

Add a git alias for quick CI checks:

```bash
# Add to ~/.gitconfig
[alias]
    ci = "!pnpm lint && pnpm type-check && pnpm test:coverage && cd flask && uv run black --check . && uv run ruff check . && uv run mypy . && uv run pytest --cov"
    ci-quick = "!pnpm lint && pnpm type-check && cd flask && uv run black --check . && uv run ruff check . && uv run mypy ."
    ci-fix = "!pnpm lint --fix && cd flask && uv run black . && uv run ruff check --fix ."

# Usage
git ci        # Run full CI
git ci-quick  # Quick CI (no tests)
git ci-fix    # Auto-fix issues
```

## Makefile Integration

Bloom's Makefile includes CI targets:

```bash
# Run full CI locally
make ci

# Run quick CI (linting only)
make ci-quick

# Auto-fix formatting
make format
```

**Makefile commands:**

```makefile
.PHONY: ci
ci:
	pnpm lint
	pnpm type-check
	pnpm test:coverage
	cd flask && uv run black --check . && uv run ruff check . && uv run mypy . && uv run pytest --cov

.PHONY: ci-quick
ci-quick:
	pnpm lint
	pnpm type-check
	cd flask && uv run black --check . && uv run ruff check . && uv run mypy .

.PHONY: format
format:
	pnpm format
	cd flask && uv run black . && uv run ruff check --fix .
```

## Related Commands

- `/lint` - Just linting checks (TypeScript + Python)
- `/coverage` - Full coverage analysis with HTML reports
- `/validate-env` - Validate development environment setup
- `/fix-formatting` - Auto-fix all formatting issues
- `/ci-debug` - Debug CI failures
- `/review-pr` - PR review checklist (includes CI checks)

## Success Metrics

After implementing this workflow, you should see:

- ✅ Fewer failed CI builds (catch issues locally first)
- ✅ Faster PR review cycles (CI passes on first try)
- ✅ More confidence in code quality
- ✅ Reduced CI queue time
- ✅ Earlier bug detection

## Related GitHub Actions (Planned)

When GitHub Actions CI is configured, these jobs will mirror this command:

- `lint-typescript` - ESLint + Prettier
- `lint-python` - Black + Ruff + mypy
- `type-check` - TypeScript compilation
- `test-unit-frontend` - Vitest tests
- `test-unit-backend` - pytest tests
- `build-docker` - Production Docker builds
- `test-integration` - Full-stack integration tests (future)

This command ensures your local validation exactly matches CI.
