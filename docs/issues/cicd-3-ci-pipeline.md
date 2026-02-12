# Issue: CI Pipeline Workflow

**Epic**: [CI/CD Pipeline with Staging & Production Environments](./EPIC-cicd-deployment-testing.md)
**Priority**: P0
**Dependencies**: #cicd-1 (Runner), #cicd-2 (Test Infrastructure)
**Labels**: `cicd`, `github-actions`, `testing`

## Summary

Create GitHub Actions workflows that run linting, type checking, and all test suites on every PR and push, providing quality gates before code can be merged or deployed.

## Background

Without CI, code quality issues are only caught manually. This workflow ensures:
- All code passes linting and type checks
- Unit tests pass with coverage tracking
- Integration tests validate API contracts
- E2E tests verify critical user flows
- Build succeeds before merge

## Requirements

### Functional
- [ ] Triggers on all PRs and pushes to `dev`/`main`
- [ ] Runs lint and type check
- [ ] Runs unit tests with coverage
- [ ] Runs integration tests against test database
- [ ] Runs E2E tests against ephemeral environment
- [ ] Reports results back to PR
- [ ] Blocks merge if any check fails

### Performance
- [ ] Caches dependencies between runs
- [ ] Parallelizes independent jobs
- [ ] Completes in under 15 minutes

## Implementation

### Main CI Workflow

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main, dev]
  pull_request:
    branches: [main, dev]

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

env:
  NODE_VERSION: '20'
  PNPM_VERSION: '10'

