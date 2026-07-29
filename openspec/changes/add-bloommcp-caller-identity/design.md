## Context

[#406](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/406) went through two
rounds of review before this proposal was written. The 2026-07-07 comment found a genuine
inconsistency in the original acceptance criteria (the `aud`-rejection rule can't hold for a
Supabase session access token, whose `aud` *is* `"authenticated"`) and an unscoped transport
question (how does a second, identity-carrying token reach bloommcp alongside the shared
`BLOOMMCP_API_KEY` bearer, across every mounted section). The 2026-07-29 comment resolved both:
two verification profiles depending on token source, and — since this deployment signs JWTs
symmetrically, not with per-project asymmetric keys behind a JWKS endpoint — the near-term path
mirrors `langchain/deps.py`'s existing HS256/`JWT_SECRET` verification instead of introducing
JWKS. It also traced the langchain-agent side of the transport question far enough to conclude
it's out of scope (startup-`lifespan`-scoped MCP client construction, no per-request hook today)
and re-scoped the issue to bloommcp-side-only. This design implements that resolved, re-scoped
slice.

## Goals / Non-Goals

- **Goals:** verify an optional `X-Bloom-Identity` header using the same HS256/`JWT_SECRET`
  mechanism `langchain-agent` already uses for its own session verification; never let that
  identity carry DB/Storage authority; record usage (identity, first/last seen, count, last
  action) including an anonymous bucket for callers with no header.
- **Non-Goals:** OIDC/JWKS verification; asymmetric signing keys; making any real client send
  the header; per-user upload namespacing (#388); per-tool-name usage attribution; changing how
  bloommcp authenticates to Postgres/Storage (`bloom_agent` stays exactly as-is).

## Decisions

### Decision 1 — Verification mirrors `langchain/deps.py:get_current_user()` exactly, not a new scheme

`bloom_mcp.identity` uses PyJWT (already a transitive concern the langchain-agent side already
depends on; bloommcp does not have it as a dependency today and will add it), `algorithms=["HS256"]`,
`audience="authenticated"`, extracts `payload["sub"]`. This is a deliberate 1:1 mirror of
[deps.py:38-52](../../../langchain/deps.py#L38-L52), not an independent design, because the
2026-07-29 resolution comment explicitly says the near-term path "should mirror that precedent
exactly, not introduce JWKS verification that doesn't match today's signing setup."

- **Alternative considered:** the original issue's JWKS-based verification against
  `/auth/v1/.well-known/jwks.json` (reusing `/api/client-info` for discovery, per the issue's
  final comment). Rejected for *this* change — this deployment signs symmetrically today, so
  there is no real per-project JWKS to verify against; that comment's discovery mechanism
  remains useful whenever the OIDC stretch item is picked up as its own change.

### Decision 2 — A present-but-invalid token is rejected (401), not downgraded to anonymous

The issue's re-scoped AC reads: "invalid/expired tokens are rejected" as one clause and "an
absent header falls back to anonymous" as a separate one. This proposal treats those as two
distinct outcomes, not one "best-effort" fallback.

- **Alternative considered:** treat any unverifiable header (absent or invalid) as anonymous —
  simpler, and would still satisfy "no regression to today's behavior" for the absent-header
  case. Rejected: a caller that positively asserts an identity that fails verification (expired,
  wrong audience, bad signature) is a materially different, more suspicious signal than a caller
  that asserts nothing — silently absorbing it as anonymous would mask both legitimate
  client-side bugs (clock skew, stale cached token) and adversarial probing, with no operator
  visibility either way. Fail-closed on a positive-but-broken assertion matches this codebase's
  existing precedent (#265, hard-fail on missing `BLOOMMCP_API_KEY` rather than silently
  dropping to dev mode).
- **Consequence:** this rejection happens in the new outer middleware, *before* FastMCP's own
  `BLOOMMCP_API_KEY` bearer check runs (the middleware wraps the whole `Starlette` app; FastMCP's
  `TokenVerifier` executes only once a request is routed into one of the mounted `Mount` sub-apps
  — see Decision 3). A request can therefore be rejected for a bad identity header even before
  its bearer token is checked. This is intentional (both are independent, either failing is
  sufficient reason to reject) and has no current-production impact since no client sends the
  header yet (see proposal.md Scope).

### Decision 3 — One Starlette middleware on the app `build_app()` already returns, not per-section wiring

`build_app()` ([server.py:80-100](../../../bloommcp/src/bloom_mcp/server.py#L80-L100)) already
composes the combined surface and every per-section sub-app into a **single** `Starlette`
instance via `Mount`. Starlette dispatches `Mount` as a route match inside the router; middleware
sits outside the router in the ASGI call chain. So one middleware added to this single app (via
`Starlette(routes=routes, middleware=[...])` or `.add_middleware(...)` before `main()`'s
`uvicorn.run(build_app(), ...)` call) sees every request regardless of which `Mount` it
eventually resolves to — combined `/mcp`, every `/<section>/mcp`, and `/health` alike — with no
per-section changes to `bloom_mcp/sections/*/__init__.py`.

- **No conflict with `BLOOMMCP_API_KEY`:** that check is wired per-`FastMCP`-instance via
  `auth=auth_provider` ([auth.py](../../../bloommcp/src/bloom_mcp/auth.py), shared by
  `server.py:61` and all three section constructors) and reads the `Authorization` header, a
  different header than `X-Bloom-Identity`. The new middleware runs first in the ASGI chain
  (outer), FastMCP's bearer check runs second (inner, inside whichever `Mount` the request
  routes to) — see Decision 2's consequence for the one behavioral interaction between the two.

### Decision 4 — Usage is attributed to the mounted section (request path), not the specific MCP tool name

The `bloommcp_usage.last_action` column records which mounted surface handled the request (e.g.
`core`, `sleap_roots`, `phenotyping_segmentation`, or `combined` for the root `/mcp` — derived
from the same path the middleware already inspects to decide whether a request is in scope),
**not** the specific tool name (e.g. `qc_clean`).

- **Alternative considered:** attribute to the exact tool name, which the issue's original
  wording ("last action") arguably suggests more naturally. Rejected for this change: the tool
  name is only known once FastMCP parses the JSON-RPC body and dispatches to a specific
  `@as_mcp_tool`-wrapped callable
  ([wrap.py](../../../bloommcp/src/bloom_mcp/contract/wrap.py)) — getting it would mean either
  (a) parsing the JSON-RPC request body a second time in the outer middleware (duplicate,
  fragile parsing of a protocol FastMCP already owns), or (b) adding a network call (the usage
  upsert) inside `contract/wrap.py`'s `as_mcp_tool` decorator, which the module's own docstring
  documents as foundational and side-effect-free ("Tier 1 does not modify server.py"; no I/O
  appears anywhere in that module today). Crossing that boundary for a coarse audit table is
  disproportionate scope creep for this change. Section-level granularity still answers "who
  used bloommcp and roughly what for," which is what #34's audit-logging item and this issue ask
  for; a future change wanting per-tool attribution has a clean, separate seam to add it
  (`contract/wrap.py`, or a dedicated hook `register()` could apply) without revisiting this
  design.
- **Consequence:** no `contextvars.ContextVar` is needed to carry identity from the middleware
  into tool-call code, since the middleware itself has everything it needs (identity + path) to
  write the usage row directly, synchronously, before calling `await call_next(request)` or
  wrapping the response. This also sidesteps an open question the alternative would have raised
  — whether a `ContextVar` set in the outer Starlette middleware is reliably visible inside
  FastMCP's tool-dispatch code path, which may or may not run tool callables on the same
  `asyncio` task (unverified from this repo; FastMCP is a dependency, not code in this
  repository). Deferred, not silently assumed, to whichever future change actually needs
  identity inside tool-call code.

### Decision 5 — `JWT_SECRET` is validated lazily, only when a request actually carries the header

Mirrors the existing convention: `bloom_mcp.supabase_client._require_env()`
([supabase_client.py:50-72](../../../bloommcp/src/bloom_mcp/supabase_client.py#L50-L72)) defers
validation to call time so `import bloom_mcp` and the fakes-based unit tests run with no runtime
env at all. `JWT_SECRET` follows the same pattern: unset in an environment that never receives
an `X-Bloom-Identity` header is a no-op (today, that's every environment — see proposal.md
Scope). If a request *does* carry the header and `JWT_SECRET` is unset, that is a server
misconfiguration, not a client error: the middleware returns a `500`-class response naming the
missing variable, rather than either crashing the process or silently treating the caller as
anonymous (which would hide a real deploy defect behind what looks like ordinary anonymous
traffic).

- **Alternative considered:** require `JWT_SECRET` unconditionally at boot, alongside
  `SUPABASE_URL`/`BLOOM_AGENT_KEY`. Rejected — it would force every current deployment (none of
  which receive the header yet) to set a var for a code path nothing exercises, which is exactly
  the import-time/boot-time-validation-creep this codebase's "Lazy Environment Validation"
  convention (`bloommcp-packaging`) was written to avoid.

### Decision 6 — `bloommcp-packaging`'s existing spec is left untouched; `JWT_SECRET`'s contract lives in the new capability

`bloommcp-packaging`'s "Lazy Environment Validation" requirement enumerates `SUPABASE_URL`,
`BLOOM_AGENT_KEY`, and the `BLOOM_*_DIR`/`BLOOM_PLOTS_URL` vars — but not `BLOOMMCP_API_KEY`,
which already exists and is already lazily-resolved the same way. Since that requirement's
enumeration is already not exhaustive of every bloommcp env var, adding `JWT_SECRET` to it would
imply a completeness guarantee the existing spec doesn't actually make. This proposal's own
capability states `JWT_SECRET`'s lazy-validation contract directly instead.

### Decision 7 — Anonymous requests collapse into one sentinel row, not one row per caller

`bloommcp_usage.identity` is the table's primary key, so it cannot be `NULL`. Every request with
no `X-Bloom-Identity` header (all current production traffic — see proposal.md Scope) upserts
against the literal string `'anonymous'`, accumulating `request_count` and `last_seen` across all
unauthenticated callers combined.

- **Alternative considered:** key anonymous usage by caller IP or MCP session id. Rejected — no
  session concept exists at this transport layer today, IP-based tracking raises its own
  privacy/retention questions this issue never asked for, and the issue's own wording ("anonymous
  entries when no identity header is present") reads as a single aggregate bucket, not
  per-anonymous-caller tracking.

## Data Model — `bloommcp_usage`

Following the repo's table conventions (`ALTER DEFAULT PRIVILEGES` already grants `bloom_admin`
`ALL` and `bloom_agent` `SELECT` on every new `public` table —
[20260414002000_security_groups.sql:52-54](../../../supabase/migrations/20260414002000_security_groups.sql#L52-L54)
— so only `bloom_agent`'s `INSERT`/`UPDATE` need an explicit grant, mirroring
[20260605000000_create_bloommcp_data_bucket.sql:61](../../../supabase/migrations/20260605000000_create_bloommcp_data_bucket.sql#L61)):

```sql
CREATE TABLE bloommcp_usage (
    identity      TEXT PRIMARY KEY,
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_count BIGINT NOT NULL DEFAULT 1,
    last_action   TEXT
);

ALTER TABLE bloommcp_usage ENABLE ROW LEVEL SECURITY;

-- admin_all_bloommcp_usage (ALL, bloom_admin), agent_read_bloommcp_usage (SELECT, bloom_agent)
-- follow the gravi_experiments template exactly
-- (20260527180100_create_gravi_experiments_table.sql). agent_insert_bloommcp_usage /
-- agent_update_bloommcp_usage (bloom_agent, USING/WITH CHECK true) follow the
-- agent_insert_bloommcp_data / agent_update_bloommcp_data template
-- (20260605000000_create_bloommcp_data_bucket.sql) since there is no natural per-row ownership
-- predicate for a usage-aggregate table. No bloom_user policies — this table is
-- bloommcp/ops-only, not user-facing.

GRANT INSERT, UPDATE ON public.bloommcp_usage TO bloom_agent;
```

The upsert itself is a single Postgres function, `record_bloommcp_usage(p_identity text,
p_action text)`, called through the existing `call_rpc()` seam
([supabase_client.py:134-150](../../../bloommcp/src/bloom_mcp/supabase_client.py#L134-L150)) so
this reuses bloommcp's one existing RPC-calling code path rather than introducing a new
`.table().upsert()` call shape:

```sql
INSERT INTO bloommcp_usage (identity, last_action)
  VALUES (p_identity, p_action)
  ON CONFLICT (identity) DO UPDATE SET
    last_seen = now(),
    request_count = bloommcp_usage.request_count + 1,
    last_action = EXCLUDED.last_action;
```

The exact function body, migration filename/timestamp, and matching rollback are implementation
(tasks.md), not fixed by this design doc.

## Risks / Trade-offs

- **Section-level, not tool-level, usage attribution** (Decision 4) is a real reduction in
  granularity versus the issue's original "last action" wording. Accepted as the right scope
  boundary for this change; documented so it isn't mistaken for an oversight.
- **This entire feature is inert until a separate, not-yet-filed langchain-agent change ships**
  (proposal.md Scope) — there is no way to demo the identity-resolved path end-to-end from
  Bloom-web today. Mitigated by testing the middleware directly (a client sending
  `X-Bloom-Identity` by hand against bloommcp), which is sufficient to verify this change's own
  contract independent of its unshipped prerequisite.
- **The rejection behavior (Decision 2) has no real-traffic exposure to validate against** —
  since no current client sends the header, "invalid token → 401" has only ever been exercised
  by this change's own tests. Accepted; this is standard for additive, currently-unreachable
  code paths.

## Migration Plan

Purely additive: a new module, a new middleware wired into `build_app()`, one new table, one new
env var read only when the (currently never-sent) header is present. No existing behavior
changes for any current deployment. Rollback is reverting the code change and running the
migration's paired rollback SQL (removing `bloommcp_usage`); no data migration in either
direction.

## Open Questions

- Whether identity should later be threaded into `Provenance` (`contract/provenance.py`'s
  `agent` field, currently the fixed literal `"bloom_agent"`) once a caller identity can
  meaningfully reach that layer — explicitly deferred, not decided here (see proposal.md Scope,
  "Per-tool attribution").
- Ordering with #265 (hard-fail on missing `BLOOMMCP_API_KEY`) — no ordering dependency for this
  change to implement or merge, but production defense-in-depth wants both.
