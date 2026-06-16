## ADDED Requirements

### Requirement: Runtime Public Config Resolution

`bloom-web` MUST resolve every public configuration value from the environment at request time and MUST NOT inline public configuration values into the static JavaScript bundle shipped to browsers, so one image runs in any environment without rebuild.

#### Scenario: Same image serves different config in different environments

- **GIVEN** a single `bloom-web` image built with no environment-specific build args
- **WHEN** that image is run with `NEXT_PUBLIC_SUPABASE_URL=https://api.bloom-staging.salkhpi.org`
- **THEN** browser code makes Supabase requests to `https://api.bloom-staging.salkhpi.org`

#### Scenario: Same image serves prod config when run with prod env

- **GIVEN** the same single image
- **WHEN** that image is run with `NEXT_PUBLIC_SUPABASE_URL=https://api.bloom.salk.edu`
- **THEN** browser code makes Supabase requests to `https://api.bloom.salk.edu`
- **AND** no rebuild occurred between staging and prod runs

#### Scenario: Dockerfile does not declare NEXT_PUBLIC ARGs

- **WHEN** `web/Dockerfile.bloom-web.prod` is read
- **THEN** the file contains no `ARG NEXT_PUBLIC_*` lines
- **AND** the file contains no `ENV NEXT_PUBLIC_*` lines that interpolate a build arg
- **AND** `docker build --build-arg NEXT_PUBLIC_SUPABASE_URL=...` has no effect on the resulting bundle

### Requirement: Typed Public Config Module

A module at `web/lib/config/public-config.ts` MUST export a typed `PublicConfig` record covering every public env var consumed in `web/`, and a `getPublicConfig()` function that reads those values from `process.env` at call time (not at module load), so server-side callers have a single source of truth that defers env reads.

#### Scenario: PublicConfig covers all eight public envs

- **WHEN** a contributor reads the exported `PublicConfig` type
- **THEN** the type has exactly these keys: `supabaseUrl`, `supabaseAnonKey`, `supabaseCookieName`, `mcpUrl`, `appUrl`, `commitSha`, `storageUrl`, `bloomUrl`

#### Scenario: getPublicConfig reads process.env at call time

- **GIVEN** the module has been imported and `getPublicConfig()` has been called once with `NEXT_PUBLIC_SUPABASE_URL=https://first`
- **WHEN** `process.env.NEXT_PUBLIC_SUPABASE_URL` is set to `https://second`
- **AND** `getPublicConfig()` is called again
- **THEN** the returned object's `supabaseUrl` is `https://second`

#### Scenario: Route handler returns the configured key set

- **WHEN** `/api/config`'s route handler is invoked with fixture env vars
- **THEN** the response body's top-level keys are exactly the keys declared by the `PublicConfig` type
- **AND** the Vitest test that asserts this fails if a new key is added to `PublicConfig` without a corresponding fixture update — making the type + handler + fixture co-edit a structural choke point

### Requirement: Public Config Endpoint

`web/app/api/config/route.ts` MUST serve the runtime-resolved `PublicConfig` as JSON to client-side code, MUST be dynamic (never statically generated), MUST run on the Node runtime (not Edge), and MUST emit `Cache-Control: no-store`, `Vary: Host`, and `Pragma: no-cache` headers.

#### Scenario: Endpoint returns the runtime config as JSON

- **GIVEN** the server is started with `NEXT_PUBLIC_SUPABASE_URL=https://example`
- **WHEN** a client issues `GET /api/config`
- **THEN** the response status is 200
- **AND** the response body is JSON matching the `PublicConfig` shape
- **AND** `response.supabaseUrl === "https://example"`

#### Scenario: Endpoint is force-dynamic

- **WHEN** `next build` runs against the project
- **THEN** the route handler at `app/api/config/route.ts` is reported as dynamic, not statically prerendered
- **AND** a Playwright e2e test asserts `/api/config` returns different bodies when the underlying container is restarted with different env vars

#### Scenario: Endpoint is pinned to Node runtime

- **WHEN** `web/app/api/config/route.ts` is read
- **THEN** the module exports `runtime = 'nodejs'`

#### Scenario: Endpoint disables caching

