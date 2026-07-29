## Why

[#406](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/406) — bloommcp runs
every database and Storage operation as the single shared `bloom_agent` role and has no notion
of *who* is calling it. That blocks usage tracking / audit (the "usage tracking and audit
logging" item of #34) and trustworthy provenance for a run or upload.

The issue's original design proposed Supabase OIDC + JWKS verification for a fully general
external-MCP-client story. A 2026-07-07 review comment (quoted in the issue) found that design
doesn't match this deployment: Supabase here signs JWTs **symmetrically** (`GOTRUE_JWT_SECRET` /
`PGRST_JWT_SECRET` / etc. all source one `JWT_SECRET` env var — `docker-compose.prod.yml:110`),
not with per-project asymmetric keys, so there is no real JWKS endpoint to verify an ID token
against today. A follow-up review comment posted 2026-07-29 resolved the issue's two open
questions and **re-scoped it** to a bloommcp-side-only, near-term slice, deferring OIDC/JWKS and
per-user upload namespacing (#388) to separate future issues. This proposal implements exactly
that re-scoped slice — the acceptance criteria below are copied verbatim from the issue's
2026-07-29 resolution comment, not the original issue body.

**What already exists today (unrelated to this issue, confirmed in code):**

- bloommcp already runs every DB/Storage call as `bloom_agent`, never forwarding a caller's own
  token. The only credential in play is `BLOOM_AGENT_KEY`, read once per call by
  `get_postgrest_client()`
  ([supabase_client.py:98-111](../../../bloommcp/src/bloom_mcp/supabase_client.py#L98-L111)); no
  user JWT is read or forwarded anywhere in that module.
- The `bloommcp-data` bucket is already scoped to `bloom_agent`-only INSERT/UPDATE via RLS
  policies introduced 7 weeks ago
  ([20260605000000_create_bloommcp_data_bucket.sql](../../../supabase/migrations/20260605000000_create_bloommcp_data_bucket.sql)).
- `langchain-agent` already verifies its own session JWT the same way this proposal verifies
  bloommcp's identity header — `get_current_user()`
  ([deps.py:21-52](../../../langchain/deps.py#L21-L52)): PyJWT, `algorithms=["HS256"]`,
  `audience="authenticated"`, extracts `payload["sub"]`, distinguishes `ExpiredSignatureError`
  from other `InvalidTokenError`s.

## What Changes

- **New `bloom_mcp.identity` module** verifies a caller-supplied `X-Bloom-Identity` header as an
  HS256 JWT against a shared `JWT_SECRET` env var, `audience="authenticated"` — mirroring
  `langchain/deps.py:get_current_user()`'s exact verification logic (same library, same
  algorithm allow-list, same audience, same `sub` extraction), adapted from FastAPI's
  `HTTPException` to a plain Starlette-compatible result/error type since bloommcp has no
  FastAPI dependency-injection layer.
- **One Starlette middleware**, added to the single `Starlette` app `build_app()` already
  returns ([server.py:80-100](../../../bloommcp/src/bloom_mcp/server.py#L80-L100)), reads
  `X-Bloom-Identity` on every request before it is routed to any `Mount` (combined surface, or
  any of the three per-section sub-apps — one app, one middleware stack, so this covers all of
  them uniformly, no per-section wiring). Behavior:
  - Header **absent** → proceed as anonymous. No regression to today's behavior (no client
    sends this header yet — see Scope below).
  - Header **present and valid** → proceed, caller identity is the token's `sub`.
  - Header **present and invalid/expired** → reject the request (`401`), rather than silently
    downgrading to anonymous. This is the issue's literal wording ("invalid/expired tokens are
    rejected" is a separate bullet from "an absent header falls back to anonymous") and matches
    this codebase's fail-closed precedent (#265) — a caller that *asserts* an identity that
    doesn't verify is a stronger signal than a caller that asserts nothing.
- **New `bloommcp_usage` table** (migration + rollback, following the repo's
  `supabase/migrations/` + `supabase/rollbacks/` convention) records identity, first/last seen,
  request count, and last action, upserted once per request by the same middleware via the
  existing `call_rpc()` seam
  ([supabase_client.py:134-150](../../../bloommcp/src/bloom_mcp/supabase_client.py#L134-L150)).
  Unauthenticated requests collapse into a single `identity = 'anonymous'` row so "anonymous
  entries when no identity header is present" (the issue's wording) doesn't require a
  fabricated per-caller key for traffic with no identity at all. A usage-write failure is
  caught and logged, never fails the underlying tool call — usage tracking is observability,
  not a functional gate.
- **`JWT_SECRET` wired into bloommcp's environment** in `docker-compose.dev.yml` and
  `docker-compose.prod.yml` (one line each, `JWT_SECRET: ${JWT_SECRET}` — the value already
  exists at the top-level `.env` in every environment; dev has it committed, staging/prod
  inject it from the existing `STAGING_JWT_SECRET`/`PROD_JWT_SECRET` GitHub Secrets already used
  by `langchain-agent`/GoTrue/PostgREST — no new secret to create). Validated lazily (only when
  a request actually carries `X-Bloom-Identity`), matching the codebase's existing
  validate-at-point-of-use convention for `BLOOM_AGENT_KEY`/`SUPABASE_URL`
  ([supabase_client.py:50-72](../../../bloommcp/src/bloom_mcp/supabase_client.py#L50-L72)) rather
  than requiring it unconditionally at boot for a feature no current client exercises yet.
- **The caller token is never forwarded or used as a DB/Storage authorization principal** — this
  is already true today and this change does not alter it; it is pinned down as a spec
  requirement (with a regression-guard scenario) rather than left as an implicit assumption,
  because it is the whole reason this design is safe to ship without RLS/`auth.uid()` changes.

## Impact

- **Affected specs:**
  - `bloommcp-caller-identity` (new capability) — ADDED requirements for header verification,
    the never-forwarded/never-authorizing invariant, the `bloommcp_usage` table, and
    `JWT_SECRET`'s lazy-validation contract.
  - No changes to `bloommcp-tool-contract` or `bloommcp-packaging` — see design.md Decision 4
    for why usage-recording deliberately does *not* reach into the `@as_mcp_tool` contract
    wrapper (Tier 1, currently I/O-free), and Decision 6 for why `JWT_SECRET` isn't folded into
    `bloommcp-packaging`'s existing "Lazy Environment Validation" requirement text (that
    requirement's enumerated var list doesn't even include the already-existing
    `BLOOMMCP_API_KEY`, so this capability documents its own lazy-validation contract instead of
    retrofitting an unrelated one).
- **Affected code (to be written during implementation, not this proposal):**
  - New: `bloommcp/src/bloom_mcp/identity.py` (verification), a new Starlette middleware (same
    module or a small `bloommcp/src/bloom_mcp/usage.py`), `bloommcp/src/bloom_mcp/server.py`
    (`build_app()` gains the middleware), one migration +
    rollback under `supabase/migrations/` / `supabase/rollbacks/`.
  - Modified: `docker-compose.dev.yml`, `docker-compose.prod.yml` (bloommcp's `environment:`
    block gains `JWT_SECRET: ${JWT_SECRET}`).
  - Tests: header verification (valid/absent/expired/malformed/wrong-audience), middleware
    coverage across the combined surface and each mounted section, the never-forwarded
    invariant, usage upsert semantics (new identity, repeat identity, anonymous collapsing,
    write-failure non-fatality), `JWT_SECRET`-unset-but-header-present behavior.

## Scope / Non-Goals

- **Does not make any client actually send `X-Bloom-Identity`.** Bloom-web/langchain-agent
  wiring is explicitly out of scope — the issue's own re-scoping comment traced
  `langchain/server.py`'s `MultiServerMCPClient`/`mcp_tools` construction to FastAPI startup
  `lifespan`, reused for every user's every request with no per-request/per-user hook to attach
  a caller-specific header today. That is real, unscoped work in a different service with
  different risk (perf, caching) and is tracked as a **separate, not-yet-filed** langchain-agent
  issue. Until that ships, every real request through bloommcp today omits the header and takes
  the anonymous path — this change is inert in production until its sibling issue lands.
- **OIDC/JWKS verification, asymmetric signing keys, and external-MCP-client auth** are the
  issue's own stretch items — unaffected, deferred, not addressed here.
- **Per-user upload namespacing (#388)** is not addressed — this proposal supplies a verified
  identity for the usage table only, not a namespacing key.
- **Per-tool attribution** (recording *which specific MCP tool* a caller invoked, vs. which
  mounted section/path) is not attempted — see design.md Decision 4.
- **#265** (hard-fail on missing `BLOOMMCP_API_KEY`) is a prerequisite for the *shared bearer
  transport* to be fail-closed in prod, assumed but not implemented by this issue; #265 remains
  open/unmerged. This proposal's own fail-closed behavior (Decision 5 in design.md) does not
  depend on #265 landing first, but production defense-in-depth does.
