# Add GHCR Image Publishing

## Why

Today the deploy workflow rebuilds container images on the Salk server every time
(`docker compose -f docker-compose.prod.yml ... up -d --build`), so:

- Staging and production are never guaranteed to run identical artifacts —
  every deploy is a fresh build that may pick up moved upstream package tags,
  base-image drift, or transient registry state.
- There is no immutable, content-addressable handle to roll back to; the
  rollback path in [.github/workflows/deploy.yml:464-466](.github/workflows/deploy.yml#L464-L466)
  and [:851-853](.github/workflows/deploy.yml#L851-L853) `git reset --hard $PREV`
  then `up -d --build`, which can re-pull the same bad upstream layer.
- 10 of the 13 third-party images in [docker-compose.prod.yml](docker-compose.prod.yml)
  still resolve via floating tags (caddy, kong, supabase/realtime, minio/minio,
  supabase/storage-api, supabase/postgres, supabase/supavisor, supabase/studio,
  darthsim/imgproxy, supabase/postgres-meta) — only `gotrue`, `postgrest`, and
  `minio/mc` carry an `@sha256:` digest today.
- The deploy server must host the full build toolchain (Node 20, gcc, libcairo,
  libpango, librsvg, Python build-essential — see
  [web/Dockerfile.bloom-web.prod:26-28](web/Dockerfile.bloom-web.prod#L26-L28)),
  doubling its attack surface vs. a pull-only host.

These gaps are exactly what GitHub issue
[#107 — Set up GHCR image publishing + deployment pipeline](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/107)
calls out. PR [#122](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/122)
intentionally deferred GHCR ("step 1 of #107, GHCR is a follow-up") and shipped
only the SSH/env/rollback plumbing; this change picks up the deferred work.

The GHCR-pull model only works end-to-end if **one image works in any
environment**. For Python services (`langchain-agent`, `bloommcp`) that is
already true — config flows in at container start time. For `bloom-web` it is
not: [web/Dockerfile.bloom-web.prod:9-17](web/Dockerfile.bloom-web.prod#L9-L17)
declares four `NEXT_PUBLIC_*` build ARGs that Next.js inlines into the JS
bundle shipped to browsers. An image built with staging values will always
serve staging values, even if run in production. This change therefore also
introduces a runtime-config mechanism so `bloom-web` reads public configuration
at request time, removing the build-time bake.

A pre-existing data-integrity gap surfaces alongside this work:
`.env.staging.defaults` and `.env.prod.defaults` both declare
`SUPABASE_COOKIE_NAME=sb-bloom-auth-token` ([.env.prod.defaults:76](.env.prod.defaults#L76),
[.env.staging.defaults:62](.env.staging.defaults#L62)). A researcher with both
environments open in one browser would have sessions collide on the same cookie
name. This change fixes that as part of the cross-environment fence work.

## What Changes

### Infrastructure — image publishing pipeline

- **CI builds and pushes three custom images** (`bloom-web`,
  `langchain-agent`, `bloommcp`) to
  `ghcr.io/salk-harnessing-plants-initiative/<service>` on push to `staging`,
  tagged with both `sha-<short-git-sha>` (immutable) and `staging` (mutable).
  The build job runs on `ubuntu-latest` (not the self-hosted Salk runner) to
  avoid saturating the deploy host. Build job has `concurrency:
  build-images-${{ github.ref }}` so concurrent staging pushes can't race
  the mutable `staging` tag.
- **Per-image build context is explicit:**
  - `bloom-web` → `context: .` (repo root) + `file: web/Dockerfile.bloom-web.prod`
    so the Dockerfile can `COPY packages/*` from the workspace.
  - `langchain-agent` → `context: ./langchain` + `file: ./langchain/Dockerfile`.
  - `bloommcp` → `context: ./bloommcp` + `file: ./bloommcp/Dockerfile`.
  (Issue #107's body explicitly flagged this monorepo build-context concern;
  this change resolves it.)
- **`docker-compose.prod.yml` switches the three custom services from
  `build:` to `image: ghcr.io/${IMAGE_NAMESPACE}/<service>:${IMAGE_TAG:-staging}`.**
  A single `IMAGE_TAG` env var selects which tag is pulled; default `staging`
  preserves the staging-deploy contract. **BREAKING** for any operator who ran
  `docker compose -f docker-compose.prod.yml up` locally — they now need GHCR
  pull access (or use the new `docker-compose.ci.yml` override described
  below). Production tag selection (sha promotion, approval gate) is out of
  scope for this change — see "Deferred" below.
- **`BLOOM_IMAGE_SHA: ${IMAGE_TAG:-staging}` is injected into all three
  custom service env blocks** as forward-prep for the deferred per-result
  stamping work (see Deferred). The env var is present and reachable by
  service code; consumer-side stamping (manifests, plot descriptors) is
  tracked separately so the GHCR cutover can ship independently.
- **The 10 unpinned third-party images in `docker-compose.prod.yml` gain
  `@sha256:` digests** matching their current resolved tag, so every layer
  in the prod stack is content-addressable. Per issue
  [#56](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/56)
  most CVE-vulnerable images were already upgraded (PRs #91, #94); this
  change pins what's already there. Filing a per-image upgrade-tracking
  comment on #56 is part of §9.
- **Staging deploy in `deploy.yml` runs `docker compose pull` before
  `docker compose up -d ... --wait`.** The staging-side `--build` flag
  goes away.
- **Rollback paths in `deploy.yml` are updated** so they pull the previous
  `sha-<short>` from GHCR instead of attempting to rebuild a `build:` block
  that no longer exists. The previous `IMAGE_TAG` is captured into
  `${PROD_DEPLOY_PATH}.state/previous_image_tag` (matching the existing
  `previous_sha` path pattern). **If `previous_image_tag` is missing,
  rollback aborts with a clear "manual recovery required" error rather than
  falling back to `staging`** — a `staging`-fallback would re-pull the
  exact image that just failed forward-deploy. Broader sticky-rollback
  work remains issue [#140](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/140);
  rolling back *across* the GHCR-cutover commit boundary (where compose
  itself changes shape) is documented as a manual runbook step in
  `PROD_SETUP.md`.
- **A `GHCR_READ_TOKEN`** (or use of `${{ secrets.GITHUB_TOKEN }}` with
  `packages: write` permission) is added to the workflow so CI can push,
  and the staging host gains read access via a `docker login ghcr.io` step
  in `deploy.yml`. GitHub-Environment-scoped secrets per issue
  [#159](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/159)
  are a follow-up.
- **New `docker-compose.ci.yml` override file** is added so
  `.github/workflows/pr-checks.yml`'s `compose-health-check` job can still
  exercise the full stack on PRs (where no image is yet published to GHCR).
  The override restores `build:` blocks for the three custom services.
  This widens the gap that issue
  [#185](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/185)
  already tracks (CI compose stack ≠ deploy code path); the design.md
  Risks table acknowledges this.

### CI compatibility (`.github/workflows/pr-checks.yml`)

- The `docker-build` job's three `--build-arg NEXT_PUBLIC_*` flags
  ([pr-checks.yml:150-152](.github/workflows/pr-checks.yml#L150-L152)) are
  removed — after the runtime-config refactor they would become silent
  no-ops.
- The `compose-health-check` job switches to the new
  `-f docker-compose.prod.yml -f docker-compose.ci.yml` invocation so the
  PR-time test stack continues to build locally. The flag pair is hoisted
  into a job-level `COMPOSE_FILES` env var so the 6+ compose commands
  inside the job don't drift apart.
- A new `web-unit-tests` job is added that runs
  `cd web && npm run test:unit` (matching the repo's existing
  `cd web && npm run build` pattern at
  [.github/workflows/pr-checks.yml:59](.github/workflows/pr-checks.yml#L59);
  the repo uses npm with workspaces, not pnpm).
- The existing `compose-health-check` job gains a Playwright step at the
  end (after the stack is healthy) that runs `cd web && npm run test:e2e`
  against the live stack, so the new `web/e2e/runtime-config.spec.ts`
  exercises the runtime-config refactor end-to-end.

### Frontend — runtime public config

- **BREAKING (internal):** `web/Dockerfile.bloom-web.prod` removes the four
  `NEXT_PUBLIC_*` `ARG`/`ENV` lines so the same image is bit-identical
  across environments. `web/Dockerfile.bloom-web.dev` retains the build-arg
  approach (dev images are environment-local and don't need
  promote-by-SHA).
- **New runtime-config module `web/lib/config/public-config.ts`** with a
  typed `PublicConfig` shape and `getPublicConfig()` accessor.
- **`PublicConfig` covers all eight public envs actually consumed today,
  not just the four currently baked at build time:**
  `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`,
  `NEXT_PUBLIC_SUPABASE_COOKIE_NAME`, `NEXT_PUBLIC_MCP_URL`,
  `NEXT_PUBLIC_APP_URL`, `NEXT_PUBLIC_COMMIT_SHA`,
  `NEXT_PUBLIC_STORAGE_URL`, `NEXT_PUBLIC_BLOOM_URL`.
  Critically, `NEXT_PUBLIC_COMMIT_SHA` is consumed by
  [web/app/api/health/route.ts:19](web/app/api/health/route.ts#L19) — the
  liveness probe the deploy workflow's `--wait` flag depends on.
- **New `app/api/config/route.ts`** returns the runtime-resolved public
  config on every request with `export const dynamic = 'force-dynamic'`,
  `export const revalidate = 0`, `export const runtime = 'nodejs'`, and
  `Cache-Control: no-store` (plus `Vary: Host` and `Pragma: no-cache` as
  CDN belt-and-suspenders).
- **All ~15 call sites that read `process.env.NEXT_PUBLIC_*` are migrated
  to `getPublicConfig()` / `usePublicConfig()`.** Server-rendered code
  resolves config from `process.env` server-side; client-side code
  resolves it via a `usePublicConfig()` hook backed by the `/api/config`
  endpoint with React `Suspense` boundary (React 19's `use()` semantics —
  verified via [web/package.json:43-44](web/package.json#L43-L44),
  `react@19.2.0`). The migration-lint test that fences this strips
  commented `process.env.NEXT_PUBLIC_*` reads so the
  `web/app/api/gitlab/user/route.ts:15` and
  `web/app/api/oauth/gitlab/exchange/route.ts:43,96` comments don't
  false-positive.
- **`web/app/api/client-info/route.ts` is deleted** — it already serves
  `{ api_url, anon_key }` from `NEXT_PUBLIC_*` and is now redundant with
  `/api/config`. Any caller of `/api/client-info` is migrated.

### Cross-environment safety fence

The runtime-config refactor moves public envs from build-time bake to
runtime resolution. That expands the blast radius of a misconfigured
`.env.staging` or `.env.prod`: a wrong URL/key/cookie at runtime can
silently route researcher writes to the wrong Supabase instance. This
change adds three concrete fences, each with an automated test:

1. **`SUPABASE_URL_HOSTS_ALLOWED` — explicit canonical mapping.** A new
   env var declares each environment's `server_internal_host=public_host`
   pair (concrete format defined in
   [design.md Decision 13](design.md)). The `/api/config` handler refuses
   to serve config whose `NEXT_PUBLIC_SUPABASE_URL` host isn't the declared
   public counterpart of `process.env.SUPABASE_URL`'s host. Operators must
   set this var per environment; absence fails container boot in
   production (skipped when `NODE_ENV !== 'production'` so local dev keeps
   working with the existing `NEXT_PUBLIC_SUPABASE_URL` fallback pattern).

2. **Anon-key project-ref check.** The `NEXT_PUBLIC_SUPABASE_ANON_KEY` is
   a JWT whose `ref` claim names the Supabase project. The `/api/config`
   handler decodes the JWT (base64url, with `-`/`_` substitution and
   padding), reads the `ref` claim, and asserts it matches the subdomain
   of `NEXT_PUBLIC_SUPABASE_URL`. For self-hosted Supabase (Bloom's setup),
   the JWT may not carry a `ref` claim; the fallback asserts `iss` ends
   with the hostname of `NEXT_PUBLIC_SUPABASE_URL`. This catches
   URL-correct-but-key-swapped misconfigurations.

3. **Per-environment cookie-name divergence (NEW — fixes a pre-existing
   bug).** Staging's `SUPABASE_COOKIE_NAME` changes from
   `sb-bloom-auth-token` to `sb-bloom-staging-auth-token`; prod stays
   `sb-bloom-auth-token`. A Python test (`tests/unit/test_env_cross_check.py`)
   asserts the names differ. **Operator note:** this invalidates every
   currently-active staging session on first deploy after PR-3; documented
   in `PROD_SETUP.md` and called out in the PR-3 description. **Rollback
   re-invalidates sessions a second time** — also noted in PROD_SETUP.md.

### Tooling

- **Extend the existing Vitest setup in `web/`.** Vitest is already
  installed on staging (`vitest@^4.1.8` per
  [web/package.json:65](web/package.json#L65), plus
  `vite-tsconfig-paths`; [web/vitest.config.ts](web/vitest.config.ts)
  already exists with `environment: 'node'` and a colocated-test
  discovery pattern; `"test:unit": "vitest run"` already in
  `web/package.json` scripts). This change adds `setupFiles` for
  `process.env` snapshot/restore, switches to `pool: 'forks'` for
  per-file isolation, adds `jsdom` as a devDep, and adds new colocated
  tests next to `web/lib/config/`, `web/app/api/config/`, and
  `web/instrumentation.ts`. React/Response tests use per-file
  `// @vitest-environment jsdom` directives so the workspace default
  stays `node`. Coverage thresholds and `@vitest/coverage-v8` are out
  of scope (deferred to the §12.3 follow-up). Playwright e2e setup at
  [web/playwright.config.ts](web/playwright.config.ts) is extended with
  one new spec (`web/e2e/runtime-config.spec.ts`).
- **Workflow/compose tests are written as Python pytest under `tests/unit/`,
  not shell.** This matches the existing pattern in
  [tests/unit/test_env_defaults.py](tests/unit/test_env_defaults.py) and
  [tests/unit/test_verify_env_parity.py](tests/unit/test_verify_env_parity.py)
  and runs in the existing `python-audit` CI job without extra wiring.

## Impact

- **Affected specs:**
  - NEW capability `image-publishing` — covers building custom images in
    CI, pushing to GHCR with a stable tag scheme, pinning third-party
    images by digest, parameterized `IMAGE_TAG`/`IMAGE_NAMESPACE`
    selection, rollback-by-tag (with abort-on-missing-handle), deploy-time
    pull-not-build, and exporting `BLOOM_IMAGE_SHA` into containers as
    forward-prep plumbing.
  - NEW capability `frontend-runtime-config` — covers `bloom-web`'s
    obligation to read public config at request time so one artifact is
    promotable across environments, plus the cross-environment safety
    fence (URL + anon-key + cookie-name).
- **Affected code:**
  - `web/Dockerfile.bloom-web.prod` — remove NEXT_PUBLIC ARGs/ENVs.
  - `docker-compose.prod.yml` — convert 3 `build:` blocks to `image:`
    refs; pin 10 third-party images by digest; pass `BLOOM_IMAGE_SHA` to
    all 3 custom service env blocks.
  - **New file** `docker-compose.ci.yml` — overlay restoring `build:`
    blocks for PR CI.
  - `.github/workflows/deploy.yml` — add `docker compose pull` + GHCR
    login on the staging job; remove `--build` on staging; update
    rollback paths (lines 464-466 prod, 851-853 staging) to pull prior
    `sha-<short>` and abort on missing handle; capture `IMAGE_TAG`
    alongside `previous_sha`.
  - `.github/workflows/pr-checks.yml` — drop `--build-arg NEXT_PUBLIC_*`
    lines (150-152); hoist `COMPOSE_FILES` to job env; add new
    `web-unit-tests` job; remove `NEXT_PUBLIC_*` from the docker-build env
    block; add Playwright step after compose stack is healthy.
  - New `build-images` job inside `deploy.yml` (per design.md Decision 9).
  - New files: `web/lib/config/public-config.ts`,
    `web/lib/config/validate-on-boot.ts` (pure validator),
    `web/app/api/config/route.ts`, `web/lib/config/use-public-config.ts`,
    `web/instrumentation.ts` (wires the validator into Next.js boot),
    `web/vitest.setup.ts` (env-snapshot/restore helper),
    `web/lib/config/__fixtures__/jwt.ts` (test helper for crafted
    anon-key JWTs), `web/e2e/runtime-config.spec.ts`, plus colocated
    test files: `web/lib/config/public-config.test.ts`,
    `web/lib/config/validate-on-boot.test.ts`,
    `web/lib/config/jwt-fixture.test.ts`,
    `web/lib/config/use-public-config.test.tsx`,
    `web/lib/config/no-direct-next-public-reads.test.ts`,
    `web/lib/config/no-skipped-tests.test.ts`,
    `web/app/api/config/route.test.ts`, `web/instrumentation.test.ts`.
  - Modified files: `web/vitest.config.ts` (add `setupFiles`,
    `pool: 'forks'`, exclude `__fixtures__/`); `web/package.json` (add
    `jsdom` devDep).
  - Touched files (~15 web modules switching from
    `process.env.NEXT_PUBLIC_*` to `getPublicConfig()` /
    `usePublicConfig()`): `middleware.ts`,
    `lib/supabase/{client,server,storage-url}.ts`,
    `app/api/{debug_users,gitlab/logged-in,gitlab/projects,health,ping_db}/route.ts`,
    `app/api/oauth/gitlab/{initiate,store}/route.ts`, `app/test/page.tsx`,
    `components/expression-lib/scrna-client.ts`,
    `components/mcp-chat-client.tsx`. Plus deletion of
    `app/api/client-info/route.ts`. Plus stripping commented
    `process.env.NEXT_PUBLIC_*` reads in `app/api/gitlab/user/route.ts`
    and `app/api/oauth/gitlab/exchange/route.ts`.
  - `.env.prod.defaults` — add `IMAGE_NAMESPACE`, `IMAGE_TAG`,
    `SUPABASE_URL_HOSTS_ALLOWED`.
  - `.env.staging.defaults` — same vars, plus
    `SUPABASE_COOKIE_NAME=sb-bloom-staging-auth-token` (was
    `sb-bloom-auth-token` — see "Cross-environment safety fence" above
    for operator implications).
  - `PROD_SETUP.md` — new "Container Registry & Image Tags" section ahead
    of STEP 2 covering GHCR login, `IMAGE_TAG` semantics, rollback across
    the cutover boundary, and the one-time staging-session invalidation
    (plus the rollback re-invalidates note).
  - New Python tests under `tests/unit/`: `test_compose_ghcr_refs.py`,
    `test_compose_ci_overlay_parity.py`,
    `test_compose_thirdparty_pinned.py`,
    `test_deploy_workflow_ghcr_shape.py`,
    `test_dockerfile_no_next_public_args.py`, `test_env_cross_check.py`,
    `test_pr_checks_workflow_shape.py`,
    `test_supabase_url_hosts_allowed_format.py`,
    `test_image_tag_resolves_in_compose_config.py` (behavioral test that
    `IMAGE_TAG=sha-abcd123 docker compose config` renders the right tag).
- **Operator surface:** staging deploy host needs `docker login ghcr.io`
  and the ability to pull from the GHCR namespace. New `IMAGE_TAG`,
  `IMAGE_NAMESPACE`, `SUPABASE_URL_HOSTS_ALLOWED`, and `BLOOM_IMAGE_SHA`
  env vars across deploy steps. Operators running compose locally must
  either authenticate to GHCR or use `-f docker-compose.ci.yml`.
  **One-time staging session invalidation** when PR-3 lands (cookie name
  changes); a rollback of PR-3 re-invalidates sessions again.
- **Test surface:** new `vitest` runner in `web/`; new
  `cd web && npm run test:unit` script; new Playwright spec
  (`web/e2e/runtime-config.spec.ts`) wired into `compose-health-check`;
  new Python tests enforce compose, workflow, env-parity, and
  `SUPABASE_URL_HOSTS_ALLOWED` format.

### Multi-PR landing plan

This change is implemented as a stack of 3 PRs against `staging`, all
satisfying the same OpenSpec proposal. Each PR completes a subset of
`tasks.md`; only PR-3 archives the change.

| PR | Targets | Tasks | What ships |
| --- | --- | --- | --- |
| **PR-1 — Foundation** | `staging` | §0–§3 | pr-checks.yml CI-compat fixes, `docker-compose.ci.yml` overlay, issue #107 label cleanup (§0.5), Vitest extension, public-config module + boot validator, `/api/config` endpoint, instrumentation wiring. All-additive; no user-visible change. |
| **PR-2 — Migration** | `staging` (rebased onto PR-1 once merged) | §4, §5 | Migrate ~15 callers; delete `client-info`. Build still bakes; behavior unchanged. CI green at end of §5. |
| **PR-3 — Cutover** | `staging` (rebased onto PR-2 once merged) | §6–§13 | Drop Dockerfile ARGs, GHCR build/push, compose `image:`, digest pins, deploy `pull`, rollback fix, env cross-check, `implement-cicd-pipeline` archival, PROD_SETUP.md update. |

**Rebase mechanic:** each PR targets `staging` directly (not the
predecessor's branch), to avoid GitHub closing a PR with "base branch was
deleted" when the predecessor merges and its branch is cleaned up per the
`cleanup-merged` workflow. The author rebases the successor onto `staging`
once the predecessor lands. *(This is a deliberate divergence from the
user's earlier preference for "merge each branch to its parent branch" —
worth confirming during PR-1 review whether to disable `cleanup-merged`
for the GHCR branches and use the parent-base style instead.)*

**Tests passing at each PR-merge milestone:**

| End of | New tests passing |
| --- | --- |
| PR-1 | `test_pr_checks_workflow_shape.py`; Vitest (colocated): `lib/config/public-config.test.ts`, `lib/config/validate-on-boot.test.ts`, `lib/config/jwt-fixture.test.ts`, `app/api/config/route.test.ts`, `instrumentation.test.ts` |
| PR-2 | above + Vitest: `lib/config/use-public-config.test.tsx`, ~15 per-caller migration tests, `lib/config/no-direct-next-public-reads.test.ts` (unskipped), `lib/config/no-skipped-tests.test.ts` |
| PR-3 | above + 8 Python tests (compose refs, ci overlay parity, third-party pinned, deploy workflow shape, dockerfile no NEXT_PUBLIC args, env cross check, hosts allowed format, image-tag resolution) + Playwright `runtime-config.spec.ts` |

Branch-protection rule: each PR needs a non-author reviewer (per project
memory).

## Deferred (explicitly out of scope)

| Item | Tracked in |
| --- | --- |
| Production deployment promoting an exact staging SHA + GitHub Environment approval gate | Issue #107, Week 3 |
| Mutable `latest` tag (per #107's tagging scheme for prod releases) | Issue #107, Week 3-4 — depends on prod promotion path |
| GHCR retention / image GC policy | Issue #107, Week 4 |
| Sticky rollback that doesn't re-pull the bad commit | Issue [#140](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/140) (this change makes #140's fix mechanically easier) |
| Full `PROD_SETUP.md` rewrite | Issue #107, Week 4 (this change adds the GHCR-specific operator section but doesn't restructure the rest) |
| Trivy CVE remediation in third-party images (pin-by-digest here is just for reproducibility) | Issue [#56](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/56) |
| UID/GID determinism in internal Dockerfiles | Issue [#158](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/158) |
| GitHub-Environment-scoped secrets for GHCR auth | Issue [#159](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/159) (this change uses repo-level secrets; env-scoped is a follow-up) |
| Sticky-rollback edge case where `.previous_sha` is empty | Issue [#162](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/162) |
| Partial `.env` files reaching docker compose | Issue [#174](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/174) |
| Rollback reverts code but not DB schema | Issue [#177](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/177) |
| Broader CI smoke-test path divergence (CI compose vs prod compose) | Issue [#185](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/185) (this change introduces `docker-compose.ci.yml` overlay which widens that gap) |
| Broad bloom-web unit/component test coverage | New issue filed in §12.3 covering CI/test work previously bundled in `implement-cicd-pipeline` |
| In-flight bind-mount-write safety during compose recreate (langchain-agent `PLOTS_DIR`, bloommcp `SLEAP_OUT_CSV`/`ANALYSIS_OUTPUT`) | New issue filed in §12.4 — requires `stop_grace_period` + atomic temp-file write patterns |
| **Per-result image-SHA traceability stamping** (bloommcp manifests + langchain plot descriptors + `/api/health` reporting) | **New issue filed in §12.6** — requires bloommcp `Manifest` schema v2 migration (current schema is `extra="forbid"` per `bloommcp/storage/schema.py`), a langchain plot-descriptor format decision (PNG metadata vs sidecar JSON), and adding a 9th `imageSha` key to `PublicConfig`. The `BLOOM_IMAGE_SHA` env injection in this change is forward-prep so the follow-up is pure consumer-side code. |
| Cross-environment data-integrity umbrella issue (broader risk class beyond the URL/key/cookie fence this change ships) | **New issue filed in §12.7** — tracks future regressions/related vectors (storage URL, MCP URL, GitLab OAuth callbacks) |

### Overlap with `implement-cicd-pipeline`

The in-flight change `implement-cicd-pipeline` (0/121 tasks) proposes
contradictory requirements: it picks Jest + 70% coverage threshold (vs this
change's Vitest + 0% threshold) and tags images with `latest` + git SHA
(vs this change's `sha-<short>` + `staging`). Both cannot pass
`openspec validate --strict` together once archived. **§12 of this
change's `tasks.md` archives `implement-cicd-pipeline` as part of PR-3**,
explicitly documenting in the successor issue each of its 12 ADDED
requirements and their status (some already landed via other PRs — uv
adoption via #126, dependency security scanning via Trivy in pr-checks.yml,
the CD pipeline subset via #122 and this change). `--skip-specs` is used
because the contradictions with this change's specs cannot coexist; the
deliberate discard is justified by the per-requirement audit in §12.3's
issue.
