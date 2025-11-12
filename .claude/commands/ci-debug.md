---
name: CI Debug
description: Debug GitHub Actions CI failures for Bloom's Next.js + Flask stack
category: Troubleshooting
tags: [ci, github-actions, debugging, testing]
---

# CI Debug - GitHub Actions Pipeline

Comprehensive guide for debugging CI failures and understanding the CI pipeline for Bloom (Next.js + Flask + Docker + Supabase).

## Overview

**Note**: Bloom's GitHub Actions CI is planned for Phase 3. This guide prepares for future CI implementation and helps debug common issues that would occur in CI.

**Planned CI Jobs**:

1. **lint-typescript** - ESLint + Prettier for Next.js
2. **lint-python** - black + ruff + mypy for Flask
3. **type-check** - TypeScript compilation
4. **test-unit-frontend** - Vitest/Jest unit tests
5. **test-unit-backend** - pytest for Flask
6. **test-integration** - Full-stack integration tests
7. **build-docker** - Docker production builds
8. **test-e2e** - Playwright E2E tests (future)

## Quick Diagnosis

### Step 1: Identify Failing Job

Check PR checks section on GitHub to see which job(s) failed.

### Step 2: Check Job Logs

Click "Details" next to failed check → View full CI logs.

### Step 3: Common Failure Patterns

| Symptom                   | Likely Cause                      | Quick Fix                                                       |
| ------------------------- | --------------------------------- | --------------------------------------------------------------- |
| **Lint failures**         | Code style violations             | Run `pnpm lint` and `pnpm format` locally                       |
| **Python lint failures**  | Black/Ruff violations             | Run `cd flask && uv run black . && uv run ruff check .`         |
| **Type errors**           | TypeScript compilation issues     | Run `pnpm type-check` locally                                   |
| **Docker build fails**    | Layer caching or dependency issue | Run `docker-compose -f docker-compose.dev.yml build --no-cache` |
| **Database errors**       | Supabase migration issue          | Run `supabase db reset` locally                                 |
| **Port conflicts**        | Services already running          | Stop containers: `docker-compose down`                          |
| **MinIO errors**          | Bucket not initialized            | Check `minio_data/` permissions                                 |
| **Timeout errors**        | CI slower than local machine      | Increase timeout values in tests                                |
| **Coverage too low**      | Tests not meeting thresholds      | Add more tests to increase coverage                             |
| **Environment variables** | Missing .env.ci file              | Check required env vars are set in CI config                    |

## Job-by-Job Debugging

### 1. lint-typescript (Planned)

**What it does:**

- Runs ESLint on TypeScript/JavaScript files
- Checks Prettier formatting
- Validates import order

**Common failures:**

- Code style violations
- Formatting inconsistencies
- Import order issues
- Unused variables

**Debug locally:**

```bash
# Run linting
pnpm lint

# Check formatting
pnpm format:check

# Auto-fix formatting
pnpm format

# Lint specific workspace
cd web && pnpm lint
```

**CI-specific considerations:**

- Uses cached `node_modules` for speed
- Runs on all workspaces (web/, packages/\*)
- Uses pnpm workspace protocol

---

### 2. lint-python (Planned)

**What it does:**

- Runs black (formatter check)
- Runs ruff (linter)
- Runs mypy (type checker)

**Common failures:**

- Python formatting violations
- Type annotation issues
- Unused imports or variables
- Import order issues

**Debug locally:**

```bash
# Navigate to Flask directory
cd flask

# Install dependencies
uv sync

# Check formatting
uv run black --check .

# Run linter
uv run ruff check .

# Run type checker
uv run mypy .
```

**Auto-fix:**

```bash
cd flask

# Fix formatting
uv run black .

# Fix auto-fixable linter issues
uv run ruff check . --fix
```

**CI-specific considerations:**

- Uses `astral-sh/setup-uv@v7` for Python environment
- Caches uv dependencies
- Runs from `flask/` directory

---

### 3. type-check (Planned)

**What it does:**

- Type-checks TypeScript without emitting files
- Validates type definitions across all packages
- Ensures workspace dependencies are typed

**Common failures:**

- Type errors (`Property 'x' does not exist`)
- Missing type definitions
- Workspace dependency type mismatches

**Debug locally:**

```bash
# Type check all workspaces
pnpm type-check

# Type check specific workspace
cd web && pnpm type-check

# Type check with verbose output
pnpm type-check --verbose
```

**CI-specific considerations:**

