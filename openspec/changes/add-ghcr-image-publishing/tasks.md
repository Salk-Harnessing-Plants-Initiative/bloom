# Implementation Tasks

Every code task below follows the TDD red → green → refactor cycle. Tasks
labelled **(process)** are documentation/coordination steps with no TDD
applicable. Tasks labelled **(manual)** are operator smoke checks that cannot
be automated and are explicitly flagged as such in the corresponding spec
scenarios.

Tasks are grouped by PR per the proposal's "Multi-PR landing plan":

- **PR-1 (Foundation):** §0–§3
- **PR-2 (Migration):** §4, §5
- **PR-3 (Cutover):** §6–§13

Each PR leaves CI green at the end of its last task.

**npm/pnpm note:** the repo uses npm with workspaces (root
`package-lock.json`; CI invokes `npm ci` at [pr-checks.yml:33](.github/workflows/pr-checks.yml#L33)
and `cd web && npm run build` at [:59](.github/workflows/pr-checks.yml#L59)).
All test invocations below follow that pattern (`cd web && npm run test:unit`).

---

## PR-1 — Foundation

### 0. Prep `pr-checks.yml` so the rest of the migration doesn't break CI

- [ ] 0.1 **Test (red):** add `tests/unit/test_pr_checks_workflow_shape.py`
      asserting:
  - The `docker-build` job no longer contains any `--build-arg NEXT_PUBLIC_*`
    lines.
  - The `compose-health-check` job has a job-level env entry
    `COMPOSE_FILES: "-f docker-compose.prod.yml -f docker-compose.ci.yml"`.
  - Every `docker compose` command in the `compose-health-check` job uses
    `$COMPOSE_FILES` (not hard-coded `-f` flags).
  - A new `web-unit-tests` job exists, runs on `ubuntu-latest`, executes
    `cd web && npm run test:unit`, and has no `needs:` block (runs in
    parallel with other jobs).
  - A Playwright step exists in `compose-health-check` after the stack
    is verified healthy, running `cd web && npm run test:e2e` with
    `TEST_BASE_URL` pointing at the up-stack.
  - `docker-compose.ci.yml` exists at the repo root and declares
    `build:` blocks for `bloom-web`, `langchain-agent`, and `bloommcp`.
  Run `uv run --extra test pytest tests/unit/test_pr_checks_workflow_shape.py -v`
  and observe red.
- [ ] 0.2 **Impl (green):** edit `.github/workflows/pr-checks.yml`:
  - **Do NOT drop the `--build-arg NEXT_PUBLIC_*` lines yet.** They MUST
    stay in the `docker-build` job until PR-3 §6 simultaneously removes
    the matching `ARG NEXT_PUBLIC_*` declarations from
    `web/Dockerfile.bloom-web.prod:9-17`. Removing them in PR-1 breaks
    `next build`'s `/test/page` prerender because `@supabase/ssr`
    instantiates at module load and needs the values baked at build time.
    *(Earlier drafts of this proposal mistakenly scheduled the drop for
    PR-1; that was a sequencing bug, caught when CI on PR #268 failed.)*
  - Hoist `COMPOSE_FILES: "-f docker-compose.prod.yml -f docker-compose.ci.yml"`
    to a job-level env block on `compose-health-check`.
  - Rewrite every `docker compose -f docker-compose.prod.yml ...` command in
    that job (lines ~377-564) as
    `docker compose $COMPOSE_FILES --env-file .env.ci ...`.
  - Add a Playwright step at the end of `compose-health-check` (after
    the existing health-validation steps), exporting
    `TEST_BASE_URL=http://localhost` and running
    `cd web && npm run test:e2e`. Add the Playwright browser cache to
    `actions/cache@v4` for speed. **Pin Node 20 via
    `actions/setup-node@v4` BEFORE the npm/Playwright steps** so the
    runner-default Node version can't drift CI behavior (matches the
    `web-unit-tests` job's pin; flagged by Copilot review).
  - Add a new `web-unit-tests` job:
    ```yaml
    web-unit-tests:
      name: Web Unit Tests (Vitest)
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: actions/setup-node@v4
          with:
            node-version: '20'
            cache: 'npm'
        - run: npm ci
        - run: cd web && npm run test:unit
    ```
- [ ] 0.3 **Impl (green):** create a minimal `docker-compose.ci.yml` at the
      repo root with a `services:` block that declares `build:` for
      `bloom-web`, `langchain-agent`, and `bloommcp` matching today's
      `docker-compose.prod.yml:67-74,113-117,171-175` blocks. (Per
      [design.md Decision 11](design.md), compose merges `build:` over
      base `image:` cleanly.)
- [ ] 0.4 **Refactor:** add a comment block at the top of
      `docker-compose.ci.yml` explaining "for CI / local-without-GHCR
      only; production compose stays `image:`-only" and that
      `test_compose_ci_overlay_parity.py` will enforce parity in PR-3.
- [ ] 0.5 **(process)** Remove the misleading `PR-Ready` and
      `Resolved` labels from issue #107 — the work is still pending
      (PR #122 explicitly deferred GHCR; this proposal picks it up).
      Moved to PR-1 (instead of PR-3 §12) so reviewers reading #107
      during PR-1 / PR-2 review don't see the issue as already-done.
      Run `gh issue edit 107 --remove-label "PR-Ready" --remove-label "Resolved"`;
      confirm via `gh issue view 107 --json labels`.

### 1. Extend the existing Vitest setup in `web/`

**Reconciliation note (staging-as-of-PR-1):** Vitest is **already
installed** in `web/` (`vitest@^4.1.8` per
[web/package.json:65](web/package.json#L65), plus `vite@^5.4.0` and
`vite-tsconfig-paths@^5.1.0`). `web/vitest.config.ts` already exists with
`environment: 'node'` and a colocated-test discovery pattern
(`lib/**/*.test.ts`, `components/**/*.test.{ts,tsx}`,
`app/**/*.test.{ts,tsx}`). The `"test:unit": "vitest run"` script is
already in `web/package.json`. This change extends the existing config
rather than bootstrapping it, and follows the colocated convention seen
at [web/lib/queries/recent-phenotypes-by-cyl-scanner.test.ts](web/lib/queries/recent-phenotypes-by-cyl-scanner.test.ts)
rather than the originally-proposed `web/tests/unit/*` layout.

- [ ] 1.1 **Test (red):** create `web/lib/config/public-config.test.ts`
      (colocated with the future module) with a **real failing**
      assertion that the module is importable:
      ```typescript
      import { describe, it, expect } from 'vitest';
      describe('public-config', () => {
        it('module is importable', async () => {
          await expect(import('./public-config')).resolves.toBeDefined();
        });
      });
      ```
      Run `cd web && npm run test:unit` and confirm it executes (vitest
      already installed) and **fails** because `./public-config` doesn't
      exist yet. Real assertions land in §2.1.
- [ ] 1.2 **Impl (green):** extend the existing `web/vitest.config.ts`
      to add the following entries — **append-only; do not replace or
      regenerate the file**:
  - `setupFiles: ['./vitest.setup.ts']` — wires the env-snapshot
    helper from §1.3.
  - `pool: 'forks'` — per-file process isolation so the `process.env`
    mutations in our new tests don't leak across files. Existing
    colocated tests (`web/lib/queries/*.test.ts`,
    `web/components/recent-phenotypes-by-cyl-scanner/format-times.test.ts`)
    don't mutate `process.env`, so the switch is safe. **Smoke-test:
    both existing tests MUST still pass after the pool change.**
  - Keep `environment: 'node'` as the default (matches existing
    config's stated intent). Our React component test
    (`use-public-config.test.tsx`) and route-handler test
    (`route.test.ts`) use per-file `// @vitest-environment jsdom`
    directives instead of flipping the workspace default.
  - **Append** `'lib/**/__fixtures__/**'` to the existing `exclude`
    array (preserving `node_modules`, `.next`, `e2e`) so the JWT
    fixture helper (§2.5) isn't auto-discovered as a test.
  - **Preserve** the existing
    `// eslint-disable-next-line @typescript-eslint/no-explicit-any`
    cast on `plugins` ([web/vitest.config.ts:18-19](web/vitest.config.ts#L18))
    — it's a TS-stub fix the runtime needs.
- [ ] 1.3 **Impl (green):** create `web/vitest.setup.ts` (workspace
      root, conventional path) with explicit shape:
      ```typescript
      import { beforeEach, afterEach } from 'vitest';
      let snapshot: Record<string, string | undefined>;
      beforeEach(() => { snapshot = { ...process.env } as Record<string, string | undefined>; });
      afterEach(() => {
        for (const k of Object.keys(process.env)) {
          if (!(k in snapshot)) delete process.env[k];
        }
        for (const [k, v] of Object.entries(snapshot)) {
          if (v === undefined) delete process.env[k];
          else process.env[k] = v;
        }
      });
      ```
- [ ] 1.4 **Impl (green):** add `jsdom` to `web/package.json`
      `devDependencies` (vitest is already there; jsdom is not). Used
      by the per-file `// @vitest-environment jsdom` directive on our
      React/Response tests.
- [ ] 1.5 **Refactor:** confirm `test:unit` is wired into the root
      `turbo.json` task graph; add `tasks.test:unit.outputs = ["coverage/**"]`
      if missing. *(Coverage thresholds and `@vitest/coverage-v8`
      install are out of scope for PR-1 — defer to the follow-up
      tracked in §12.3.)*

### 2. Public-config module + boot validator

- [ ] 2.1 **Test (red):** complete `web/lib/config/public-config.test.ts`
      asserting:
  - `getPublicConfig()` returns a record with all 8 expected keys.
  - Mutating `process.env.NEXT_PUBLIC_SUPABASE_URL` between two calls
    causes the second call to reflect the new value.
  - The return type is the exported `PublicConfig` type (via
    `expectTypeOf`).
- [ ] 2.2 **Impl (green):** create `web/lib/config/public-config.ts`
      exporting `PublicConfig` and `getPublicConfig()`. Use a function
      (not module-level `const`) so reads are deferred. Cover all 8 keys.
      Also export `decodeAnonKeyProject(anonKey: string): { ref?: string; iss?: string }`
      with base64url handling (substitute `-`→`+`, `_`→`/`; pad with `=`
      to multiple of 4; `atob` and JSON-parse). Per
      [design.md Decision 14](design.md), this is a non-cryptographic
      sanity-check; document so in JSDoc.
- [ ] 2.3 **Test (red):** `web/lib/config/validate-on-boot.test.ts`
      asserting:
  - In production mode (`NODE_ENV=production`): `validateOnBoot()`
    throws when `NEXT_PUBLIC_SUPABASE_URL` is unset, naming the missing
    key.
  - In production mode: `validateOnBoot()` throws when
    `SUPABASE_URL_HOSTS_ALLOWED` is unset.
  - In production mode: `validateOnBoot()` throws when the parsed
    `SUPABASE_URL_HOSTS_ALLOWED` does not contain
    `process.env.SUPABASE_URL`'s host as a key.
  - In production mode: `validateOnBoot()` does NOT throw when all
    required keys are present and well-formed.
  - **In dev mode (`NODE_ENV !== 'production'`): `validateOnBoot()`
    returns silently even when keys are missing** — preserves the
    existing local-dev fallback pattern.
- [ ] 2.4 **Impl (green):** create `web/lib/config/validate-on-boot.ts`
      exporting `validateOnBoot()` and a helper
      `parseHostsAllowed(raw: string): Map<string, Set<string>>` per
      [design.md Decision 13](design.md). The function checks
      `process.env.NODE_ENV !== 'production'` first and returns early
      if dev. Tests pass.
- [ ] 2.5 **Test (red):** create `web/lib/config/jwt-fixture.test.ts`
      asserting:
  - Calling `makeAnonKey({iss: 'https://bloom-dev.salk.edu'})` (imported
    from `__fixtures__/jwt`) produces a JWT whose payload segment
    contains `-` or `_` (base64url) at least sometimes.
  - Calling `decodeAnonKeyProject` on that JWT returns the expected
    `iss`.
  The fixture itself lives at `web/lib/config/__fixtures__/jwt.ts`
  (excluded from test discovery per §1.2's `exclude` glob); this test
  lives one level up so it IS discovered.
- [ ] 2.6 **Impl (green):** implement the JWT fixture helper at
      `web/lib/config/__fixtures__/jwt.ts` exporting
      `makeAnonKey({iss?, ref?, sub?}): string` for use across §3.1's
      JWT sub-cases. Tests pass.
- [ ] 2.7 **Refactor:** JSDoc on `getPublicConfig`, `validateOnBoot`, and
      `decodeAnonKeyProject` explaining their contracts (call-time
      reads, dev-mode early-exit, non-cryptographic-sanity-check).

### 3. Runtime config endpoint with fence

- [ ] 3.1 **Test (red):** `web/app/api/config/route.test.ts` (colocated with the route handler; prepend `// @vitest-environment jsdom` directive at top of file since the test exercises `Response` objects):
  - The route handler from `web/app/api/config/route.ts` returns
    status 200 and a JSON body matching the full `PublicConfig` shape
    when env is well-formed.
  - The response body's top-level keys are exactly the 8 keys declared
    by `PublicConfig` (structural choke-point assertion — type + handler
    + fixture must co-edit).
  - The route module exports `dynamic = 'force-dynamic'`,
    `revalidate = 0`, and `runtime = 'nodejs'`.
  - The response headers include `Cache-Control: no-store`,
    `Vary: Host`, and `Pragma: no-cache`.
  - The route returns 503 when `SUPABASE_URL_HOSTS_ALLOWED` doesn't
    contain `process.env.SUPABASE_URL`'s host (URL-fence test using a
    stub mapping passed via env).
  - The route returns 503 when `NEXT_PUBLIC_SUPABASE_URL`'s host is not
    in the allow-list for the internal host.
  - The route returns 503 when the anon-key JWT's `iss`/`ref` doesn't
    match `NEXT_PUBLIC_SUPABASE_URL`'s hostname — uses `makeAnonKey()`
    from §2.5 fixture helper.
  - The route returns 503 when the anon-key is not a parseable JWT.
  - The route returns 503 when the anon-key JWT has base64url
    characters (`-`/`_`) and the decoder must handle them correctly —
    test fixture uses `makeAnonKey()` and asserts the response is 200,
    not 503 due to decoder failure.
  - All 503 responses include a body field naming the failure cause.
- [ ] 3.2 **Impl (green):** create `web/app/api/config/route.ts`. Export
      `const dynamic = 'force-dynamic'`, `const revalidate = 0`,
      `const runtime = 'nodejs'`. Implement `GET` that:
  - Calls `getPublicConfig()`.
  - Runs the URL-fence check (per §2.4's helper).
  - Runs the anon-key project-match check (uses `decodeAnonKeyProject()`).
  - Returns 503 with `{ "error": "<cause>" }` body on any fence
    failure, 200 with the config JSON otherwise.
  - Sets all three response headers.
- [ ] 3.3 **Test (red):** add `web/instrumentation.test.ts` (colocated
      with `web/instrumentation.ts` at the workspace root) asserting:
  - The file `web/instrumentation.ts` exists.
  - Its source matches the regex `/import\s*\{[^}]*\bvalidateOnBoot\b[^}]*\}/`
    (import of the named symbol, tolerant to whitespace/order).
  - Its source matches the regex `/\bawait\s+validateOnBoot\s*\(/`
    (actual await-call, not a JSDoc reference; not a `validateOnBoot`
    string in a comment).
  Run and observe **red** (the file doesn't exist yet).
- [ ] 3.4 **Impl (green):** create `web/instrumentation.ts` exporting
      `async function register() { ... }` that imports and calls
      `validateOnBoot()` from `@/lib/config/validate-on-boot`. This is
      the Next.js 16+ standard hook ([Next docs: Instrumentation](https://nextjs.org/docs/app/api-reference/file-conventions/instrumentation)).
- [ ] 3.5 **Refactor:** none expected.

**End of PR-1.** CI green: §0 fixed pr-checks.yml and removed
misleading #107 labels; §1–§3 are all-additive with their own Vitest
tests; `web-unit-tests` job runs and passes; no caller uses the new
module yet. Tests passing: `test_pr_checks_workflow_shape.py`, plus
**5 Vitest test files** (colocated, matching the cross-PR table in
proposal.md):
1. `web/lib/config/public-config.test.ts` (§1.1 import-smoke +
   §2.1 8-key shape)
2. `web/lib/config/validate-on-boot.test.ts` (§2.3 NODE_ENV
   early-exit + URL host-map fence)
3. `web/lib/config/jwt-fixture.test.ts` (§2.5 base64url JWT
   round-trip)
4. `web/app/api/config/route.test.ts` (§3.1 endpoint contract +
   503 fence cases)
5. `web/instrumentation.test.ts` (§3.3 import + await-call regex)

---

## PR-2 — Migration

### 4. Migration lint — no direct `process.env.NEXT_PUBLIC_*` reads outside the config module

This test fences the spec requirement "No Direct NEXT_PUBLIC Reads". It is
committed as `describe.skip`-ed initially (so PR CI stays green during §5's
incremental migration) and unskipped in §5.7.

- [ ] 4.1 **Test (red, committed `describe.skip`):**
      `web/lib/config/no-direct-next-public-reads.test.ts`:
  - Walks every `*.ts` and `*.tsx` file under `web/` excluding:
    - **`web/lib/config/public-config.ts`** — the one module the spec
      explicitly allows to read `process.env.NEXT_PUBLIC_*`. **Without
      this exclusion, the scanner self-flags the legitimate module
      once §2.2 lands.**
    - `node_modules/`, `.next/`, `tests/`, `*.env*`, `Dockerfile*`,
      `openspec/**`, and `next-env.d.ts`.
    - Any path matching `lib/**/__fixtures__/**` (per §1.2).
  - **Strips line comments (`//`) and block comments (`/* */`) before
    matching**, so the commented `process.env.NEXT_PUBLIC_*` lines in
    `web/app/api/gitlab/user/route.ts:15` and
    `web/app/api/oauth/gitlab/exchange/route.ts:43,96` don't
    false-positive.
  - Asserts no remaining file contains `process.env.NEXT_PUBLIC_`.
  - Wrapped in `describe.skip(...)` until §5.7.

### 5. Migrate every NEXT_PUBLIC consumer

15 files migrate from `process.env.NEXT_PUBLIC_*` to `getPublicConfig()` /
`usePublicConfig()`, plus `client-info` is deleted and commented reads are
stripped.

- [ ] 5.1 **Test (red):** `web/lib/config/use-public-config.test.tsx` (colocated with the hook; prepend `// @vitest-environment jsdom` directive at top since the test renders React components)
      covering both happy and failure paths:
  - Renders two client components inside a single React tree, each
    calling `usePublicConfig()`. Mocks global `fetch` to a resolved JSON
    response. Asserts exactly **one** `GET /api/config` is issued and
    both components receive the same `PublicConfig` instance.
  - Asserts the tree suspends until the fetch resolves (`waitFor`).
  - Renders a third component where `fetch` rejects with HTTP 500;
    asserts the Suspense fallback's error variant renders with a
    "refresh to retry" hint.
  - Wraps the same tree in `<React.StrictMode>` and asserts the
    single-fetch invariant still holds under double-render.
  - Renders a component calling `usePublicConfig()` **outside** the
    provider; asserts the thrown error names the missing provider.
- [ ] 5.2 **Impl (green):** create `web/lib/config/use-public-config.ts`
      with `PublicConfigProvider` (React context + `use()` to suspend),
      `usePublicConfig()` hook, and a `<Fallback>` component. Add the
      provider once in `web/app/layout.tsx`.

- [ ] 5.3 **Migrate server-only callers.** For each module:
  - **Test (red):** Vitest test that imports the module's exported
    function with a fixture `getPublicConfig()` and asserts the same
    observable behavior as before migration.
  - **Impl (green):** Replace `process.env.NEXT_PUBLIC_*` reads with
    `getPublicConfig()` reads.

  Per-file checklist:
  - [ ] 5.3.1 `web/middleware.ts` (lines 9, 10 — runtime `nodejs`)
  - [ ] 5.3.2 `web/lib/supabase/server.ts` (lines 16, 21)
  - [ ] 5.3.3 `web/lib/supabase/storage-url.ts` (line 6)
  - [ ] 5.3.4 `web/app/api/debug_users/route.ts` (line 4)
  - [ ] 5.3.5 `web/app/api/gitlab/logged-in/route.ts` (line 15)
  - [ ] 5.3.6 `web/app/api/gitlab/projects/route.ts` (line 16)
  - [ ] 5.3.7 `web/app/api/health/route.ts` (line 19 — **critical: this
        is the deploy `--wait` health check**)
  - [ ] 5.3.8 `web/app/api/ping_db/route.ts` (line 3)
  - [ ] 5.3.9 `web/app/api/oauth/gitlab/initiate/route.ts` (lines 26, 82)
  - [ ] 5.3.10 `web/app/api/oauth/gitlab/store/route.ts` (line 9)

- [ ] 5.4 **Migrate client callers.** For each module:
  - **Test (red):** Vitest test that renders the component inside
    `PublicConfigProvider` with a fixture config; asserts current
    behavior preserved.
  - **Impl (green):** Replace direct env reads with `usePublicConfig()`.

  Per-file checklist:
  - [ ] 5.4.1 `web/lib/supabase/client.ts` (lines 11, 14, 15) — the
        `createClientSupabaseClient` function must move from module-load
        invocation to a per-component hook call.
  - [ ] 5.4.2 `web/components/expression-lib/scrna-client.ts` (line 31)
  - [ ] 5.4.3 `web/app/test/page.tsx` (line 14 — likely test-only code;
        confirm before migrating or delete)
  - [ ] 5.4.4 **`web/components/mcp-chat-client.tsx` (line 155) — the
        trickiest migration in §5.** The `API_BASE_URL` module-level
        constant (currently `((process.env.NEXT_PUBLIC_MCP_URL as string) || "http://localhost:5002").replace(/\/$/, "")`)
        is captured at import time and reused across all component
        renders. After migration it MUST become per-render: read
        `usePublicConfig().mcpUrl`, derive the trimmed value inside the
        component body, and pass to any fetch/WebSocket constructor.
        Watch for: WebSocket connections opened at module load (none
        today — verify), fetch calls in non-React utility functions
        called from the component (must accept the URL as a parameter),
        and any tests that mock `process.env.NEXT_PUBLIC_MCP_URL` (must
        switch to the `usePublicConfig` provider).

- [ ] 5.5 **Delete the redundant client-info endpoint.**
  - [ ] 5.5.1 Search for callers: `rg -n "/api/client-info" web/`.
        Migrate any to `/api/config`.
  - [ ] 5.5.2 Delete `web/app/api/client-info/route.ts`.

- [ ] 5.6 **Strip commented `process.env.NEXT_PUBLIC_*` reads.**
  - [ ] 5.6.1 `web/app/api/gitlab/user/route.ts:15` — remove commented line.
  - [ ] 5.6.2 `web/app/api/oauth/gitlab/exchange/route.ts:43,96` — remove
        commented lines.

- [ ] 5.7 **Unskip §4's lint test.** Remove `describe.skip` from
      `no-direct-next-public-reads.test.ts`. Run
      `cd web && npm run test:unit`; if any offenders remain, the
      failure lists them — migrate.

- [ ] 5.8 **Test (red→green):** `web/lib/config/no-skipped-tests.test.ts`
      asserts that `web/lib/config/no-direct-next-public-reads.test.ts`
      contains zero occurrences of `describe.skip` or `test.skip` or
      `it.skip`. Initially red (the `.skip` is still present until §5.7);
      goes green after §5.7.

**End of PR-2.** CI green: bundle still bakes (Dockerfile ARGs untouched);
all code reads through the new module/hook; client-info deleted; commented
reads removed; migration-lint and no-skipped-tests both green.

---

## PR-3 — Cutover

### 6. Drop the build-time bake

- [ ] 6.1 **Test (red):** `tests/unit/test_dockerfile_no_next_public_args.py`
      asserts:
  - `web/Dockerfile.bloom-web.prod` contains no `ARG NEXT_PUBLIC_*` lines.
  - `web/Dockerfile.bloom-web.prod` contains no `ENV NEXT_PUBLIC_*` lines.
  - `docker-compose.prod.yml`'s `bloom-web.build.args` block does not
    contain any `NEXT_PUBLIC_*` keys (after the migration, but still
    `build:` because §8 hasn't flipped to `image:` yet).
  - `docker-compose.ci.yml`'s `bloom-web.build.args` block (if any) does
    not contain any `NEXT_PUBLIC_*` keys.
  Observe red.
- [ ] 6.2 **Impl (green):** delete lines 9-17 of
      `web/Dockerfile.bloom-web.prod` (the four `ARG NEXT_PUBLIC_*` and
      the four `ENV NEXT_PUBLIC_*=$...` lines).
- [ ] 6.3 **Impl (green):** delete the `NEXT_PUBLIC_*` keys from the
      `bloom-web.build.args` block in `docker-compose.prod.yml:70-74` and
      the same keys from the overlay if present.
- [ ] 6.3a **Impl (green):** delete the three
      `--build-arg NEXT_PUBLIC_*` flags from
      `.github/workflows/pr-checks.yml`'s `Build bloom-web image` step
      (deferred from §0.2 because removing them before §6.2 drops the
      matching `ARG` lines causes `next build` to fail on the
      `/test/page` prerender). Then update
      `tests/unit/test_pr_checks_workflow_shape.py` to add an invariant
      that asserts these flags are absent.
- [ ] 6.4 **Manual smoke check** *(manual — flagged in proposal.md scope;
      spec scenario "Same image serves different config in different
      environments" is verified by §11.7's Playwright e2e, not by this
      manual step)*: rebuild the image locally, run with staging values,
      confirm chat page hydrates and Supabase requests go to the staging
      URL. Repeat with prod values from the same image.

### 7. GHCR build-and-push job

- [ ] 7.1 **Test (red):** `tests/unit/test_deploy_workflow_ghcr_shape.py`
      parses `.github/workflows/deploy.yml` with PyYAML and asserts:
  - A job named `build-images` exists with `runs-on: ubuntu-latest`.
  - The job declares `concurrency.group: build-images-${{ github.ref }}`
    and `cancel-in-progress: false`.
  - Permissions block includes `packages: write`.
  - For each of `bloom-web`, `langchain-agent`, `bloommcp`:
    - A `docker/build-push-action` step is present with the correct
      `context:` and `file:` per
      [design.md Decision 10](design.md).
    - The step tags with both `sha-<short>` and `staging`.
    - The step does NOT include `--force`, `force_push: true`, or any
      flag that would allow overwriting an immutable tag.
  - The job outputs `image_tag: sha-<short>`.
  - The `deploy-staging` job declares `needs: [build-images]`.
  - The `deploy-staging` job env block exports
    `IMAGE_TAG: ${{ needs.build-images.outputs.image_tag }}`.
- [ ] 7.2 **Impl (green):** add the `build-images` job to
      `.github/workflows/deploy.yml` per §7.1's shape. Use
      `docker/login-action@v3` against `ghcr.io` with
      `${{ github.actor }}` / `${{ secrets.GITHUB_TOKEN }}` and three
      `docker/build-push-action@v6` steps with per-service contexts.

### 8. Switch compose to `image:` and verify CI overlay

- [ ] 8.1 **Test (red):** `tests/unit/test_compose_ghcr_refs.py` parses
      `docker-compose.prod.yml` with PyYAML and asserts:
  - `services.bloom-web.image`, `services.langchain-agent.image`, and
    `services.bloommcp.image` each match
    `^ghcr\.io/\${IMAGE_NAMESPACE}/[^:]+:\${IMAGE_TAG:-staging}$`
    (PyYAML strips outer quotes on parse, so the regex tests the
    *post-parse* string; YAML-quoted values like
    `"ghcr.io/${IMAGE_NAMESPACE}/bloom-web:${IMAGE_TAG:-staging}"` are
    accepted because the quotes are stripped before regex matching).
  - None of those three services have a `build:` block in prod compose.
  - **All three services** (bloom-web, langchain-agent, bloommcp) have
    an `environment.BLOOM_IMAGE_SHA` set to `${IMAGE_TAG:-staging}`.
  - `IMAGE_NAMESPACE=salk-harnessing-plants-initiative` is declared in
    `.env.prod.defaults` and `.env.staging.defaults`.
  - `IMAGE_TAG=staging` is declared in both defaults files.
- [ ] 8.2 **Impl (green):** edit `docker-compose.prod.yml`:
  - `bloom-web`, `langchain-agent`, `bloommcp` (lines 67, 114, 172):
    replace `build: ...` with
    `image: ghcr.io/${IMAGE_NAMESPACE}/<service>:${IMAGE_TAG:-staging}`.
  - Add `BLOOM_IMAGE_SHA: ${IMAGE_TAG:-staging}` to **all three**
    services' `environment:` blocks.
- [ ] 8.3 **Impl (green):** add `IMAGE_NAMESPACE=salk-harnessing-plants-initiative`
      and `IMAGE_TAG=staging` to `.env.prod.defaults` and
      `.env.staging.defaults`.
- [ ] 8.4 **Test (red):** `tests/unit/test_compose_ci_overlay_parity.py`
      asserts: for every service in `docker-compose.prod.yml` with
      `image: ghcr.io/${IMAGE_NAMESPACE}/...`,
      `docker-compose.ci.yml` declares a corresponding `build:` block
      matching the per-service context/file from §7.1.
- [ ] 8.5 **Impl (green):** confirm/update the CI overlay from §0.3 so
      it satisfies §8.4 against the new prod compose.
- [ ] 8.6 **Test (red):**
      `tests/unit/test_image_tag_resolves_in_compose_config.py` —
      behavioral test (not just structural):
  - With `IMAGE_TAG=sha-abcd123` and
    `IMAGE_NAMESPACE=salk-harnessing-plants-initiative` exported, run
    `docker compose -f docker-compose.prod.yml config` as a subprocess
    and assert the rendered YAML contains
    `image: ghcr.io/salk-harnessing-plants-initiative/bloom-web:sha-abcd123`
    for each custom service.
  - With `IMAGE_TAG` unset (only `IMAGE_NAMESPACE` set), run the same
    command and assert the rendered YAML contains `:staging` for each
    custom service.
  - With `IMAGE_TAG=sha-abcd123` exported, assert the rendered YAML
    contains `BLOOM_IMAGE_SHA: sha-abcd123` for each custom service.
  This test gates §8.5 and must pass before §9.

  **CI job placement:** this test invokes `docker compose` as a
  subprocess, which **requires Docker on the runner**. The existing
  `python-audit` job (which runs `pytest tests/unit/`) does NOT have
  Docker installed. Two options for §0.2 wiring (pick one during
  implementation):
  - **A — Move this single test** to a new pytest invocation inside the
    `compose-health-check` job (which has Docker). Use a Pytest marker
    (`@pytest.mark.docker`) and selector
    (`pytest -m docker tests/unit/test_image_tag_resolves_in_compose_config.py`)
    so `python-audit` skips it via `-m "not docker"`.
  - **B — Skip gracefully** via
    `@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available")`.
    This loses the assertion in `python-audit` but keeps a single test
    file; `compose-health-check` re-runs without skip.
  Recommend **A** (explicit and discoverable). Either way, §0.1's
  workflow-shape test must be updated to reflect the chosen wiring.

### 9. Pin third-party images by digest

- [ ] 9.1 **Test (red):** `tests/unit/test_compose_thirdparty_pinned.py`
      asserts: for every `image:` value in `docker-compose.prod.yml`
      whose registry prefix is not `ghcr.io/${IMAGE_NAMESPACE}/`, the
      value matches `.+:.+@sha256:[a-f0-9]{64}`.
- [ ] 9.2 **Impl (green):** for each of caddy, kong, supabase/realtime,
      minio/minio, supabase/storage-api, supabase/postgres,
      supabase/supavisor, supabase/studio, darthsim/imgproxy,
      supabase/postgres-meta:
  - Cross-check the existing tag against
    [issue #56's tracking comment](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/56)
    — if a CVE-clean upgrade has landed (PRs #91, #94, etc.), bump the
    tag first.
  - Run `docker pull <image>:<tag>` and
    `docker inspect --format='{{index .RepoDigests 0}}' <image>:<tag>`
    to capture the digest.
  - Append `@sha256:<digest>` to the `image:` line in
    `docker-compose.prod.yml`.
- [ ] 9.3 **Manual smoke check** *(manual)*: locally `docker compose pull`
      against the updated file; verify every image resolves.
- [ ] 9.4 **(process)** file or update a tracking comment on issue #56
      listing each pinned image's tag+digest. Add `security` and
      `infrastructure` labels to #56 since it currently has none.
- [ ] 9.5 **Forward-compat note (caddy):** at the time of writing this
      proposal, there is an in-flight branch
      [`chore/caddy-acme-dns-cloudflare`](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/tree/chore/caddy-acme-dns-cloudflare)
      that adds `caddy/Dockerfile` (custom caddy build with the
      Cloudflare DNS plugin for ACME DNS-01). **If that branch merges
      into `staging` before PR-3 lands**, the caddy entry MUST move
      from this §9.2 third-party-pin list to the §7/§8 custom-services
      flow:
  - Add a 4th `docker/build-push-action` step to `build-images`
    (§7.2) with `context: ./caddy` and `file: ./caddy/Dockerfile`.
  - Update `docker-compose.prod.yml` (§8.2) to use
    `image: ghcr.io/${IMAGE_NAMESPACE}/caddy:${IMAGE_TAG:-staging}`
    instead of the third-party `caddy:2.11.2-alpine` tag.
  - Add a `build:` block for caddy in `docker-compose.ci.yml` (§8.5).
  - Update `test_compose_ghcr_refs.py` (§8.1) and
    `test_compose_thirdparty_pinned.py` (§9.1) to expect caddy in the
    custom-services bucket, not the third-party bucket.
  - **Do NOT** add `BLOOM_IMAGE_SHA` to caddy's env block — caddy
    doesn't write analyses, so the SHA-traceability plumbing (§8.2)
    stays scoped to bloom-web/langchain-agent/bloommcp.
  - File the §12.10 caddy migration tracking issue when this happens.

  If that branch is still in flight when PR-3 lands, leave caddy in the
  third-party-pin list and file §12.10 anyway as a forward note.

### 10. Staging deploy pulls + rollback uses prior SHA (or aborts)

- [ ] 10.1 **Test (red):** extend
      `tests/unit/test_deploy_workflow_ghcr_shape.py` — assert:

  **Forward staging deploy** (around `deploy.yml:638`):
  - A `docker login ghcr.io` step exists and runs before any compose
    command.
  - A `docker compose ... pull` step exists and runs before `up -d`.
  - The `up -d` step does NOT include `--build`.
  - The `up -d` step includes `--remove-orphans --wait --wait-timeout 600`.

  **Pull-failure fence:**
  - The `up -d` step has no fallback that runs if `pull` fails (no
    `if: always()` or unconditional run).
  - Alternative: a single bash block does `pull && up -d` so the
    `&&` short-circuit is the fence.

  **Auth-failure fence:**
  - The `docker login ghcr.io` step has no error-suppression
    (`continue-on-error: true` is absent).

  **Staging "Save previous SHA" step** (around `deploy.yml:575`):
  - Writes `IMAGE_TAG` to `${STAGING_DEPLOY_PATH}.state/previous_image_tag`
    alongside the existing `.state/previous_sha` write at line 580.

  **Production "Save previous SHA" step** (around `deploy.yml:152`):
  - Writes `IMAGE_TAG` to `${PROD_DEPLOY_PATH}.state/previous_image_tag`
    alongside the existing `.state/previous_sha` write at line 157.

  **Staging rollback** (around `deploy.yml:829-853`, where the existing rollback step reads `${STAGING_DEPLOY_PATH}.state/previous_sha` at line 838):
  - The rollback bash block checks for the existence and non-emptiness
    of `${STAGING_DEPLOY_PATH}.state/previous_image_tag`.
  - If present: exports `IMAGE_TAG=$(cat ...)`, runs
    `docker compose pull` before `up -d`, no `--build`, with
    `--remove-orphans --wait --wait-timeout 300`.
  - If missing or empty: exits non-zero with the GitHub Actions error
    annotation `::error::No previous_image_tag captured — manual recovery required (do NOT auto-fallback to staging)`,
    and does NOT attempt `docker compose pull` or `up -d`.

  **Production rollback** (around `deploy.yml:438-466`, where the existing rollback step reads `${PROD_DEPLOY_PATH}.state/previous_sha` at line 449):
  - Same shape as staging rollback (capture + abort-on-missing), using
    `${PROD_DEPLOY_PATH}` instead of `${STAGING_DEPLOY_PATH}`.

  **`workflow_dispatch.inputs.image_tag` for manual recovery** (around
  `deploy.yml:8-25`, the existing `workflow_dispatch.inputs` block with
  `environment` and `runner`):
  - A new `image_tag` input is declared with `required: false` and
    `description: 'Override image tag (e.g. sha-abcd123) for manual
    rollback when previous_image_tag is missing. Leave blank for
    default behavior.'`.
  - When `inputs.image_tag` is set (non-empty), the deploy job exports
    `IMAGE_TAG: ${{ inputs.image_tag }}` AFTER the `build-images` job's
    output (so manual override wins).
  - When `inputs.image_tag` is blank (default), the job continues to
    use `IMAGE_TAG: ${{ needs.build-images.outputs.image_tag }}`.

- [ ] 10.2 **Impl (green):** apply the deploy.yml edits per §10.1.
  - **Save previous SHA edits:** add `IMAGE_TAG` capture to both the
    prod save step (around line 152-158) and the staging save step
    (around line 575-581), each writing to its corresponding
    `*_DEPLOY_PATH.state/previous_image_tag`.
  - **Rollback blocks** use this pattern (substitute `PROD` /
    `STAGING` per environment to match the existing path-secret
    convention):
    ```bash
    if [ -s "${PROD_DEPLOY_PATH}.state/previous_image_tag" ]; then
      IMAGE_TAG=$(cat "${PROD_DEPLOY_PATH}.state/previous_image_tag")
      # pull + up -d ... --wait
    else
      echo "::error::No previous_image_tag captured — manual recovery required (do NOT auto-fallback to staging)"
      echo "Operator: choose a SHA via 'git log' or GHCR's package UI, then re-run with workflow_dispatch setting IMAGE_TAG=sha-<chosen>"
      exit 1
    fi
    ```
  - Path-secret convention matches the existing `previous_sha` path
    pattern: `${{ secrets.PROD_DEPLOY_PATH }}` at
    [deploy.yml:157,449](.github/workflows/deploy.yml#L157) and
    `${{ secrets.STAGING_DEPLOY_PATH }}` at
    [deploy.yml:580,838](.github/workflows/deploy.yml#L580).
    *(Note: `${PROD_DEPLOY_PATH}` in the bash heredoc above is the
    runtime-expanded form of the GHA `${{ secrets.PROD_DEPLOY_PATH }}`
    expression that the existing deploy.yml steps interpolate into
    their `cd ${{ secrets.PROD_DEPLOY_PATH }}` step prefix — see
    [deploy.yml:448](.github/workflows/deploy.yml#L448) for the same
    pattern in the existing prod rollback.)*
  - **Add `image_tag` to `workflow_dispatch.inputs`** at
    [deploy.yml:9-25](.github/workflows/deploy.yml#L9) per §10.1. In
    the deploy job's env block, set
    `IMAGE_TAG: ${{ inputs.image_tag != '' && inputs.image_tag || needs.build-images.outputs.image_tag }}`
    so a manual `workflow_dispatch` with `image_tag=sha-abcd123`
    overrides the freshly-built tag.

### 11. Cross-environment fence + env-defaults + PROD_SETUP

- [ ] 11.1 **Test (red):** `tests/unit/test_env_cross_check.py` asserts:
  - `.env.staging.defaults` and `.env.prod.defaults` declare distinct
    `NEXT_PUBLIC_SUPABASE_URL` values. *(Already true today; fences
    regression.)*
  - The same files declare distinct `SUPABASE_COOKIE_NAME` values.
    *(NOT true today; §11.2 fixes it.)*
  - Both files declare `SUPABASE_URL_HOSTS_ALLOWED` matching the format
    in [design.md Decision 13](design.md).
  - The expected `SUPABASE_URL` (server-internal) appears as a key in
    `SUPABASE_URL_HOSTS_ALLOWED` and the corresponding
    `NEXT_PUBLIC_SUPABASE_URL`'s host appears in the value set.
- [ ] 11.2 **Impl (green):**
  - Update `.env.staging.defaults`: change `SUPABASE_COOKIE_NAME` from
    `sb-bloom-auth-token` to `sb-bloom-staging-auth-token`. **This
    invalidates every currently-active staging session on first deploy
    after PR-3**; documented in §11.5 PROD_SETUP edit and the PR-3
    description.
  - ~~Add `SUPABASE_URL_HOSTS_ALLOWED` to `.env.staging.defaults` and
    `.env.prod.defaults`.~~ **MOVED TO PR-1** along with the matching
    `SUPABASE_URL_HOSTS_ALLOWED: ${SUPABASE_URL_HOSTS_ALLOWED}` entry
    in `docker-compose.prod.yml`'s `bloom-web.environment:` block.
    Both files now declare:
    - prod: `SUPABASE_URL_HOSTS_ALLOWED=kong:8000=bloom-dev.salk.edu`
    - staging: `SUPABASE_URL_HOSTS_ALLOWED=kong:8000=staging-bloom-dev.salk.edu:8443`
    Reason: `web/instrumentation.ts` ships in PR-1 §3 and runs
    `validateOnBoot()` at every production boot. Without the var
    present in the container env, every prod/staging container would
    crash on startup with "Missing required env:
    SUPABASE_URL_HOSTS_ALLOWED" — incl. PR CI's compose-health-check
    stack. PR-3 §11 still owns the cookie-name divergence and the
    `test_env_cross_check.py` regression guard.
- [ ] 11.3 **Test (red):**
      `tests/unit/test_supabase_url_hosts_allowed_format.py` asserts
      that for both defaults files:
  - `SUPABASE_URL_HOSTS_ALLOWED` is parseable per Decision 13's format
    (`<host>=<host>[,<host>=<host>]*`).
  - `URL(SUPABASE_URL).host` appears as a key in the parsed mapping.
  - `URL(NEXT_PUBLIC_SUPABASE_URL).host` appears in the value set for
    that key.
  - The value contains no `$` characters (which would trigger compose
    interpolation).
- [ ] 11.4 **Impl (green):** §11.2's defaults edits should already
      satisfy §11.3 if Decision 13's format is implemented correctly.
- [ ] 11.5 **(process)** edit `PROD_SETUP.md`: add a new
      `## 📦 Container Registry & Image Tags` section before STEP 2
      containing:
  - The GHCR login command
    (`docker login ghcr.io -u <gh-username> --password-stdin <<< $GHCR_READ_TOKEN`).
  - The `IMAGE_TAG` env var semantics (default `staging`, override per
    deploy or rollback).
  - **One-time staging session invalidation** notice from §11.2.
  - **Rollback re-invalidates sessions again** — note that rolling
    back §11.2 specifically requires another session-invalidation
    notice.
  - **Rollback across the cutover boundary** runbook (per
    [design.md Decision 6](design.md)): if rolling back to a SHA
    before the GHCR migration, the rollback uses the pre-GHCR compose
    file (with `build:` blocks) and runs `up -d --build` against it.
  - **Manual rollback when `previous_image_tag` is missing** runbook
    with **exact CLI syntax** (operators under stress shouldn't have to
    invent it):
    ```bash
    # 1. List recent images on GHCR
    gh api -X GET /orgs/salk-harnessing-plants-initiative/packages/container/bloom-web/versions \
      --jq '.[0:10] | .[] | {name: .name, created: .created_at, tags: .metadata.container.tags}'

    # 2. Or check git log for the last known-good SHA
    git log --oneline -20 staging

    # 3. Trigger the rollback deploy with the chosen SHA
    gh workflow run deploy.yml \
      -f environment=staging \
      -f image_tag=sha-<chosen-7-chars>

    # (For production: -f environment=production)
    ```
  - **Pre-merge label cleanup** reminder: if migrating from a deploy
    that pre-dated the GHCR cutover, ensure issue #107's `PR-Ready` and
    `Resolved` labels are removed (handled mechanically by §0.5).
- [ ] 11.6 **Test (red→green):** Playwright e2e
      `web/e2e/runtime-config.spec.ts`:
  - Starts a compose stack via the existing `compose-health-check`
    invocation (or a Playwright `webServer` block referencing the
    up-stack).
  - Assertions:
    - `GET /api/config` returns 200 and JSON with the expected
      `supabaseUrl` for the staging-env CI stack.
    - The built JS bundle (downloaded via the page) does NOT contain
      the baked staging URL as a literal string (greps the response
      body for `bloom-dev.salk.edu` — should not match).
    - Restart the stack with a different `NEXT_PUBLIC_SUPABASE_URL`
      and confirm `/api/config`'s response changes (this may require
      Playwright's `globalSetup` or a custom `webServer` config —
      acceptable to mark as `.skip` if the in-CI restart proves too
      complex, with the assertion captured in a follow-up).
    - Starting the stack with empty `SUPABASE_URL_HOSTS_ALLOWED` and
      `NODE_ENV=production` asserts the container exits non-zero
      (validates `validateOnBoot`'s boot-fail fence via the
      instrumentation hook).

### 12. Archive `implement-cicd-pipeline` + file successor issues

- [ ] 12.1 **(process)** Manual confirmation: read
      `openspec/changes/implement-cicd-pipeline/proposal.md` one last
      time; confirm with the user that archiving is acceptable.
- [ ] 12.2 **(process)** File §12.3, §12.4, §12.6, §12.7 issues BEFORE
      running the archive (so §12.5's `--skip-specs` rationale points at
      live issues).
- [ ] 12.3 **(process)** File a new GitHub issue titled "CI/test work
      previously bundled in implement-cicd-pipeline." The issue body
      MUST include Decision 12's per-requirement table verbatim,
      naming each of the 12 ADDED requirements and its status
      (LANDED / PARTIAL / OPEN). Labels: `cicd`, `tracking`.
- [ ] 12.4 **(process)** File a new GitHub issue titled "Atomic
      analysis-output writes + stop_grace_period for bloommcp/langchain"
      covering the deferred in-flight-write safety concern from
      [design.md Risks](design.md). Issue body MUST enumerate the
      concrete write paths and the proposed mitigations:
  - **bloommcp** writes to bind-mounted volumes
    `BLOOM_OUTPUT_DIR` (`/app/data/ANALYSIS_OUTPUT`),
    `BLOOM_PLOTS_DIR` (`/app/data/PLOTS_DIR`), and `BLOOM_TRAITS_DIR`
    (`/app/data/SLEAP_OUT_CSV`) per [docker-compose.prod.yml:214-217](docker-compose.prod.yml#L214-L217).
    Real write sites in `bloommcp/storage/writer.py::AnalysisWriter.commit()`
    and workflow modules under `bloommcp/tools/workflows/`.
  - **langchain-agent** writes to `BLOOM_PLOTS_DIR` per
    [docker-compose.prod.yml:152-153](docker-compose.prod.yml#L152-L153)
    via `langchain/helpers/plot_renderer.py::render_and_save`.
  - **Mitigations to evaluate:**
    - `stop_grace_period: 30s` in compose to give in-flight writes
      time to complete before SIGKILL.
    - Atomic temp+rename write pattern (`fd, tmp = mkstemp(dir=…);
      write; rename(tmp, final)`) in both services' result-writer
      modules so a SIGTERM mid-write leaves no torn files.
    - Best-effort flush on SIGTERM via Python's `signal.signal(SIGTERM,
      flush_handler)`.
  - Labels: `data-integrity`, `infrastructure`.
- [ ] 12.5 **(process)** Run
      `openspec archive implement-cicd-pipeline --skip-specs --yes`.
      Justified per [design.md Decision 12](design.md)'s
      per-requirement audit table.
- [ ] 12.6 **(process)** File a new GitHub issue titled
      "Stamp BLOOM_IMAGE_SHA into bloommcp/langchain analysis outputs"
      covering the deferred per-result traceability work. Issue body
      MUST enumerate the three design pieces:
  - bloommcp `Manifest` schema v2 migration (current schema is
    `extra="forbid"` per `bloommcp/storage/schema.py:20-22`; needs
    bumping `CURRENT_SCHEMA_VERSION` and a v1→v2 migration in
    `validate_schema()`; field placement choice between top-level
    `Manifest` vs `VersionEntry`).
  - langchain plot-descriptor format decision (PNG metadata via
    `matplotlib.Figure.savefig(metadata={...})` vs `<plot>.png.json`
    sidecar — recommend metadata for rename-robustness).
  - Adding a 9th `imageSha` key to `PublicConfig` so `/api/health` can
    report it without violating "No Direct NEXT_PUBLIC Reads"
    (which forbids `process.env.BLOOM_IMAGE_SHA` reads outside the
    config module).
  Labels: `data-integrity`, `traceability`, `tracking`.
- [ ] 12.7 **(process)** File a new GitHub issue titled
      "Cross-environment configuration safety — umbrella tracking"
      covering the broader data-integrity risk class beyond this
      change's three fences. Tracks future regressions/related vectors
      (storage URL, MCP URL, GitLab OAuth callbacks). Labels:
      `data-integrity`, `tracking`.
- [ ] 12.10 **(process)** File a new GitHub issue titled
      "Migrate caddy from third-party image to GHCR custom build" if
      branch [`chore/caddy-acme-dns-cloudflare`](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/tree/chore/caddy-acme-dns-cloudflare)
      (or any successor that introduces `caddy/Dockerfile`) lands on
      `staging` before or after PR-3 merges. Issue body MUST enumerate
      the §9.5 forward-compat checklist:
  - Add caddy to the `build-images` matrix (§7.2 shape).
  - Flip `docker-compose.prod.yml` caddy entry from third-party
    `image: caddy:2.11.2-alpine@sha256:...` to
    `image: ghcr.io/${IMAGE_NAMESPACE}/caddy:${IMAGE_TAG:-staging}`.
  - Add caddy `build:` block to `docker-compose.ci.yml`.
  - Update `test_compose_ghcr_refs.py` + `test_compose_thirdparty_pinned.py`
    to expect caddy in the custom-services bucket.
  - Caddy does NOT need `BLOOM_IMAGE_SHA` (no analysis outputs).
  Labels: `infrastructure`, `cicd`, `tracking`. If the branch has
  already merged when PR-3 lands, this issue is closed as part of
  PR-3 (§9.5 forward-compat work absorbs it inline).
- [ ] 12.8 **(process)** Run `openspec validate --all --strict` and
      confirm cross-change validation still passes after the archive.
      *(Issue #107 label cleanup was moved to PR-1 §0.5 so reviewers
      reading the issue during PR-1/PR-2 review don't see misleading
      `PR-Ready`/`Resolved` labels.)*

### 13. Validation

- [ ] 13.1 Run `openspec validate add-ghcr-image-publishing --strict` and
       fix any reported issues.
- [ ] 13.2 Confirm `cd web && npm run test:unit`,
       `uv run --extra test pytest tests/unit/ -v`, and
       `uv run --extra test pytest tests/integration/ -v` all pass on a
       fresh checkout of PR-3.
- [ ] 13.3 Run the Playwright e2e suite (§11.6) via the CI
       `compose-health-check` job and confirm green.
- [ ] 13.4 Confirm CI summary at PR-3 merge shows non-zero counts for
       every new test file (sanity check against silent `.skip`
       reintroduction).
- [ ] 13.5 Run the existing `pre-merge` skill workflow against the
       branch before opening PR-3.

**End of PR-3.** GHCR pull-not-build is live on staging; rollback handles
the new model (capture-or-abort); cross-environment fence is enforced at
boot and at every `/api/config` request; `BLOOM_IMAGE_SHA` env plumbing is
in place for the deferred per-result stamping work;
`implement-cicd-pipeline` is archived; PROD_SETUP.md documents the new
operator surface; this change is ready to be archived after deploy
verification.
