# Issue: Test Infrastructure Setup

**Epic**: [CI/CD Pipeline with Staging & Production Environments](./EPIC-cicd-deployment-testing.md)
**Priority**: P0
**Dependencies**: None (can be done in parallel with runner setup)
**Labels**: `cicd`, `testing`, `infrastructure`

## Summary

Set up comprehensive testing infrastructure for unit tests, integration tests, and end-to-end tests across the Bloom monorepo.

## Background

Currently, Bloom has no automated testing infrastructure:
- No test framework configured
- No test files exist
- `turbo.json` has a `test` task but no implementation
- Manual QA is the only validation method

## Requirements

### Test Types Needed

| Type | Framework | Scope | Runs Against |
|------|-----------|-------|--------------|
| Unit | Vitest | Functions, components | Mocked dependencies |
| Integration | Vitest | API endpoints | Test database |
| E2E | Playwright | User workflows | Full stack |

### Coverage Requirements
- Unit tests: Core business logic, utilities, React components
- Integration tests: PostgREST API endpoints, database operations
- E2E tests: Critical user journeys (auth, data entry, exports)

## Implementation Plan

### Phase 1: Unit Test Setup

#### 1.1 Install Dependencies

```bash
pnpm add -Dw vitest @vitest/coverage-v8 @vitest/ui
pnpm add -Dw @testing-library/react @testing-library/jest-dom
pnpm add -Dw jsdom happy-dom
```

#### 1.2 Create Root Vitest Config

```typescript
// vitest.config.ts
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./test/setup.ts'],
    include: ['**/*.{test,spec}.{js,ts,jsx,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      exclude: ['node_modules/', 'test/'],
    },
  },
})
```

#### 1.3 Create Test Setup File

```typescript
// test/setup.ts
import '@testing-library/jest-dom'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
})
```

#### 1.4 Add Test Scripts to package.json

```json
{
  "scripts": {
    "test": "vitest",
    "test:run": "vitest run",
    "test:coverage": "vitest run --coverage",
    "test:ui": "vitest --ui"
  }
}
```

#### 1.5 Update turbo.json

```json
{
  "tasks": {
    "test": {
      "dependsOn": ["^build"],
      "inputs": ["src/**", "test/**", "*.config.*"],
      "outputs": ["coverage/**"]
    }
  }
}
```

### Phase 2: Integration Test Setup

#### 2.1 Create Test Database Config

```typescript
// test/integration/setup.ts
import { createClient } from '@supabase/supabase-js'

const TEST_SUPABASE_URL = process.env.TEST_SUPABASE_URL || 'http://localhost:54321'
const TEST_SUPABASE_KEY = process.env.TEST_SUPABASE_ANON_KEY

export const testClient = createClient(TEST_SUPABASE_URL, TEST_SUPABASE_KEY)

export async function resetTestDatabase() {
  // Truncate test tables, reseed if needed
}
```

#### 2.2 Docker Compose for Test Database

```yaml
# docker-compose.test.yml
services:
  db-test:
    image: supabase/postgres:15.8.1.060
    environment:
      POSTGRES_PASSWORD: test_password
      POSTGRES_DB: bloom_test
    ports:
      - "54322:5432"
    tmpfs:
      - /var/lib/postgresql/data  # Fast, ephemeral storage
```

### Phase 3: E2E Test Setup

#### 3.1 Install Playwright

```bash
pnpm add -Dw @playwright/test
npx playwright install --with-deps chromium
```

#### 3.2 Create Playwright Config

```typescript
// playwright.config.ts
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'pnpm dev',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
  },
})
```

#### 3.3 Create E2E Directory Structure

```
e2e/
├── auth.spec.ts           # Login, logout, session tests
├── experiments.spec.ts    # CRUD for experiments
├── fixtures/
│   └── test-user.ts       # Test user setup
└── helpers/
    └── auth.ts            # Auth helper functions
```

## Directory Structure (Final)

```
salk-bloom/
├── vitest.config.ts           # Root Vitest config
├── playwright.config.ts       # Playwright config
├── test/
│   ├── setup.ts               # Test setup file
│   └── integration/
│       └── setup.ts           # Integration test setup
├── e2e/
│   ├── auth.spec.ts
│   └── fixtures/
├── packages/
│   ├── bloom-js/
│   │   └── src/
│   │       └── __tests__/     # Unit tests for bloom-js
│   └── bloom-nextjs-auth/
│       └── src/
│           └── __tests__/     # Unit tests for auth package
├── web/
│   └── src/
│       ├── components/
│       │   └── __tests__/     # Component tests
│       └── lib/
│           └── __tests__/     # Utility tests
└── docker-compose.test.yml    # Test database
```

## Initial Test Files to Create

### Starter Unit Test
```typescript
// packages/bloom-js/src/__tests__/example.test.ts
import { describe, it, expect } from 'vitest'

describe('Example', () => {
  it('should pass', () => {
    expect(1 + 1).toBe(2)
  })
})
```

### Starter E2E Test
```typescript
// e2e/smoke.spec.ts
import { test, expect } from '@playwright/test'

test('homepage loads', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveTitle(/Bloom/)
})
```

## Verification Checklist

- [ ] `pnpm test` runs unit tests
- [ ] `pnpm test:coverage` generates coverage report
- [ ] `pnpm test:e2e` runs Playwright tests
- [ ] Tests run in CI environment
- [ ] Coverage reports upload to PR comments

## Future Improvements

- [ ] Add visual regression testing (Playwright screenshots)
- [ ] Add API contract testing
- [ ] Add performance benchmarks
- [ ] Add mutation testing (Stryker)

## Reference: bloom-desktop Testing Patterns

The [bloom-desktop](https://github.com/Salk-Harnessing-Plants-Initiative/bloom-desktop) repo has established testing patterns we should follow:

| Test Type | Framework | Coverage Target |
|-----------|-----------|-----------------|
| TypeScript Unit | Vitest | 80%+ |
| Python Unit | pytest | 84.5% |
| E2E | Playwright | Critical paths |
| IPC Integration | Custom | Python ↔ TypeScript |

**Useful scripts from bloom-desktop**:
- `npm run test:unit` - TypeScript unit tests
- `npm run test:python` - Python tests
- `npm run test:ipc` - IPC integration tests
- `npm run test:e2e` - Playwright E2E tests

We should adopt similar patterns and scripts for consistency across the Bloom ecosystem.

## References

- [Vitest Documentation](https://vitest.dev/)
- [Playwright Documentation](https://playwright.dev/)
- [Testing Library](https://testing-library.com/)
- [bloom-desktop Testing](https://github.com/Salk-Harnessing-Plants-Initiative/bloom-desktop) - Reference implementation