- Requires all workspace packages to be built
- Uses Turborepo caching
- Checks all packages in monorepo

---

### 4. test-unit-frontend (Planned)

**What it does:**

- Runs Vitest/Jest unit tests for Next.js and packages
- Enforces coverage thresholds (target: 70%)
- Tests React components, utilities, API clients

**Common failures:**

- Test failures (logic errors)
- Coverage below threshold
- Component rendering errors
- Mock setup issues

**Debug locally:**

```bash
# Run all unit tests
pnpm test

# Run with coverage
pnpm test:coverage

# Run in watch mode (for development)
pnpm test:watch

# Run specific test file
pnpm test path/to/test.test.ts

# Run with UI (Vitest)
pnpm test:ui
```

**View coverage report:**

```bash
# After running tests with coverage
open coverage/index.html  # macOS
xdg-open coverage/index.html  # Linux
start coverage/index.html  # Windows
```

**CI-specific considerations:**

- Uses Turborepo for parallel execution
- Requires environment variables for API mocking
- May need mock Supabase client

---

### 5. test-unit-backend (Planned)

**What it does:**

- Runs pytest with coverage for Flask
- Enforces coverage threshold (target: 80%)
- Tests Flask routes, utilities, video processing

**Common failures:**

- Test failures (logic errors)
- Coverage below threshold
- Import errors (missing dependencies)
- Database fixture issues

**Debug locally:**

```bash
# Run Flask tests
cd flask
uv run pytest

# Run with coverage
uv run pytest --cov --cov-report=term --cov-report=html

# Run specific test file
uv run pytest tests/test_video.py

# Run with verbose output
uv run pytest -v

# Run with debug output
uv run pytest -vv -s
```

**View coverage report:**

```bash
# After running tests with coverage
cd flask
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
start htmlcov/index.html  # Windows
```

**CI-specific considerations:**

- Uses uv for dependency management
- Caches Python dependencies
- Requires test database setup
- May need MinIO mock for S3 tests

---

### 6. test-integration (Planned)

**What it does:**

- Tests full-stack integration (Next.js + Flask + Supabase)
- Verifies API endpoints work end-to-end
- Tests database operations and file uploads

**Common failures:**

- Services not starting correctly
- Database migration issues
- Network connectivity between containers
- MinIO bucket initialization failures
- Authentication/authorization issues

**Debug locally:**

```bash
# Start all services
docker-compose -f docker-compose.dev.yml up -d

# Check service health
docker-compose -f docker-compose.dev.yml ps

# View logs
docker-compose -f docker-compose.dev.yml logs flask-app
docker-compose -f docker-compose.dev.yml logs web

# Run integration tests (when implemented)
pnpm test:integration

# Stop services
docker-compose -f docker-compose.dev.yml down
```

**CI-specific considerations:**

- Requires Docker-in-Docker or Docker socket access
- Uses separate database for testing
- Needs environment variables for service URLs
- May need wait-for-it scripts for service readiness

---

### 7. build-docker (Planned)

**What it does:**

- Builds production Docker images
- Validates multi-stage builds
- Tests image size and layer caching

**Common failures:**

- Dependency installation failures
- Build context too large
- Layer caching issues
- Missing environment variables
- File permissions issues

**Debug locally:**

```bash
# Build production images
docker-compose -f docker-compose.prod.yml build

# Build without cache
docker-compose -f docker-compose.prod.yml build --no-cache

# Build specific service
docker-compose -f docker-compose.prod.yml build flask-app

# Check image sizes
docker images | grep bloom

# Test production images locally
docker-compose -f docker-compose.prod.yml up -d
```

**Verify build:**

```bash
# Check running containers
docker-compose -f docker-compose.prod.yml ps

# Test Flask health
curl http://localhost:5002/api/

# Test Next.js
curl http://localhost:3000/

# View logs
docker-compose -f docker-compose.prod.yml logs --tail=50
```

**CI-specific considerations:**

- Uses Docker layer caching
- May push to container registry
- Requires build secrets for private dependencies
- Tests both dev and prod Docker targets

---

### 8. test-e2e (Planned - Future)

**What it does:**

- Starts all services in Docker
- Runs Playwright E2E tests
- Tests user workflows across full application

**Common failures:**

- Services not ready when tests start
- Timeout waiting for elements
- Authentication/session issues
- Flaky tests due to race conditions

**Debug locally:**