- **WHEN** the route handler is invoked
- **THEN** the response includes `Cache-Control: no-store`
- **AND** the response includes `Vary: Host`
- **AND** the response includes `Pragma: no-cache`

### Requirement: No Direct NEXT_PUBLIC Reads

Every `web/**/*.ts` and `web/**/*.tsx` file other than `web/lib/config/public-config.ts` MUST NOT reference `process.env.NEXT_PUBLIC_*` after the runtime-config migration, and a Vitest test wired into PR CI MUST enforce this invariant on every change.

#### Scenario: A new direct read of process.env.NEXT_PUBLIC_ fails CI

- **GIVEN** a contributor adds `const url = process.env.NEXT_PUBLIC_SUPABASE_URL` to `web/components/some-new-component.tsx`
- **WHEN** the unit test `web/lib/config/no-direct-next-public-reads.test.ts` runs in the `web-unit-tests` CI job
- **THEN** the test fails and names `web/components/some-new-component.tsx` in the failure message

#### Scenario: The public-config module itself is allowed to read process.env

- **WHEN** the same scanning test runs
- **THEN** matches inside `web/lib/config/public-config.ts` do not count as failures

#### Scenario: Commented reads do not false-positive

- **GIVEN** a file contains a commented line `// const url = process.env.NEXT_PUBLIC_FOO`
- **WHEN** the scanning test runs
- **THEN** the commented line does not count as a failure
- **AND** the scanner strips line and block comments before matching

#### Scenario: Build artifacts and infra files are excluded from the scan

- **WHEN** the scanning test walks the file tree
- **THEN** matches inside `node_modules/`, `.next/`, `tests/`, `*.env*`, `Dockerfile*`, `openspec/**`, and `next-env.d.ts` do not count as failures

#### Scenario: The lint test itself is not skip-wrapped

- **WHEN** `web/lib/config/no-skipped-tests.test.ts` runs
- **THEN** the contents of `web/lib/config/no-direct-next-public-reads.test.ts` contain zero occurrences of `describe.skip`, `test.skip`, or `it.skip`

### Requirement: Client-Side Config Hook

A React hook `usePublicConfig()` MUST be the entry point for `'use client'` components that need public configuration, MUST fetch `/api/config` exactly once per session, MUST cache the result in a React context, MUST suspend until the result is available, and MUST surface fetch failures via the Suspense fallback.

#### Scenario: usePublicConfig resolves once per session

- **GIVEN** a page that mounts three client components each calling `usePublicConfig()`
- **WHEN** the page is loaded
- **THEN** exactly one `GET /api/config` request is observed in the network log
- **AND** all three components receive the same `PublicConfig` instance

#### Scenario: Components suspend while config is loading

- **GIVEN** a client component that calls `usePublicConfig()` inside a `<Suspense fallback={<Fallback />}>`
- **WHEN** the page hydrates
- **THEN** the `<Fallback />` is rendered until the `/api/config` response resolves
- **AND** the component renders with the resolved config thereafter

#### Scenario: Fetch failure surfaces via fallback

- **GIVEN** `/api/config` returns a 500 response
- **WHEN** a client component using `usePublicConfig()` mounts
- **THEN** the Suspense boundary renders an error fallback that includes a "refresh to retry" hint
- **AND** the component does not silently render with `undefined` config values

#### Scenario: Using usePublicConfig outside the provider throws a clear error

- **GIVEN** a `'use client'` component that calls `usePublicConfig()` but is rendered outside `<PublicConfigProvider>`
- **WHEN** the component mounts
- **THEN** an error is thrown whose message names the missing provider
- **AND** the error is not the generic "undefined context" crash

### Requirement: Cross-Environment Configuration Fence — URL Hostname Mapping

`bloom-web` MUST validate at runtime that the public Supabase URL hostname is the declared public counterpart of the server-side `SUPABASE_URL` hostname, using an explicit `SUPABASE_URL_HOSTS_ALLOWED` mapping. The mapping MUST be present in production environments; absence MUST fail container boot in production. Local development MUST NOT be broken by the fence — when `NODE_ENV !== 'production'`, both the boot validator AND the `/api/config` route handler MUST return without enforcing the fence so dev setups that don't set `SUPABASE_URL` or `SUPABASE_URL_HOSTS_ALLOWED` keep working.

