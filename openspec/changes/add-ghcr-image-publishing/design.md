## Context

### Where Bloom is today

- PR [#122](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/122) shipped an SSH-based deploy workflow with rollback, secret generation, and a health-gated `docker compose up -d --build`. It explicitly deferred GHCR.
- All three custom services (`bloom-web`, `langchain-agent`, `bloommcp`) are rebuilt on the Salk deploy host on every push to `staging` (see [.github/workflows/deploy.yml:225-226](.github/workflows/deploy.yml#L225-L226) and [:638-639](.github/workflows/deploy.yml#L638-L639)).
- The deploy host therefore needs the full build toolchain: Node 20, gcc, libcairo, libpango, librsvg, Python build-essential ([web/Dockerfile.bloom-web.prod:26-28](web/Dockerfile.bloom-web.prod#L26-L28)) plus uv for the Python services.
- `bloom-web` bakes four `NEXT_PUBLIC_*` values into the JS bundle at build time via Dockerfile ARGs ([web/Dockerfile.bloom-web.prod:9-17](web/Dockerfile.bloom-web.prod#L9-L17)). Four more `NEXT_PUBLIC_*` vars (`APP_URL`, `COMMIT_SHA`, `STORAGE_URL`, `BLOOM_URL`) are read at runtime today without being baked.
- `web/app/api/client-info/route.ts` already returns `{ api_url, anon_key }` from `NEXT_PUBLIC_*`. It overlaps with the proposed `/api/config` and is deleted in §5.
- The repo uses **npm with workspaces** (root `package-lock.json`; `workspaces: ["bloom-v2/*", "web", "packages/*"]` in [package.json:4-8](package.json#L4-L8)). CI invokes `npm ci` ([pr-checks.yml:33](.github/workflows/pr-checks.yml#L33)) and `cd web && npm run build` ([:59](.github/workflows/pr-checks.yml#L59)).
- `.env.prod.defaults` and `.env.staging.defaults` both declare `SUPABASE_COOKIE_NAME=sb-bloom-auth-token` ([.env.prod.defaults:76](.env.prod.defaults#L76), [.env.staging.defaults:62](.env.staging.defaults#L62)). Identical values are a pre-existing session-bleed bug surfaced by this change.
- `bloommcp/storage/schema.py` declares all manifest models with Pydantic `extra="forbid"` and `CURRENT_SCHEMA_VERSION: int = 1`. Adding new fields to manifests requires a coordinated schema-version bump and a v1→v2 migration. Per-result image-SHA stamping therefore needs its own design pass; this change ships only the env-plumbing prerequisite.

### Stakeholders

- **Operators** (elizabeth@talmolab.org and the Salk infra team) — get a deploy host that pulls instead of builds, plus reproducible rollback targets. One-time staging session invalidation on PR-3 deploy.
- **Reviewers** — get a smaller surface to audit per deploy.
- **Researchers (data integrity)** — get a cross-environment fence that prevents staging browsers from writing to prod Supabase, plus the env plumbing that the future per-result traceability work needs.
- **Future contributors** — must understand the runtime-config pattern so they don't reintroduce `process.env.NEXT_PUBLIC_*` reads at module load.

## Goals / Non-Goals

### Goals

- One `bloom-web` image runs in any environment without rebuild.
- Every custom-service image referenced in `docker-compose.prod.yml` resolves to a content-addressable artifact (either GHCR tag we control or `@sha256:` digest).
- Staging deploys pull from GHCR; the deploy host no longer needs the Node/Python build toolchains for the custom services.
- Rollback continues to work end-to-end after the GHCR migration (pulls the prior `sha-<short>` instead of rebuilding a `build:` block that no longer exists; aborts with a clear error if the previous handle is missing rather than re-pulling a known-bad image).
- PR CI (`pr-checks.yml`) keeps passing on every PR — including PRs against `staging` that land mid-migration.
- Tests fence (a) the runtime-config contract so re-introducing `process.env.NEXT_PUBLIC_*` at module load fails CI, (b) per-environment Supabase URL and cookie-name divergence, (c) anon-key project-ref consistency, so a misconfigured environment can't make staging browsers write to prod Supabase or vice-versa.
- `BLOOM_IMAGE_SHA` reaches every custom-service container as forward-prep so the deferred per-result stamping work is a pure consumer-side change when it lands.

### Non-Goals

- Promoting the exact staging SHA to production (Week 3 of #107).
- Image GC, retention, storage-cost policy (Week 4 of #107).
- The mutable `latest` tag — belongs with prod promotion.
- Replacing `bloom-web`'s server-side `process.env.SUPABASE_URL` / `JWT_SECRET` / `SERVICE_ROLE_KEY` reads.
- Sticky rollback (issue #140).
- Broad unit / component / integration test coverage for `bloom-web`.
- Atomic temp-file writes / `stop_grace_period` on bloommcp and langchain-agent — deferred to a new issue filed in §12.4.
- **Per-result image-SHA stamping in scientific outputs** — deferred to a new issue filed in §12.6 because it requires (a) bloommcp `Manifest` schema v2 migration, (b) a langchain plot-descriptor format decision, and (c) a 9th `imageSha` key in `PublicConfig`. The env plumbing (`BLOOM_IMAGE_SHA` injection) ships in this change so the follow-up is pure consumer-side code.

## Decisions

### Decision 1: Two new capabilities, not one

`image-publishing` and `frontend-runtime-config` are introduced as separate capabilities. They share a motivating goal ("one image runs anywhere") but their requirements evolve independently — future work on prod promotion, retention, and signed images will all live under `image-publishing`, while runtime-config concerns (feature flags, per-tenant config, etc.) will live under `frontend-runtime-config`.

### Decision 2: GHCR namespace = `ghcr.io/salk-harnessing-plants-initiative/<service>`

The git remote URL is `https://github.com/Salk-harnessing-plants-initiative/bloom`. GHCR uses the lowercased GitHub org slug. **Mitigation:** store the namespace in `.env.prod.defaults` as `IMAGE_NAMESPACE=salk-harnessing-plants-initiative` and interpolate as `image: ghcr.io/${IMAGE_NAMESPACE}/<service>:${IMAGE_TAG:-staging}`.

### Decision 3: Image tag scheme — `sha-<short>` (immutable) + `staging` (mutable)

- Every CI build pushes **both** tags.
- No `latest` tag in this change.
- `<short>` is 7 characters (`git rev-parse --short HEAD`).

### Decision 4: Runtime-config delivery — `/api/config` route + React Suspense, full 8-var `PublicConfig`

The eight public vars actually consumed in `web/` today:

| Key | Used at | Today's source |
| --- | --- | --- |
| `supabaseUrl` (`NEXT_PUBLIC_SUPABASE_URL`) | client.ts, server.ts, middleware.ts, storage-url.ts, route handlers | baked at build |
| `supabaseAnonKey` (`NEXT_PUBLIC_SUPABASE_ANON_KEY`) | client.ts, server.ts, middleware.ts, route handlers | baked at build |
| `supabaseCookieName` (`NEXT_PUBLIC_SUPABASE_COOKIE_NAME`) | client.ts | baked at build |
| `mcpUrl` (`NEXT_PUBLIC_MCP_URL`) | mcp-chat-client.tsx | baked at build |
| `appUrl` (`NEXT_PUBLIC_APP_URL`) | oauth/gitlab/{initiate,store}/route.ts | runtime today |
| `commitSha` (`NEXT_PUBLIC_COMMIT_SHA`) | **app/api/health/route.ts:19 — liveness probe** | runtime today |
| `storageUrl` (`NEXT_PUBLIC_STORAGE_URL`) | components/expression-lib/scrna-client.ts | runtime today |
| `bloomUrl` (`NEXT_PUBLIC_BLOOM_URL`) | app/test/page.tsx | runtime today (test-only) |

The pattern:

- **Server-side reads:** call `getPublicConfig()`, reads `process.env` at request time.
- **Client-side reads:** call `usePublicConfig()`, fetches `/api/config` once at hydration, caches in React context, suspends until resolved. React 19's `use(promise)` API is the suspending primitive.
- **`/api/config`** has `export const dynamic = 'force-dynamic'`, `export const revalidate = 0`, `export const runtime = 'nodejs'`, and returns headers `Cache-Control: no-store`, `Vary: Host`, `Pragma: no-cache`.
- **`PublicConfig` is a typed record.** A future 9th key for `imageSha` is anticipated by the deferred-traceability follow-up but not added here.
- **`web/app/api/client-info/route.ts` is deleted.**

### Decision 5: One image build per commit

Because `bloom-web` no longer bakes public envs, a single image build per commit serves every environment.

### Decision 6: Update rollback to pull prior `sha-<short>`; abort on missing handle

Three concrete changes to rollback:

1. **Capture the previous `IMAGE_TAG`** alongside the previous SHA in the deploy state directory: `${{ secrets.PROD_DEPLOY_PATH }}.state/previous_image_tag` (matching the existing `previous_sha` path pattern at [.github/workflows/deploy.yml:157,449,580,838](.github/workflows/deploy.yml#L157)).
2. **In the rollback branch**, export `IMAGE_TAG=$(cat ${PROD_DEPLOY_PATH}.state/previous_image_tag)` before the compose command. **If the file is missing**, the rollback exits non-zero with `::error::No previous_image_tag captured — manual recovery required (do NOT auto-fallback to staging; that would re-pull the bad image that just failed forward-deploy).` Operators then manually choose a SHA (e.g. from `git log` or GHCR's package UI) and re-run with `IMAGE_TAG=sha-<chosen>` via `workflow_dispatch`.
3. **Replace `up -d --build`** with `docker compose pull && docker compose up -d --remove-orphans --wait --wait-timeout 300`.

**Rollback across the cutover boundary.** If the broken deploy IS the one that introduced GHCR (the PR-3 merge commit), then `git reset --hard $PREV` restores a `docker-compose.prod.yml` that still has `build:` blocks and no `image:` line — so the `IMAGE_TAG` capture is irrelevant for this specific rollback. The forward `up -d --build` against the pre-GHCR compose file works (it rebuilds locally). Documented as a manual runbook step in `PROD_SETUP.md` rather than scenario-fenced, because it's a one-time concern at PR-3 merge time.

The staging forward-deploy step gets the same treatment minus the IMAGE_TAG capture. The prod forward deploy is **out of scope** — production tag selection is the prod-promotion change's responsibility.

**Alternative considered:** fall back to a git-SHA-derived image tag (e.g. `sha-<first-7-of-previous_sha>`). Rejected because if the previous commit's image was garbage-collected from GHCR (future #107 Week 4 GC policy), the fallback fails the same way as the `staging` fallback would. Failing fast with a clear operator message is more honest than masking the missing handle.

### Decision 7: Pin third-party images by digest at their current resolved tag

The 10 unpinned images get `@sha256:<digest>` appended to their existing tag. §9.2 cross-checks against #56's tracking before pinning so we don't enshrine known-vulnerable digests.

### Decision 8: Extend existing Vitest setup (not Jest, not from scratch)

**As of staging rebase, Vitest is already installed in `web/`** (`vitest@^4.1.8` at [web/package.json:65](web/package.json#L65), plus `vite-tsconfig-paths`). `web/vitest.config.ts` exists with `environment: 'node'` and a colocated-test discovery pattern (`lib/**/*.test.ts`, `components/**/*.test.{ts,tsx}`, `app/**/*.test.{ts,tsx}`). The original proposal predated this and assumed a bootstrap; the current scope is to extend, not bootstrap.

**This change adds:**
- `setupFiles: ['./vitest.setup.ts']` for `beforeEach`/`afterEach` `process.env` snapshot+restore (vitest doesn't isolate `process.env` automatically).
- `pool: 'forks'` to enforce per-file process isolation. Existing tests don't mutate `process.env`, so the switch is safe.
- `exclude: ['lib/**/__fixtures__/**']` so the JWT fixture helper isn't auto-discovered as a test.
- `jsdom` devDep. The workspace default stays `environment: 'node'` (preserves the existing intent); React/Response tests use per-file `// @vitest-environment jsdom` directives.
- New colocated tests matching the existing convention seen at [web/lib/queries/recent-phenotypes-by-cyl-scanner.test.ts](web/lib/queries/recent-phenotypes-by-cyl-scanner.test.ts).

**Jest was rejected** because it would require migrating off Vitest (now in use), plus adding `jest-environment-jsdom` + Babel/SWC integration — substantial rework for no benefit.

**Coverage thresholds and `@vitest/coverage-v8`** are out of scope for this change; deferred to the §12.3 follow-up.

### Decision 9: A `build-images` job in `deploy.yml`, not a separate workflow

Same workflow, two jobs (`build-images` → `deploy-staging`). Build outputs `IMAGE_TAG` (`sha-<first-7-of-github.sha>`) which `deploy-staging` consumes via `needs:` + job outputs.

### Decision 10: Build job runs on `ubuntu-latest`; deploy job stays self-hosted

The new `build-images` job runs on **`ubuntu-latest`** to avoid saturating the Salk deploy host with three custom builds per push. Declares `concurrency: build-images-${{ github.ref }}` so concurrent staging pushes serialize their `staging`-tag overwrites.

### Decision 11: `docker-compose.ci.yml` overlay restores `build:` for PR CI

`pr-checks.yml`'s `compose-health-check` job uses `-f docker-compose.prod.yml -f docker-compose.ci.yml` so PR CI builds locally (no GHCR pull). Production stays `image:`-only. The flag pair is hoisted into a job-level `COMPOSE_FILES` env var.

**Compose merge semantics:** when an overlay declares `build:` on a service whose base declares `image:`, both keys persist in the merged config. `docker compose up --build` builds from `build:` and tags the result with `image:`. That's harmless for CI (the tag isn't pushed anywhere) but means `test_compose_ci_overlay_parity.py` asserts only that the overlay *adds* `build:` to each GHCR-ref'd custom service, never that it removes `image:`.

### Decision 12: Archive `implement-cicd-pipeline` before this change's PR-3 lands

`openspec/changes/implement-cicd-pipeline/` proposes 12 ADDED requirements contradicting this change (Jest vs Vitest, `latest` tag, CD trigger on `main` vs `staging`, 70% coverage threshold). The older change is at 0/121 tasks and predates the merged deploy work in PR #122.

**Decision:** PR-3 §12.1 confirms with the user; §12.2 runs `openspec archive implement-cicd-pipeline --skip-specs --yes`. **`--skip-specs` is justified because** the 12 ADDED requirements contradict this change's specs and cannot coexist; PR-3 §12.3 files a successor issue that audits each of the 12 by name and records their status:

| `implement-cicd-pipeline` requirement | Status |
| --- | --- |
| Python Package Management with uv | LANDED via PR #126 (pin-python-deps) |
| Automated Code Formatting | OPEN — re-file in successor issue |
| Code Linting and Type Checking | OPEN — re-file |
| Automated Testing with Coverage | OPEN — this change adds Vitest with 0% threshold; broader coverage re-filed |
| Pre-commit Quality Gates | OPEN — re-file |
| Continuous Integration Pipeline | PARTIAL — pr-checks.yml exists; complete coverage re-filed |
| Continuous Deployment Pipeline | LANDED via PR #122 + this change |
| Coverage Tracking and Reporting | OPEN — re-file |
| Type Annotation Enforcement | OPEN — re-file |
| Monorepo Task Orchestration | PARTIAL — turbo.json exists; re-file gaps |
| Developer Documentation | OPEN — re-file |
| Dependency Security Scanning | LANDED via Trivy in pr-checks.yml |

The deliberate discard is therefore documented at archive time; nothing is silently lost.

### Decision 13: `SUPABASE_URL_HOSTS_ALLOWED` format — comma-separated `internal=public` pairs

The cross-environment fence needs a declared mapping between server-internal URLs (e.g. `http://kong:8000`) and their public face (e.g. `https://bloom-dev.salk.edu/api`). The mapping must be explicit so the `/api/config` route can validate that `NEXT_PUBLIC_SUPABASE_URL`'s host is the declared public counterpart of `process.env.SUPABASE_URL`'s host.

**Format:** `SUPABASE_URL_HOSTS_ALLOWED="<internal_host>=<public_host>[,<internal_host>=<public_host>]*"`

Hosts include port. Example values:

- `.env.staging.defaults`:
  `SUPABASE_URL_HOSTS_ALLOWED="kong:8000=staging-bloom-dev.salk.edu:8443"`
- `.env.prod.defaults`:
  `SUPABASE_URL_HOSTS_ALLOWED="kong:8000=bloom-dev.salk.edu"`

**Validation algorithm** (implemented in `web/lib/config/validate-on-boot.ts`):
1. **Dev-mode early-exit:** if `process.env.NODE_ENV !== 'production'`, return without throwing — local dev historically falls back to `NEXT_PUBLIC_SUPABASE_URL` when `SUPABASE_URL` is unset (see [web/middleware.ts:9](web/middleware.ts#L9), [web/lib/supabase/server.ts:16](web/lib/supabase/server.ts#L16)), and we don't want to break that.
2. Parse `SUPABASE_URL_HOSTS_ALLOWED` into a `Map<string, Set<string>>` (one internal host can map to multiple public hosts for multi-domain deployments).
3. Extract `internalHost` from `URL(process.env.SUPABASE_URL).host`.
4. Extract `publicHost` from `URL(getPublicConfig().supabaseUrl).host`.
5. Look up `allowedPublicHosts = map.get(internalHost)`.
6. If `allowedPublicHosts` is undefined → boot fails (operator forgot to declare the mapping).
7. If `publicHost ∉ allowedPublicHosts` → `/api/config` returns 503.

**Malformed input handling** (`parseHostsAllowed()`): MUST throw a named `MalformedHostsAllowedError` with a specific cause when the value:
- Contains a pair missing `=` (e.g. `kong:8000`): error message names the offending pair.
- Contains a pair with empty internal or public hostname (e.g. `=bloom-dev.salk.edu` or `kong:8000=`).
- Contains trailing or leading commas (e.g. `,kong:8000=bloom-dev.salk.edu` or `kong:8000=bloom-dev.salk.edu,`).
- Contains a duplicate internal-host key with conflicting (rather than additive) values.
A blank value is permitted ONLY when `NODE_ENV !== 'production'` (per the dev-mode early-exit); production boot fails on blank.

**Tests:** `tests/unit/test_supabase_url_hosts_allowed_format.py` asserts the format is parseable and the staging/prod defaults are well-formed. Vitest tests in `web/lib/config/validate-on-boot.test.ts` exercise the algorithm including the dev-mode early-exit AND the malformed-input cases above.

**Alternative considered:** JSON encoding. Rejected because JSON inside a `.env` file requires careful quoting.

**Dev-mode security note (worth a JSDoc warning):** the early-exit means a dev process pointed at a real prod/staging Supabase URL (e.g. operator-set `NEXT_PUBLIC_SUPABASE_URL=https://bloom-dev.salk.edu/api` for "quick local test") will silently bypass the fence. Pre-existing risk in today's `NEXT_PUBLIC_SUPABASE_URL` fallback pattern — not a regression introduced by this change — but worth surfacing in `validateOnBoot()`'s JSDoc so future contributors understand the contract.

### Decision 14: Anon-key project-ref check

The URL-hostname fence (Decision 13) catches misconfigured URLs but not misconfigured keys. If `.env.staging` has the right URL but the prod `ANON_KEY` swapped in, writes still go to the wrong project. The `NEXT_PUBLIC_SUPABASE_ANON_KEY` is a JWT whose `ref` claim names the Supabase project (e.g. `{"ref":"bloomdev"}`).

**Decision:** the `/api/config` handler decodes the JWT and asserts the `ref` claim (or `iss` hostname for self-hosted JWTs lacking `ref`) matches the configured `NEXT_PUBLIC_SUPABASE_URL`.

**Base64url handling (required, not optional):** JWTs use base64url, not base64. The decoder MUST:
1. Replace `-` with `+` and `_` with `/`.
2. Pad with `=` to multiple of 4.
3. Then `atob` and JSON-parse.

**Test fixture helper** (`web/lib/config/__fixtures__/jwt.ts`) exports
`makeAnonKey({iss?, ref?, sub?}): string` so tests don't roll their own
base64 encoding. The helper produces JWTs with `-`/`_` characters in the
encoded segments to exercise the base64url substitution.

**Note: this is a sanity-check, not authentication.** The decoder does not verify the JWT signature (we don't have the signing key at the bloom-web layer). The check exists to catch operator misconfiguration, not to defend against forged keys; that's RLS's job.

**Implementation:** small helper `decodeAnonKeyProject(anonKey: string): { ref?: string; iss?: string }` in `web/lib/config/public-config.ts`.

**Self-hosted JWT shape verification — must happen during PR-3 implementation, not at proposal time.** Bloom uses self-hosted Supabase (per [openspec/project.md:21-27](openspec/project.md#L21-L27)), where the JWT claim shape may differ from Supabase Cloud. Specifically: the `ref` claim may be absent (it's a Supabase Cloud convention), and `iss` may not be a URL with a recognizable hostname. **PR-3 §11 implementation MUST decode a real `.env.prod.defaults` `ANON_KEY` and confirm at least one of `ref` or `iss` is reliably populated and matches the deployed URL.** If neither claim works, this requirement degrades to "JWT is parseable" — still useful (catches `not-a-jwt` strings) but weaker than intended. The spec scenario "Mismatched key project returns 503" must then be re-graded to acknowledge the gap; this is tracked as Open Question 3 below.

### Decision 15: `BLOOM_IMAGE_SHA` plumbing only — no consumer stamping in this change

This change creates the SHA-tagged image scheme that makes per-result traceability mechanically possible, **but consumer-side stamping is deferred to a follow-up issue (§12.6).** The reason is that the consumer side has its own design surface:

- **bloommcp:** `bloommcp/storage/schema.py` declares all manifest models with Pydantic `extra="forbid"` and `CURRENT_SCHEMA_VERSION: int = 1`. Adding `image_sha` to a manifest model requires bumping `CURRENT_SCHEMA_VERSION` to 2, writing a v1→v2 migration in `validate_schema()` ([bloommcp/storage/manifest.py:17-33](bloommcp/storage/manifest.py#L17)), choosing whether the field lives on `Manifest` (per-experiment) or `VersionEntry` (per-run), and updating every workflow writer (`bloommcp/storage/writer.py::AnalysisWriter.commit()` plus the workflows under `bloommcp/tools/workflows/`).
- **langchain-agent:** `langchain/helpers/plot_renderer.py` writes raw PNGs to `BLOOM_PLOTS_DIR`. There is no JSON manifest or sidecar today. The implementation must choose between (a) `<plot>.png.json` sidecar files (extensible, easy to inspect, breaks on rename) or (b) PNG metadata via `matplotlib.Figure.savefig(metadata={"image_sha": ...})` (survives rename, single file, harder to inspect). This is a real design decision that belongs in its own change.
- **bloom-web `/api/health`:** the deferred work also adds a 9th key (`imageSha`) to `PublicConfig` so `/api/health` can report it without violating the "No Direct NEXT_PUBLIC Reads" invariant.

**What this change ships:** `BLOOM_IMAGE_SHA: ${IMAGE_TAG:-staging}` injected into the `environment:` block of `bloom-web`, `langchain-agent`, and `bloommcp` in `docker-compose.prod.yml`. The env var is reachable by service code. Spec scenario "BLOOM_IMAGE_SHA Available to Custom Services" fences the injection.

**Why ship the plumbing now:** the env injection is mechanically trivial (3 lines of YAML) and there is no good reason to defer it — when the follow-up lands, no compose changes will be needed. Shipping the consumer wiring later means the follow-up is a contained change touching only `bloommcp/storage/*`, `langchain/helpers/plot_renderer.py`, and `web/lib/config/public-config.ts` + the route handler.

**Tracking:** §12.6 files a new GitHub issue titled "Stamp BLOOM_IMAGE_SHA into bloommcp/langchain analysis outputs" with the design pieces enumerated above. Labels: `data-integrity`, `traceability`.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| GHCR push fails mid-deploy → deploy job hangs | Build job runs before deploy job with its own timeout; push failure fails the build job. |
| Staging host can't pull from GHCR | `docker login ghcr.io` fails fast before `docker compose pull`. `GHCR_READ_TOKEN` PAT; env-scoped via #159 follow-up. |
| Runtime-config fetch fails on first paint | Vitest tests exercise 500 → fallback path; spec scenario fences. |
| Existing call sites that read `process.env.NEXT_PUBLIC_*` at module load silently break if missed | Vitest test asserts no `web/**/*.ts`/`tsx` file matches `/process\.env\.NEXT_PUBLIC_/` (excluding `public-config.ts`, `tests/**`, `Dockerfile*`, `*.env*`, `openspec/**`, `next-env.d.ts`; comment-stripping pass first). |
| Misconfigured `.env.staging` points at prod Supabase → researcher browsers write to wrong DB (data corruption) | Three layers: (1) `validateOnBoot()` throws if any required key unset/malformed (production mode only); (2) `/api/config` returns 503 if URL hostname mismatch per `SUPABASE_URL_HOSTS_ALLOWED`; (3) `/api/config` returns 503 if anon-key JWT `ref`/`iss` doesn't match URL; (4) Python `test_env_cross_check.py` asserts defaults declare distinct URLs and cookie names. |
| Local dev breaks when `SUPABASE_URL` is unset (existing fallback pattern) | Decision 13's `NODE_ENV !== 'production'` early-exit preserves the existing dev fallback. |
| Two researchers logged into staging and prod in same browser → session bleed | This change changes staging's `SUPABASE_COOKIE_NAME` to `sb-bloom-staging-auth-token`. **Operator impact: one-time staging session invalidation on PR-3 deploy.** Rollback of PR-3 re-invalidates a second time. Documented in `PROD_SETUP.md` and PR-3 description. |
| Digest pins go stale (new digest published for same tag upstream) | Acceptable. The whole point of pinning is to make this break visible. Fix is a deliberate bump-and-re-pin PR. |
| Two-job ordering (build → deploy) lengthens staging deploy wall-clock | ~3-5 min for the longest custom build. Builds run in parallel. |
| Rollback fails because previous `sha-<short>` was garbage-collected from GHCR | Image GC policy (Week 4 of #107) MUST keep last N images; until that lands, GHCR default retention is unlimited. |
| Rollback when `previous_image_tag` is missing (first-time after migration, or state-dir wipe) | Per Decision 6, the rollback aborts with a clear error rather than re-pulling `staging` (which would re-deploy the broken image). Operator manually picks a SHA via `workflow_dispatch`. |
| `docker-compose.ci.yml` overlay drifts from prod compose | `tests/unit/test_compose_ci_overlay_parity.py` asserts every GHCR-ref'd service in prod has a `build:` block in the overlay. |
| `implement-cicd-pipeline` archive deletes 12 ADDED requirements | Decision 12's per-requirement audit table documents the discard; §12.3 files a successor issue. |
| Rolling back across the GHCR cutover boundary (PR-3 merge commit) → `IMAGE_TAG` capture irrelevant because compose file itself reverts | One-time concern at PR-3 merge. Documented as manual runbook in `PROD_SETUP.md`. |
| In-flight bind-mount writes during compose recreate → torn files (bloommcp `to_csv`, langchain `PLOTS_DIR`) | Deferred to new issue in §12.4. This change introduces `--remove-orphans --wait --wait-timeout 300` which is *some* mitigation but not a full fence. |
| Issue #185 (CI compose stack diverges from deploy code path) widens because of the `docker-compose.ci.yml` overlay | Acknowledged. Cross-referenced in proposal.md Deferred table. |
| `validateOnBoot()` wired via `web/instrumentation.ts` is bypassed by `next start` outside the container | Mitigation: instrumentation hook is the standard Next 16+ pattern; non-container `next start` paths are dev-only and the dev-mode early-exit in Decision 13 makes the validator a no-op there anyway. The pure validator is also Vitest-tested so reusing it from another entry point is trivial. |
| **CI does NOT exercise `validateOnBoot`'s boot-fail branch.** PR CI uses `docker-compose.ci.yml` overlay which builds locally and runs containers with the same `NODE_ENV=production` as prod, but PR-time CI env defaults are wired so the validator passes. The boot-fail path (missing `SUPABASE_URL_HOSTS_ALLOWED`, missing `NEXT_PUBLIC_SUPABASE_URL`, etc.) is covered ONLY by Vitest unit tests at §2.3 and the Playwright e2e in §11.6 (which deliberately starts a stack with bad env to assert non-zero exit). | Acceptable: §2.3's pure-function tests exhaustively cover the validator's logic; the §11.6 e2e proves the wiring works end-to-end. The gap is that arbitrary new validator rules can't be CI-tested against a real `bloom-web` boot in the `compose-health-check` job without explicit test-only env scaffolding. |
| **Caddy may become a 4th custom-built service** if branch [`chore/caddy-acme-dns-cloudflare`](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/tree/chore/caddy-acme-dns-cloudflare) lands before PR-3. That branch adds `caddy/Dockerfile` (custom build with the Cloudflare DNS plugin for ACME DNS-01) and modifies `docker-compose.prod.yml`, `pr-checks.yml`, `deploy.yml` — substantial overlap with this proposal's surface. | §9.5 ships an inline forward-compat checklist (4th `docker/build-push-action` step, compose `image:` flip for caddy, ci-overlay `build:` block, test updates). §12.10 files a tracking issue. If the caddy branch lands first: PR-3 implementer follows §9.5 inline; §12.10 issue closes. If it lands after PR-3: §12.10 issue tracks the standalone migration as a small follow-up PR. Caddy doesn't write analyses, so `BLOOM_IMAGE_SHA` plumbing stays scoped to the 3 services that do (`bloom-web`, `langchain-agent`, `bloommcp`). |

## Migration Plan (PR-by-PR)

### PR-1 — Foundation (`staging` ← feature branch)

Tasks §0–§3. CI-compat first (§0), then test infra and the new module/endpoint (§1–§3).

| Step | Rollback |
| --- | --- |
| §0 pr-checks.yml CI-compat (drop `--build-arg NEXT_PUBLIC_*`, prep `docker-compose.ci.yml` shape, add `web-unit-tests` job, hoist `COMPOSE_FILES`) | Revert the workflow change. |
| §1 Vitest setup | Delete vitest deps and config. |
| §2 public-config module + validateOnBoot | Delete the files. |
| §3 /api/config endpoint + instrumentation wiring | Delete the route handler and instrumentation.ts. |

### PR-2 — Migration (rebase onto `staging` post-PR-1)

Tasks §4 and §5. The 15-file mechanical refactor.

| Step | Rollback |
| --- | --- |
| §4 migration-lint test (committed `describe.skip`) | Revert the test file. |
| §5 migrate each caller; delete `app/api/client-info/route.ts`; strip commented `process.env.NEXT_PUBLIC_*` reads in `gitlab/user/route.ts` and `oauth/gitlab/exchange/route.ts` | Per-file revert. |
| §5.6 unskip §4 test, §5.8 fence no-skip | Re-add `.skip` (or §5.8 grep test catches the regression). |

### PR-3 — Cutover (rebase onto `staging` post-PR-2)

Tasks §6–§13. The infrastructure change that actually delivers GHCR pull.

| Step | Rollback |
| --- | --- |
| §6 Drop Dockerfile ARGs + compose build-args | Re-add ARGs + args block. |
| §7 GHCR build/push job | Delete the new job. |
| §8 Compose `image:` flip + `docker-compose.ci.yml` overlay + `BLOOM_IMAGE_SHA` env injection | Revert compose diffs. |
| §9 Digest pins | Drop `@sha256:` suffixes. |
| §10 Deploy `pull` + rollback `sha-<short>` capture + abort-on-missing-handle | Revert deploy.yml diffs. |
| §11 SUPABASE_URL_HOSTS_ALLOWED + cookie-name divergence + PROD_SETUP.md update | Per-piece revert. **Note: cookie-name change invalidates active staging sessions; planned for next-deploy window. Rollback re-invalidates a second time.** |
| §12 Archive `implement-cicd-pipeline` + file successor issues (§12.3 cicd, §12.4 in-flight writes, §12.6 image-SHA stamping, §12.7 cross-env umbrella) | `git revert`; reopen the archive. |

## Open Questions

- **GHCR auth on the self-hosted staging runner.** Cleanest path is an org-level PAT with `read:packages`, stored as `GHCR_READ_TOKEN`. Confirm PAT vs GitHub App; tracked under #159.
- **Should `IMAGE_TAG` default to `staging` in `docker-compose.prod.yml`, or be required explicitly?** Recommendation: default `staging`; revisit when prod-promotion lands.
- **Self-hosted JWT `ref` claim shape.** Bloom's self-hosted Supabase JWTs may not carry a `ref` claim. Decision 14 falls back to `iss` hostname check; verify this fallback works against a real anon key during PR-3 implementation. If neither claim is reliable, Decision 14's fence reduces to "JWT is parseable" — still useful but weaker than intended.
- **PR-1 ships `/api/config` reachable but unused.** A curious operator hitting `/api/config` on staging after PR-1 lands will see a real JSON response (because env vars are set). Worth a one-line note in PR-1's description; not a security concern (the anon key is already in `client-info`, which PR-1 hasn't deleted yet).
- **Multi-PR rebase style.** This change uses `staging`-as-base with rebase-on-merge to avoid `cleanup-merged` deleting bases. The user previously preferred "merge each branch to its parent branch." Worth confirming during PR-1 review whether to disable `cleanup-merged` for the GHCR branches and use the parent-base style instead.
