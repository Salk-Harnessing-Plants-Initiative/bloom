## Why

[#406](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/406) — bloommcp runs
every database and Storage operation as the single shared `bloom_agent` role and has no notion
of *who* is calling it. That blocks usage tracking / audit (the "usage tracking and audit
logging" item of #34). A same-day epic, [#554](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/554)
(created ~4 hours before this issue's re-scoping comment), independently frames this precisely:
*"#406 adds an audit trail of who ran something; it does not change what they could see."* #554
also flags #34 itself as "stale, superseded by #522's much more specific findings" — #34 remains
open and is still an accurate motivating reference, just not the sharpest one available; this
proposal cites both.

The issue's original design proposed Supabase OIDC + JWKS verification for a fully general
external-MCP-client story. A 2026-07-07 review comment (quoted in the issue) found that design
doesn't match this deployment: Supabase here signs JWTs **symmetrically** (`GOTRUE_JWT_SECRET` /
`PGRST_JWT_SECRET` / etc. all source one `JWT_SECRET` env var —
[docker-compose.prod.yml:349,387](../../../docker-compose.prod.yml#L349),
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
  ([supabase_client.py:94-107](../../../bloommcp/src/bloom_mcp/supabase_client.py#L94-L107)); no
  user JWT is read or forwarded anywhere in that module.
- The `bloommcp-data` bucket is already scoped to `bloom_agent`-only INSERT/UPDATE via RLS
  policies introduced 7 weeks ago
  ([20260605000000_create_bloommcp_data_bucket.sql](../../../supabase/migrations/20260605000000_create_bloommcp_data_bucket.sql)).
- `langchain-agent` already verifies its own session JWT the same way this proposal verifies
  bloommcp's identity header — `get_current_user()`
  ([deps.py:26-52](../../../langchain/deps.py#L26-L52)): PyJWT, `algorithms=["HS256"]`,
  `audience="authenticated"`, extracts `payload["sub"]`, distinguishes `ExpiredSignatureError`
  from other `InvalidTokenError`s. (`deps.py:21-23` separately hard-requires `JWT_SECRET` at
  **import** time — this proposal deliberately does not mirror that part; see design.md
  Decision 5.)

## What Changes

- **New `bloom_mcp.identity` module** verifies a caller-supplied `X-Bloom-Identity` header as an
  HS256 JWT against a shared `JWT_SECRET` env var, `audience="authenticated"` — mirroring
  `langchain/deps.py:get_current_user()`'s exact verification logic (same library, same
  algorithm allow-list, same audience, same `sub` extraction), adapted from FastAPI's
  `HTTPException` to a plain ASGI-middleware-compatible result/error type since bloommcp has no
  FastAPI dependency-injection layer. The resolved `sub` is additionally required to look like a
  Supabase user id (UUID-shaped) and must not equal the reserved literal `anonymous` — either
  failure is treated the same as any other invalid token (see design.md Decision 1).
- **One raw-ASGI Starlette middleware** (not `BaseHTTPMiddleware` — see design.md Decision 3),
  added to the single `Starlette` app `build_app()` already returns
  ([server.py:80-100](../../../bloommcp/src/bloom_mcp/server.py#L80-L100)), reads
  `X-Bloom-Identity` on every request before it is routed to any `Mount` (combined surface, or
  any of the three per-section sub-apps — one app, one middleware stack, so this covers all of
  them uniformly, no per-section wiring), and records usage itself (see below) — no
  `contextvars.ContextVar`, no coupling to tool-dispatch code. Behavior:
  - Header **absent** → proceed as anonymous. No regression to today's behavior (no client
    sends this header yet — see Scope below).
  - Header **present and valid** → proceed, caller identity is the token's `sub` (lowercased).
  - Header **present and invalid/expired/malformed-`sub`/reserved-value `sub`/duplicated** →
    reject the request (`401`), rather than silently downgrading to anonymous or silently
    resolving a duplicate header to one occurrence. This is the issue's literal wording
    ("invalid/expired tokens are rejected" is a separate bullet from "an absent header falls back
    to anonymous") and matches this codebase's fail-closed precedent (#265).
- **Usage attribution is at request/mounted-surface granularity** (`core`, `sleap_roots`,
  `phenotyping_segmentation`, or `combined` for the root surface), not the specific MCP tool
  name, recorded directly by the middleware above via a non-blocking call
  (`bloom_mcp.usage.record_usage_async`, run on a background thread so the DB round-trip never
  adds latency to the request it's attributed to). **This was not the original design in this
  proposal's history:** an intermediate revision attributed usage per-tool-call via a
  `contract.wrap.register()` wrapper and a `ContextVar`, which a later, deeper review found
  cannot work correctly for a reused MCP session — FastMCP's `StreamableHTTPSessionManager` runs
  the actual tool-dispatch loop in one long-lived task per session, so a `ContextVar` set by a
  *later* request's own task can never reach it. See design.md Decision 4 for the full,
  confirmed-not-assumed technical history. `/health` is explicitly excluded from recording.
- **New `bloommcp_usage` table** (migration + rollback, following the repo's
  `supabase/migrations/` + `supabase/rollbacks/` convention) records identity, first/last seen,
  request count, and last action (the mounted surface, per above), upserted once per qualifying
  request by the middleware via a **new** `call_rpc()` helper added to `supabase_client.py` by
  this change (not a reuse of existing code — see design.md Decision 8 for why, and for a
  disclosed coordination note with a concurrently open, unrelated PR that independently
  introduces a same-named helper). Unauthenticated calls collapse into a single
  `identity = 'anonymous'` row so "anonymous entries when no identity header is present" (the
  issue's wording) doesn't require a fabricated per-caller key for traffic with no identity at
  all. A usage-write failure is caught and logged, never fails the underlying request — usage
  tracking is observability, not a functional gate. This table is a rolling aggregate (last-known
  state per identity), not an append-only log — see design.md Risks for why that matches the
  issue's own AC shape and what it does *not* provide.
- **`JWT_SECRET` wired into bloommcp's environment** in `docker-compose.dev.yml` and
  `docker-compose.prod.yml` (one line each, `JWT_SECRET: ${JWT_SECRET}` — the value already
  exists at the top-level `.env` in every environment; dev has it committed, staging/prod
  inject it from the existing `STAGING_JWT_SECRET`/`PROD_JWT_SECRET` GitHub Secrets already used
  by `langchain-agent`/GoTrue/PostgREST — no new secret to create). CI needs no changes:
  `docker-compose.ci.yml`/`docker-compose.ci-cache.yml` are build-only overlays that
  deliberately carry no environment variables
  ([docker-compose.ci.yml:40](../../../docker-compose.ci.yml#L40)), and CI's `.env.ci` already
  populates `JWT_SECRET` from the `CI_JWT_SECRET` GitHub Secret
  ([pr-checks.yml:671](../../../.github/workflows/pr-checks.yml#L671)) — once bloommcp's
  compose block gains the line, CI picks it up automatically. Validated lazily (only when a
  request actually carries `X-Bloom-Identity`), matching the codebase's existing
  validate-at-point-of-use convention for `BLOOM_AGENT_KEY`/`SUPABASE_URL`
  ([supabase_client.py:46-68](../../../bloommcp/src/bloom_mcp/supabase_client.py#L46-L68))
  rather than requiring it unconditionally at boot for a feature no current client exercises yet
  (a deliberate departure from `deps.py`'s own hard-fail-at-import posture for the same secret —
  see design.md Decision 5).
- **PyJWT** is already a transitive bloommcp dependency today (pulled in via `supabase-py`'s
  `supabase_auth` sub-dependency, pinned at `2.13.0` in `bloommcp/uv.lock`) — this change
  promotes it to a direct dependency in `pyproject.toml` rather than adding a new one.
- **The caller token is never forwarded or used as a DB/Storage authorization principal** — this
  is already true today and this change does not alter it; it is pinned down as a spec
  requirement (with a regression-guard scenario covering every registered tool, not one sample)
  rather than left as an implicit assumption, because it is the whole reason this design is safe
  to ship without RLS/`auth.uid()` changes.
- **Transport-level bearer auth (`BLOOMMCP_API_KEY`) is unaffected** — pinned down as its own
  spec requirement (the issue's re-scoped ACs list this as a standalone bullet; earlier drafts of
  this proposal only covered it narratively, not as a formal requirement).

## Impact

- **Affected specs:**
  - `bloommcp-caller-identity` (new capability) — ADDED requirements for header verification,
    the never-forwarded/never-authorizing invariant, the `bloommcp_usage` table, `JWT_SECRET`'s
    lazy-validation contract, and transport-bearer-auth non-interference.
  - No spec delta to `bloommcp-tool-contract`: `register()` and `as_mcp_tool` are untouched by
    this change — an intermediate revision would have added a usage-recording wrapper there, but
    the final design (usage recorded by the middleware itself; see design.md Decision 4) doesn't
    touch `contract/wrap.py` at all.
  - No spec delta to `bloommcp-packaging` — see design.md Decision 6 for why `JWT_SECRET`'s
    lazy-validation contract lives in this change's own capability instead.
- **Affected code (to be written during implementation, not this proposal):**
  - New: `bloommcp/src/bloom_mcp/identity.py` (verification + the raw-ASGI middleware, which also
    records usage), `bloommcp/src/bloom_mcp/usage.py` (the non-blocking `record_usage_async`
    helper), a new `call_rpc()` helper in `supabase_client.py`, one migration + rollback under
    `supabase/migrations/` / `supabase/rollbacks/`.
  - Modified: `bloommcp/src/bloom_mcp/server.py` (`build_app()` gains the middleware),
    `docker-compose.dev.yml`, `docker-compose.prod.yml` (bloommcp's `environment:` block gains
    `JWT_SECRET: ${JWT_SECRET}`), `bloommcp/pyproject.toml` (PyJWT promoted to direct).
    `contract/wrap.py` is **not** modified — see above.
  - Tests: header verification (valid/absent/expired/malformed/wrong-audience/wrong-algorithm/
    malformed-or-reserved-`sub`/trailing-newline/duplicate-header), middleware coverage across
    the combined surface and each mounted section including a live subprocess test confirming
    independence from the `BLOOMMCP_API_KEY` bearer check, the never-forwarded invariant, usage
    upsert semantics (new identity, repeat identity, anonymous collapsing, concurrent-first-request
    race, write-failure non-fatality, non-blocking submission) via a new fake-RPC test double for
    the fast unit tier plus a real-Postgres concurrency test placed under the repo's existing
    root-level `tests/integration/` convention, `JWT_SECRET`-unset-but-header-present behavior.

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
- **No append-only usage log.** `bloommcp_usage` answers "what did identity X do most recently,
  and how many times" — it cannot answer "what did identity X do over time," since each upsert
  overwrites `last_action`/`last_seen` in place. The issue's own acceptance-criteria shape
  (identity, first/last seen, request count, last action) describes this aggregate, not a log; a
  full historical audit log is a larger, separate change if later needed.
- **No data-retention policy.** The table is bounded by distinct-caller count (small, no
  unbounded growth), so no TTL/purge is implemented; indefinite retention of this bounded
  aggregate is accepted as proportionate for this change.
- **#265** (hard-fail on missing `BLOOMMCP_API_KEY`) is a prerequisite for the *shared bearer
  transport* to be fail-closed in prod, assumed but not implemented by this issue; #265 remains
  open/unmerged. This proposal's own fail-closed behavior (Decision 2 in design.md) does not
  depend on #265 landing first, but production defense-in-depth does.