#### Scenario: SUPABASE_URL_HOSTS_ALLOWED parses as host-pair list

- **GIVEN** `SUPABASE_URL_HOSTS_ALLOWED="kong:8000=bloom-dev.salk.edu,kong:8000=staging-bloom-dev.salk.edu:8443"`
- **WHEN** the public-config module parses it
- **THEN** the parsed mapping has `kong:8000 → {bloom-dev.salk.edu, staging-bloom-dev.salk.edu:8443}`

#### Scenario: Missing SUPABASE_URL_HOSTS_ALLOWED fails boot in production

- **GIVEN** `bloom-web` is started with `NODE_ENV=production` and without `SUPABASE_URL_HOSTS_ALLOWED` set
- **WHEN** `web/instrumentation.ts`'s `register()` runs
- **THEN** `validateOnBoot()` throws
- **AND** the process exits non-zero before serving any request

#### Scenario: Validator skips in dev mode

- **GIVEN** `NODE_ENV !== 'production'` (typical local dev where `SUPABASE_URL` may be unset and code falls back to `NEXT_PUBLIC_SUPABASE_URL` per the existing pattern in `web/middleware.ts:9`)
- **WHEN** `validateOnBoot()` is invoked
- **THEN** the function returns without throwing, even if `SUPABASE_URL_HOSTS_ALLOWED` is absent

#### Scenario: Malformed SUPABASE_URL_HOSTS_ALLOWED throws named error

- **GIVEN** `NODE_ENV=production` and `SUPABASE_URL_HOSTS_ALLOWED` is set to a structurally malformed value (missing `=`, leading or trailing comma, empty pair between commas, empty hostname on either side)
- **WHEN** `parseHostsAllowed()` is invoked
- **THEN** it throws a `MalformedHostsAllowedError` whose message names the specific cause (e.g. `pair "kong:8000" missing '='`, `trailing comma`, `empty public host`, `empty pair between commas`)
- **AND** the error fails boot via `validateOnBoot()` before any request is served
- **AND** duplicate internal-host keys are NOT a malformed case — repeated pairs are additive and accumulate into the host's value-Set per design.md Decision 13 (multi-domain deployments)

#### Scenario: Public URL not in allow-list returns 503

- **GIVEN** `NODE_ENV=production`, `SUPABASE_URL=http://kong:8000`, `SUPABASE_URL_HOSTS_ALLOWED="kong:8000=bloom-dev.salk.edu"`, and `NEXT_PUBLIC_SUPABASE_URL=https://wrong-host.example`
- **WHEN** a client issues `GET /api/config`
- **THEN** the response status is 503
- **AND** the response body names "URL host not in SUPABASE_URL_HOSTS_ALLOWED" as the cause

#### Scenario: Public URL in allow-list returns 200

- **GIVEN** `NODE_ENV=production`, `SUPABASE_URL=http://kong:8000`, `SUPABASE_URL_HOSTS_ALLOWED="kong:8000=bloom-dev.salk.edu"`, and `NEXT_PUBLIC_SUPABASE_URL=https://bloom-dev.salk.edu/api`
- **WHEN** a client issues `GET /api/config`
- **THEN** the response status is 200

#### Scenario: Dev-mode skips the fence at /api/config

- **GIVEN** `NODE_ENV !== 'production'` (e.g. `development`, `test`, or unset) and `SUPABASE_URL_HOSTS_ALLOWED` is unset
- **WHEN** a client issues `GET /api/config`
- **THEN** the response status is 200 with the configured `PublicConfig` JSON
- **AND** the route handler does NOT enforce the URL hostname fence, mirroring `validateOnBoot()`'s dev-mode early-exit

### Requirement: Cross-Environment Configuration Fence — Anon-Key Project Match