jobs:
  lint:
    name: Lint & Type Check
    runs-on: [self-hosted, bloom]
    steps:
      - uses: actions/checkout@v4

      - name: Setup pnpm
        uses: pnpm/action-setup@v4
        with:
          version: ${{ env.PNPM_VERSION }}

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'pnpm'

      - name: Install dependencies
        run: pnpm install --frozen-lockfile

      - name: Lint
        run: pnpm lint

      - name: Type Check
        run: pnpm typecheck

  unit-tests:
    name: Unit Tests
    runs-on: [self-hosted, bloom]
    steps:
      - uses: actions/checkout@v4

      - name: Setup pnpm
        uses: pnpm/action-setup@v4
        with:
          version: ${{ env.PNPM_VERSION }}

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'pnpm'

      - name: Install dependencies
        run: pnpm install --frozen-lockfile

      - name: Run unit tests
        run: pnpm test:run --coverage

      - name: Upload coverage
        uses: codecov/codecov-action@v4
        with:
          files: ./coverage/coverage-final.json
          fail_ci_if_error: false

  build:
    name: Build
    runs-on: [self-hosted, bloom]
    steps:
      - uses: actions/checkout@v4

      - name: Setup pnpm
        uses: pnpm/action-setup@v4
        with:
          version: ${{ env.PNPM_VERSION }}

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'pnpm'

      - name: Install dependencies
        run: pnpm install --frozen-lockfile

      - name: Build
        run: pnpm build

      - name: Upload build artifacts
        uses: actions/upload-artifact@v4
        with:
          name: build
          path: |
            web/.next
            packages/*/dist
          retention-days: 1

  integration-tests:
    name: Integration Tests
    runs-on: [self-hosted, bloom]
    needs: [build]
    services:
      db:
        image: supabase/postgres:15.8.1.060
        env:
          POSTGRES_PASSWORD: test_password
          POSTGRES_DB: bloom_test
        ports:
          - 54322:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    steps:
      - uses: actions/checkout@v4

      - name: Setup pnpm
        uses: pnpm/action-setup@v4
        with:
          version: ${{ env.PNPM_VERSION }}

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'pnpm'

      - name: Install dependencies
        run: pnpm install --frozen-lockfile

      - name: Run migrations
        run: pnpm db:migrate:test
        env:
          DATABASE_URL: postgres://postgres:test_password@localhost:54322/bloom_test

      - name: Run integration tests
        run: pnpm test:integration
        env:
          DATABASE_URL: postgres://postgres:test_password@localhost:54322/bloom_test

  e2e-tests:
    name: E2E Tests
    runs-on: [self-hosted, bloom]
    needs: [build]
    steps:
      - uses: actions/checkout@v4

      - name: Setup pnpm
        uses: pnpm/action-setup@v4
        with:
          version: ${{ env.PNPM_VERSION }}

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'pnpm'

      - name: Install dependencies
        run: pnpm install --frozen-lockfile

      - name: Install Playwright browsers
        run: npx playwright install --with-deps chromium

      - name: Download build artifacts
        uses: actions/download-artifact@v4
        with:
          name: build

      - name: Start test stack
        run: docker-compose -f docker-compose.test.yml up -d

      - name: Wait for services
        run: |
          timeout 60 bash -c 'until curl -s http://localhost:3000 > /dev/null; do sleep 1; done'

      - name: Run E2E tests
        run: pnpm test:e2e
        env:
          BASE_URL: http://localhost:3000

      - name: Upload test results
        uses: actions/upload-artifact@v4
        if: failure()
        with:
          name: playwright-report
          path: playwright-report/
          retention-days: 7

      - name: Stop test stack
        if: always()
        run: docker-compose -f docker-compose.test.yml down -v

  ci-success:
    name: CI Success
    runs-on: [self-hosted, bloom]
    needs: [lint, unit-tests, build, integration-tests, e2e-tests]
    if: always()
    steps:
      - name: Check all jobs passed
        run: |
          if [[ "${{ needs.lint.result }}" != "success" ]] || \
             [[ "${{ needs.unit-tests.result }}" != "success" ]] || \
             [[ "${{ needs.build.result }}" != "success" ]] || \
             [[ "${{ needs.integration-tests.result }}" != "success" ]] || \
             [[ "${{ needs.e2e-tests.result }}" != "success" ]]; then
            echo "One or more jobs failed"
            exit 1
          fi
          echo "All CI jobs passed!"
```

## Pipeline Visualization

```
┌─────────────────────────────────────────────────────────────┐
│                    PR / Push Trigger                         │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐    ┌──────────┐
        │   Lint   │   │  Unit    │    │  Build   │
        │  & Type  │   │  Tests   │    │          │
        └──────────┘   └──────────┘    └────┬─────┘
                                            │
                              ┌─────────────┼─────────────┐
                              ▼                           ▼
                       ┌──────────────┐          ┌──────────────┐
                       │ Integration  │          │    E2E       │
                       │    Tests     │          │   Tests      │
                       └──────────────┘          └──────────────┘
                              │                           │
                              └─────────────┬─────────────┘
                                            ▼
                                    ┌──────────────┐
                                    │  CI Success  │
                                    │   (gate)     │
                                    └──────────────┘
```

## Package.json Scripts

Add these scripts to root `package.json`:

```json
{
  "scripts": {
    "lint": "turbo lint",
    "typecheck": "turbo typecheck",
    "test": "vitest",
    "test:run": "vitest run",
    "test:coverage": "vitest run --coverage",
    "test:integration": "vitest run --config vitest.integration.config.ts",
    "test:e2e": "playwright test",
    "db:migrate:test": "node scripts/run-migrations.js"
  }
}
```

## Branch Protection Rules

Configure in GitHub: Settings → Branches → Add rule for `main` and `dev`:

- [x] Require status checks to pass before merging
  - [x] ci-success
- [x] Require branches to be up to date before merging
- [x] Require conversation resolution before merging
- [x] Do not allow bypassing the above settings

## Verification Checklist

- [ ] Workflow triggers on PR creation
- [ ] Workflow triggers on push to dev/main
- [ ] All jobs run on self-hosted runner
- [ ] Dependencies are cached between runs
- [ ] Coverage reports appear on PRs
- [ ] Failed checks block merge
- [ ] Concurrent runs are cancelled

## Future Improvements

- [ ] Add security scanning (Snyk, CodeQL)
- [ ] Add dependency update checks (Dependabot)
- [ ] Add bundle size tracking
- [ ] Add visual regression testing
- [ ] Add performance benchmarks

## References

- [GitHub Actions Syntax](https://docs.github.com/en/actions/reference/workflow-syntax-for-github-actions)
- [Self-Hosted Runners](https://docs.github.com/en/actions/hosting-your-own-runners)
- [bloom-desktop CI](https://github.com/Salk-Harnessing-Plants-Initiative/bloom-desktop) - Reference implementation