```bash
# Start all services
docker-compose -f docker-compose.dev.yml up -d

# Wait for services to be ready
sleep 10

# Run E2E tests (when implemented)
pnpm test:e2e

# Run with UI for debugging
pnpm test:e2e:ui

# Run with debug mode
pnpm test:e2e:debug
```

**CI-specific considerations:**

- Requires headless browser setup
- May need xvfb on Linux
- Longer timeouts needed for CI environment
- Uploads test artifacts on failure

---

## Platform-Specific Issues

### Linux (Ubuntu CI Runner)

**Special requirements:**

- Docker and Docker Compose installed
- Sufficient disk space for images
- System packages for native dependencies

**Common Linux-specific errors:**

1. **Docker socket permission denied:**

   ```
   permission denied while trying to connect to Docker daemon socket
   ```

   **Fix:** Add user to docker group or use sudo (in CI, runner usually has access)

2. **Out of disk space:**

   ```
   no space left on device
   ```

   **Fix:** Clean up old Docker images in CI config:

   ```bash
   docker system prune -af --volumes
   ```

3. **System library missing:**
   ```
   ImportError: libgl1: cannot open shared object file
   ```
   **Fix:** Install system dependencies in CI step:
   ```bash
   apt-get update && apt-get install -y libgl1-mesa-glx
   ```

---

### macOS (macOS CI Runner)

**Special requirements:**

- Docker Desktop or colima
- More expensive CI minutes (10x cost)

**Common macOS-specific errors:**

1. **Docker not running:**

   ```
   Cannot connect to the Docker daemon
   ```

   **Fix:** Ensure Docker Desktop is started in CI setup

2. **File permissions:**
   - macOS has different default file permissions
   - May need explicit `chmod` commands

---

### Windows (Windows CI Runner)

**Special requirements:**

- Use `shell: bash` in GitHub Actions (for cross-platform scripts)
- Different path separators
- WSL2 for Docker

**Common Windows-specific errors:**

1. **Path separators:**

   ```
   Error: ENOENT: no such file or directory
   ```

   **Fix:** Use `path.join()` instead of string concatenation

2. **Line endings:**

   ```
   SyntaxError: Invalid or unexpected token
   ```

   **Fix:** Ensure `.gitattributes` configures CRLF/LF correctly

3. **Port conflicts:**
   - More common on Windows due to slower port cleanup
   - **Fix:** Ensure proper container shutdown

---

## Docker-Specific Issues

### Issue 1: Layer Caching Not Working

**Symptom:** Docker builds are slow in CI, not using cache

**Debug:**

```bash
# Check if layers are being cached
docker-compose -f docker-compose.prod.yml build --progress=plain

# Look for "CACHED" in output
```

**Fix:**

- Ensure `.dockerignore` is correct
- Order Dockerfile commands from least to most frequently changed
- Use `COPY package.json pnpm-lock.yaml` before `COPY .`

### Issue 2: Build Context Too Large

**Symptom:** "Sending build context" takes a long time

**Debug:**

```bash
# Check build context size
du -sh .

# Check what's being sent
tar -czh . | wc -c
```

**Fix:**

Add to `.dockerignore`:

```
node_modules
.next
.turbo
dist
coverage
.git
minio_data
supabase/data
```

### Issue 3: Multi-Stage Build Fails

**Symptom:** Intermediate stage succeeds, final stage fails

**Debug:**

```bash
# Build just the first stage
docker build --target builder -t bloom-builder .

# Test intermediate stage
docker run -it bloom-builder sh
```

**Fix:** Ensure dependencies are available in the final stage

---

## Supabase-Specific Issues

### Issue 1: Migrations Not Applied

**Symptom:** Database schema is outdated in tests

**Debug:**

```bash
# Check migration status
supabase migration list

# Check applied migrations
supabase db diff
```

**Fix:**

```bash
# Apply all migrations
supabase db push

# Or reset database
supabase db reset
```

### Issue 2: RLS Policies Blocking Tests

**Symptom:** Database queries fail with permission errors

**Debug:**

```bash
# Check RLS policies
supabase db inspect rls

# Test with service role (bypasses RLS)
# In tests, use service_role key instead of anon key
```

**Fix:** Either:

- Use service_role key for tests
- Set up test user with correct JWT claims
- Disable RLS for test database

### Issue 3: Supabase Not Ready

**Symptom:** Tests fail with "connection refused" to database

**Debug:**

```bash
# Check Supabase status
supabase status

# Check database is accepting connections
pg_isready -h 127.0.0.1 -p 54322
```

**Fix:** Add wait-for script in CI:

```bash
#!/bin/bash
until pg_isready -h 127.0.0.1 -p 54322; do
  echo "Waiting for Supabase..."
  sleep 2
done
```

---

## MinIO-Specific Issues

### Issue 1: Bucket Not Initialized

**Symptom:** File upload tests fail with "bucket does not exist"

**Debug:**

```bash
# Check MinIO is running
docker-compose -f docker-compose.dev.yml ps minio

# List buckets
docker exec -it bloom-minio mc ls local/
```

**Fix:**

```bash
# Initialize bucket in CI setup
docker exec bloom-minio mc mb local/bloom-videos --ignore-existing
```

### Issue 2: Presigned URLs Fail

**Symptom:** Can't access uploaded files via presigned URL

**Debug:**

```bash
# Test presigned URL generation
curl -I "http://localhost:9000/bloom-videos/test.mp4?..."
```

**Fix:** Ensure MinIO is accessible at correct URL in environment variables

---

## Environment Variable Issues

### Common Missing Variables

**Required for CI:**

```bash
# Database
DATABASE_URL=postgresql://...
SUPABASE_URL=http://localhost:54321
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...

# MinIO/S3
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
AWS_ENDPOINT=http://localhost:9000
AWS_REGION=us-east-1
S3_BUCKET_NAME=bloom-videos

# Next.js
NEXT_PUBLIC_SUPABASE_URL=http://localhost:54321
NEXT_PUBLIC_SUPABASE_ANON_KEY=...

# Flask
FLASK_ENV=test
SECRET_KEY=test-secret-key
CORS_ALLOWED_ORIGINS=http://localhost:3000
```

**Debug:**

```bash
# Check environment variables in container
docker exec bloom-flask-app printenv | grep SUPABASE

# Check .env file is being loaded
docker exec bloom-flask-app cat .env.dev
```

---

## CI Performance Optimization

### Caching Strategy

**Node.js Dependencies:**

```yaml
- name: Cache pnpm store
  uses: actions/cache@v4
  with:
    path: ~/.pnpm-store
    key: ${{ runner.os }}-pnpm-${{ hashFiles('pnpm-lock.yaml') }}
    restore-keys: |
      ${{ runner.os }}-pnpm-
```

**Python Dependencies (uv):**

```yaml
- name: Setup uv
  uses: astral-sh/setup-uv@v7
  with:
    enable-cache: true
    cache-dependency-glob: 'flask/pyproject.toml'
```

**Docker Layer Cache:**

```yaml
- name: Build Docker images
  uses: docker/build-push-action@v5
  with:
    context: .
    cache-from: type=gha
    cache-to: type=gha,mode=max
```

**Turborepo Cache:**

```yaml
- name: Run tests
  run: pnpm test
  env:
    TURBO_TOKEN: ${{ secrets.TURBO_TOKEN }}
    TURBO_TEAM: ${{ secrets.TURBO_TEAM }}
```

### Parallel Execution

**Use Turborepo for parallel tasks:**

```bash
# Run linting in parallel across workspaces
pnpm lint

# Run tests in parallel
pnpm test

# Run type checking in parallel
pnpm type-check
```

**Use matrix strategy for platform testing:**

```yaml
strategy:
  matrix:
    os: [ubuntu-latest, macos-latest, windows-latest]
```

---

## Debugging Failed CI Runs

### Step 1: Reproduce Locally

```bash
# Set CI environment variable (enables CI-specific behavior)
export CI=true  # macOS/Linux
set CI=true     # Windows

# Run failing command
pnpm test
```

### Step 2: Check CI Logs

**Key sections to check:**

1. **Setup steps** - Did dependencies install correctly?
2. **Build steps** - Did Docker builds succeed?
3. **Service startup** - Are all services running?
4. **Test output** - What was the actual error?
5. **Cleanup** - Did containers stop properly?

**Search for keywords:**

- `ERROR`
- `FAIL`
- `timeout`
- `ENOENT` (file not found)
- `ECONNREFUSED` (connection refused)
- `EADDRINUSE` (port conflict)

### Step 3: Download Artifacts

Failed tests may upload artifacts:

```bash
# From GitHub Actions UI
# 1. Click on failed workflow run
# 2. Scroll to "Artifacts" section
# 3. Download relevant artifact
# 4. Extract and view:
unzip playwright-report.zip
npx playwright show-report playwright-report/
```

### Step 4: Run Locally with Docker

Test the exact same environment as CI:

```bash
# Use the same Docker Compose file
docker-compose -f docker-compose.dev.yml up -d

# Run tests inside container
docker exec bloom-flask-app pytest

# Or run Next.js tests
docker exec bloom-web pnpm test
```

---

## Common CI Failure Scenarios

### Scenario 1: "Works on my machine, fails in CI"

**Possible causes:**

1. **Different environment variables**
   - CI might not have same env vars as local
2. **Cached dependencies**
   - CI cache might be stale
3. **File permissions**
   - Different user permissions in CI
4. **Timing issues**
   - Services start slower in CI

**Debug approach:**

```bash
# Match CI environment locally
node -v  # Should match CI
python --version  # Should match CI
pnpm install --frozen-lockfile  # Use exact lockfile
uv sync  # Use exact lockfile

# Clear caches
pnpm store prune
uv cache clean
docker system prune -af
```

---

### Scenario 2: Intermittent CI Failures

**Possible causes:**

1. **Race conditions**
   - Services not ready when tests start
2. **Resource limits**
   - CI runner out of memory/disk
3. **Network issues**
   - Dependency download failures
4. **Flaky tests**
   - Non-deterministic test behavior

**Debug approach:**

```bash
# Run test multiple times
for i in {1..10}; do pnpm test || break; done

# Add wait-for scripts
./scripts/wait-for-services.sh

# Increase timeouts in tests
# Edit test file, increase timeout values
```

---

### Scenario 3: Docker Build Failures

**Example:** Build works locally, fails in CI

**Debug approach:**

1. **Check build context:**

   ```bash
   # Ensure .dockerignore is correct
   cat .dockerignore

   # Check context size
   docker-compose -f docker-compose.prod.yml build --progress=plain
   ```

2. **Check layer caching:**

   ```bash
   # Build without cache
   docker-compose -f docker-compose.prod.yml build --no-cache
   ```

3. **Check multi-arch issues:**
   ```bash
   # CI might be ARM, local might be x86
   docker buildx build --platform linux/amd64,linux/arm64 .
   ```

---

### Scenario 4: Database Migration Failures

**Example:** Migrations apply locally, fail in CI

**Debug approach:**

```bash
# Check migration files are committed
git ls-files supabase/migrations/

# Test migrations in clean database
supabase db reset
supabase db push

# Check for migration conflicts
supabase db diff
```

---

## Local CI Simulation

### Run Full CI Pipeline Locally

```bash
# 1. Lint TypeScript
pnpm lint

# 2. Lint Python
cd flask && uv run black --check . && uv run ruff check . && uv run mypy .

# 3. Type check
pnpm type-check

# 4. Run unit tests (frontend)
pnpm test:coverage

# 5. Run unit tests (backend)
cd flask && uv run pytest --cov

# 6. Build Docker images
docker-compose -f docker-compose.prod.yml build

# 7. Start services and run integration tests (when implemented)
docker-compose -f docker-compose.dev.yml up -d
pnpm test:integration

# 8. Clean up
docker-compose -f docker-compose.dev.yml down
```

**Or use the `/run-ci-locally` command** for automated CI simulation.

---

## Quick Reference: Debugging Checklist

When CI fails, go through this checklist:

- [ ] **Identify failing job** - Which job failed?
- [ ] **Check logs** - What was the error message?
- [ ] **Reproduce locally** - Does it fail on your machine?
- [ ] **Check environment variables** - Are all required env vars set?
- [ ] **Check Docker images** - Did builds succeed?
- [ ] **Check service health** - Are all services running?
- [ ] **Check database** - Are migrations applied?
- [ ] **Check MinIO** - Is bucket initialized?
- [ ] **Check recent changes** - Did you add new dependencies?
- [ ] **Check file permissions** - Are permissions correct in Docker?
- [ ] **Test with CI=true** - Does `CI=true` change behavior locally?
- [ ] **Check disk space** - Is CI runner out of space?
- [ ] **Increase timeouts** - Could slow CI runner cause timeout?
- [ ] **Review caching** - Could stale cache cause issues?

---

## Related Commands

- `/run-ci-locally` - Run full CI suite locally before pushing
- `/validate-env` - Validate development environment setup
- `/lint` - Run all linting checks
- `/coverage` - Run test coverage analysis
- `/fix-formatting` - Auto-fix code formatting issues

---

## Getting Help

- **CI-specific issues:** Check GitHub Actions status page
- **Report bugs:** https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues
- **Docker issues:** Review Docker and Docker Compose logs
- **Supabase issues:** Check Supabase local development docs