`bloom-web` MUST validate at request time that the `NEXT_PUBLIC_SUPABASE_ANON_KEY` JWT's project identifier (`ref` claim, falling back to `iss` hostname) matches the configured `NEXT_PUBLIC_SUPABASE_URL`, so a URL-correct-but-key-swapped misconfiguration is caught. JWT decoding MUST handle base64url characters (`-` and `_`) and missing padding. The fence MUST be enforced only when `NODE_ENV === 'production'`; non-production NODE_ENV mirrors the dev-mode skip from the URL hostname fence above.

#### Scenario: Matching key and URL returns 200

- **GIVEN** `NODE_ENV=production` and the anon key JWT has `iss=https://bloom-dev.salk.edu` and `NEXT_PUBLIC_SUPABASE_URL=https://bloom-dev.salk.edu/api`
- **WHEN** a client issues `GET /api/config`
- **THEN** the response status is 200

#### Scenario: Mismatched key project returns 503

- **GIVEN** `NODE_ENV=production` and the anon key JWT has `iss=https://prod-bloom.salk.edu` and `NEXT_PUBLIC_SUPABASE_URL=https://staging-bloom-dev.salk.edu:8443/api`
- **WHEN** a client issues `GET /api/config`
- **THEN** the response status is 503
- **AND** the response body names "anon-key project does not match URL" as the cause

#### Scenario: Malformed anon-key JWT returns 503

- **GIVEN** `NODE_ENV=production` and `NEXT_PUBLIC_SUPABASE_ANON_KEY="not-a-jwt"`
- **WHEN** a client issues `GET /api/config`
- **THEN** the response status is 503
- **AND** the response body names "anon-key is not a valid JWT" as the cause

#### Scenario: base64url-encoded JWT is decoded correctly

- **GIVEN** an anon key JWT whose middle (payload) segment contains `-` and `_` characters (base64url encoding) and uses unpadded length, plus the JSON payload may contain multi-byte UTF-8 characters (CJK ideographs, emoji, combining marks)
- **WHEN** the route handler decodes the JWT
- **THEN** the `-` characters are interpreted as `+`, `_` as `/`, missing `=` padding is added, and the resulting bytes are decoded as UTF-8 (not Latin-1/binary)
- **AND** the parsed `iss`/`ref` claim matches the expected value

#### Scenario: Dev-mode skips the anon-key fence at /api/config

- **GIVEN** `NODE_ENV !== 'production'` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` is a non-JWT placeholder (e.g. a local supabase dev key)
- **WHEN** a client issues `GET /api/config`
- **THEN** the response status is 200
- **AND** the route handler does NOT enforce the anon-key project-match fence

### Requirement: Cross-Environment Configuration Fence — Cookie Name Divergence

`.env.staging.defaults` and `.env.prod.defaults` MUST declare distinct `SUPABASE_COOKIE_NAME` values (which propagate to `NEXT_PUBLIC_SUPABASE_COOKIE_NAME` via the docker-compose env block), so a researcher with both environments open in one browser does not have session cookies collide.

#### Scenario: Staging and prod declare distinct cookie names

- **WHEN** `tests/unit/test_env_cross_check.py` runs in `python-audit` CI
- **THEN** the test reads `.env.staging.defaults` and `.env.prod.defaults`
- **AND** the test fails if both files declare the same `SUPABASE_COOKIE_NAME` value

#### Scenario: Staging defaults declare staging-suffixed cookie name

- **WHEN** `.env.staging.defaults` is read
- **THEN** `SUPABASE_COOKIE_NAME` is `sb-bloom-staging-auth-token` (changed from the original `sb-bloom-auth-token`)

#### Scenario: Prod defaults retain the original cookie name

- **WHEN** `.env.prod.defaults` is read
- **THEN** `SUPABASE_COOKIE_NAME` is `sb-bloom-auth-token`

### Requirement: Cross-Environment Configuration Fence — Distinct Supabase URLs

`.env.staging.defaults` and `.env.prod.defaults` MUST declare distinct `NEXT_PUBLIC_SUPABASE_URL` values, so the URL fence has something to fence against.

#### Scenario: Staging and prod declare distinct public Supabase URLs

- **WHEN** `tests/unit/test_env_cross_check.py` runs in `python-audit` CI
- **THEN** the test fails if both files declare the same `NEXT_PUBLIC_SUPABASE_URL` value
