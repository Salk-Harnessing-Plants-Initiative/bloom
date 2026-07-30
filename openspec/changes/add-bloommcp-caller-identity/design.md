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

**This document has now been revised three times.** The first revision, after a 5-agent
adversarial review of the initial proposal, found one load-bearing citation (an "existing
`call_rpc()` seam") was fabricated, and rewrote Decision 4 to attribute usage per-tool-call
instead of per-section, based on a claim (FastMCP's own context-propagation mechanism is
reliable) that later turned out to be true in general but not sufficient for the specific
reused-session case this feature needs. The second revision — a review of the actual PR
implementation, not just the proposal — found that gap and reverted Decision 4 to its original,
section-level design, this time with the real mechanism confirmed correct rather than assumed.
Two other real issues from that same review were also fixed then: a UUID regex anchor that let a
trailing newline slip through (Decision 1), and usage recording running synchronously on every
tool call rather than off the request's critical path (Decision 4). The third revision — a
re-review of that fix — found the middleware-level recording it introduced fired before FastMCP's
own bearer-auth check could reject an unauthenticated request, and that the fire-and-forget
submission had no bound; both are fixed in Decision 4's fourth stage below. Where a decision below
changed as a result of any of these reviews, that's noted
explicitly rather than silently rewritten.

**Root cause of the fabricated citation** (worth recording so it isn't repeated): the first
draft's author read `bloommcp/src/bloom_mcp/supabase_client.py` *before* creating this change's
branch — while the shared working tree still had a different, unrelated feature branch checked
out (`egao28/bloommcp-supabase-reader-db-tier2-551`, issue #551, PR #557, still open and unmerged
today). That branch's own commits add a `call_rpc()` function to that exact file as part of a
DB-direct-query rewrite. After switching the working tree to this change's actual base
(`origin/staging`, which does not have that function), the file's content silently changed
underneath the citations already gathered, and they were never re-verified post-switch. Lesson
applied going forward in this proposal: **verify file contents against the actual target branch,
after any `git checkout`, not before it.**

## Goals / Non-Goals

- **Goals:** verify an optional `X-Bloom-Identity` header using the same HS256/`JWT_SECRET`
  mechanism `langchain-agent` already uses for its own session verification; never let that
  identity carry DB/Storage authority; record usage (identity, first/last seen, count, last
  action) including an anonymous bucket for callers with no header.
- **Non-Goals:** OIDC/JWKS verification; asymmetric signing keys; making any real client send
  the header; per-user upload namespacing (#388); an append-only usage history (this delivers a
  rolling aggregate, matching the issue's own AC shape — see Risks); changing how bloommcp
  authenticates to Postgres/Storage (`bloom_agent` stays exactly as-is).

## Decisions

### Decision 1 — Verification mirrors `langchain/deps.py:get_current_user()`, plus a `sub`-shape guard

`bloom_mcp.identity` uses PyJWT (already a transitive dependency via `supabase-py`'s
`supabase_auth` sub-dependency, pinned `2.13.0` in `bloommcp/uv.lock` — confirmed directly, not
assumed; promoted to a direct dependency by this change rather than added fresh),
`algorithms=["HS256"]`, `audience="authenticated"`, extracts `payload["sub"]`. This is a
deliberate 1:1 mirror of the decode/except block at
[deps.py:38-52](../../../langchain/deps.py#L38-L52), not an independent design, because the
2026-07-29 resolution comment explicitly says the near-term path "should mirror that precedent
exactly, not introduce JWKS verification that doesn't match today's signing setup."

**On the `aud == "authenticated"` check specifically:** the resolution comment's Q1 table
describes this check, for the near-term profile, as *"confirm `aud == "authenticated"` (sanity
check, not a rejection rule)"* — language that reads as advisory. In practice it cannot be
advisory-only while also "mirroring `deps.py` exactly": `deps.py`'s single `jwt.decode(...,
audience="authenticated")` call has PyJWT raise `InvalidAudienceError` (caught by the broad
`except jwt.InvalidTokenError`) on any mismatch — there is no advisory-only mode for that kwarg
within one `decode()` call, and `deps.py` itself would reject a wrong-audience token the same
way. So this proposal's behavior (audience mismatch → rejected) is what literal mirroring
actually produces, and is consistent with `deps.py`'s own real behavior; the resolution comment's
"sanity check" phrasing is a description of *intent* (why the check exists for this profile) more
than a distinct *mechanism* from the OIDC profile's rejection rule. Documented here rather than
left as a silent inconsistency between the resolution comment's wording and this proposal's
scenarios.

**Additional guard beyond `deps.py`, new to this proposal:** the resolved `sub` must match a
standard UUID shape (Supabase issues UUIDv4 user ids) and must not case-insensitively equal the
reserved literal `anonymous`. Either failure is treated as an invalid token (`401`), not silently
downgraded. This exists because `bloommcp_usage.identity` uses the literal string `'anonymous'`
as its no-header sentinel (Decision 7) — without this guard, a validly-signed token whose issuer
allowed an attacker-influenced `sub` value could collide with that sentinel and pollute or mask
the aggregate anonymous-usage counter. `langchain/deps.py` has no equivalent guard because it
only ever returns `sub` for authorization decisions scoped to that one user, not for insertion
into a table keyed by a value with its own reserved sentinel.

The shape check uses `re.fullmatch`, not a `$`-anchored `.match()` — a review of the implementation
found the original `$`-anchored pattern would let a `sub` ending in a trailing `\n` slip through
(`$` matches immediately before a trailing newline as well as at the true end of string in Python's
`re` module; `\Z`/`fullmatch` do not). The resolved `sub` is also lowercased before being returned,
so a non-lowercase UUID from any future issuer can't fragment into a second `bloommcp_usage` row
for the same identity.

- **Alternative considered:** the original issue's JWKS-based verification against
  `/auth/v1/.well-known/jwks.json` (reusing `/api/client-info` for discovery, per the issue's
  final comment, 2026-07-30). Rejected for *this* change — this deployment signs symmetrically
  today, so there is no real per-project JWKS to verify against; that comment's discovery
  mechanism remains useful whenever the OIDC stretch item is picked up as its own change.

### Decision 2 — A present-but-invalid token is rejected (401), not downgraded to anonymous

The issue's re-scoped AC reads: "invalid/expired tokens are rejected" as one clause and "an
absent header falls back to anonymous" as a separate one. This proposal treats those as two
distinct outcomes, not one "best-effort" fallback. "Invalid" now includes the `sub`-shape guard
from Decision 1.

- **Alternative considered:** treat any unverifiable header (absent or invalid) as anonymous —
  simpler, and would still satisfy "no regression to today's behavior" for the absent-header
  case. Rejected: a caller that positively asserts an identity that fails verification (expired,
  wrong audience, bad signature, malformed `sub`) is a materially different, more suspicious
  signal than a caller that asserts nothing — silently absorbing it as anonymous would mask both
  legitimate client-side bugs (clock skew, stale cached token) and adversarial probing, with no
  operator visibility either way. Fail-closed on a positive-but-broken assertion matches this
  codebase's existing precedent (#265, hard-fail on missing `BLOOMMCP_API_KEY` rather than
  silently dropping to dev mode).
- **Consequence:** this rejection happens in the new outer middleware, *before* FastMCP's own
  `BLOOMMCP_API_KEY` bearer check runs (the middleware wraps the whole `Starlette` app; FastMCP's
  `TokenVerifier` executes only once a request is routed into one of the mounted `Mount` sub-apps
  — see Decision 3). A request can therefore be rejected for a bad identity header even before
  its bearer token is checked. This is intentional (both are independent, either failing is
  sufficient reason to reject) and has no current-production impact since no client sends the
  header yet (see proposal.md Scope). Pinned down as its own spec requirement (see spec.md,
  "Transport-Level Bearer Auth Is Unaffected") rather than left implicit, per review feedback.

### Decision 3 — One raw-ASGI middleware on the app `build_app()` already returns, not per-section wiring, and not `BaseHTTPMiddleware`

`build_app()` ([server.py:80-100](../../../bloommcp/src/bloom_mcp/server.py#L80-L100)) already
composes the combined surface and every per-section sub-app into a **single** `Starlette`
instance via `Mount`. Starlette dispatches `Mount` as a route match inside the router; middleware
sits outside the router in the ASGI call chain. So one middleware added to this single app (via
the `middleware=[...]` argument to its `Starlette(...)` constructor) sees every request
regardless of which `Mount` it eventually resolves to — combined `/mcp`, every `/<section>/mcp`,
and `/health` alike — with no per-section changes to `bloom_mcp/sections/*/__init__.py`.

**Implementation style, revised after review:** the middleware MUST be a raw ASGI middleware
class (`async def __call__(self, scope, receive, send)`), not `Starlette.BaseHTTPMiddleware`.
This matches FastMCP's own precedent for this exact transport: FastMCP inserts its own
`RequestContextMiddleware` as a raw ASGI class, always as the outermost middleware of every
FastMCP-built sub-app (confirmed directly in the installed package: `fastmcp/server/http.py`
defines `class RequestContextMiddleware` around line 89 and does
`middleware.insert(0, Middleware(RequestContextMiddleware))` around line 132). `BaseHTTPMiddleware`'s
`dispatch(request, call_next)` pattern has a well-documented history in the Starlette ecosystem
of buffering responses and losing client-disconnect propagation for long-lived streaming
responses — a real risk here since this middleware sits in front of the same persistent
`streamable-http`/SSE session FastMCP took its own care to protect. A dedicated test drives a
request through a held-open streaming session (not just ordinary request/response pairs) to
confirm the middleware doesn't buffer or break it.

- **No conflict with `BLOOMMCP_API_KEY`:** that check is wired per-`FastMCP`-instance via
  `auth=auth_provider` ([auth.py](../../../bloommcp/src/bloom_mcp/auth.py), shared by
  `server.py:61` and all three section constructors) and reads the `Authorization` header, a
  different header than `X-Bloom-Identity`. The new middleware runs first in the ASGI chain
  (outer), FastMCP's bearer check runs second (inner, inside whichever `Mount` the request
  routes to, itself sitting outside FastMCP's own `RequestContextMiddleware` for that sub-app) —
  see Decision 2's consequence for the one behavioral interaction between the two. Verified live
  (not just by code inspection, per an earlier review's own admission that this was untested): a
  subprocess test with `BLOOMMCP_API_KEY` actually set confirms a valid identity header does not
  bypass FastMCP's bearer check, and an invalid identity header is rejected before that check is
  ever reached, regardless of bearer-token validity.
- **Duplicate `X-Bloom-Identity` headers are rejected, not silently resolved to one occurrence.**
  ASGI's `` scope["headers"] `` is a list of `(name, value)` pairs, and a naive
  `dict(scope["headers"])` construction would silently keep only the *last* occurrence of a
  repeated header. If a caller (or a future trusted-proxy-injected-header architecture) ever sends
  `X-Bloom-Identity` twice, this middleware rejects the request (`401`) rather than picking one
  silently — not exploitable today (no proxy injects this header yet), but cheap to close now
  rather than leave as a latent footgun for whenever one is added.

### Decision 4 — Usage is attributed at request/mounted-surface granularity, recorded by the middleware itself, non-blocking (reverted and corrected after a second, deeper review)

**This decision has now been revised twice.** The full history is kept here rather than
compressed away, because the second revision corrects a real bug the first revision introduced —
worth remaining visible so it isn't repeated.

**Draft 1 (original):** attribute usage to the mounted section/path (`core`, `sleap_roots`,
etc.), recorded directly by the identity middleware, on the stated grounds that "whether a
`ContextVar` set in the outer Starlette middleware is reliably visible inside FastMCP's
tool-dispatch code path... [is] unverified from this repo."

**Draft 2 (first review's revision, now known to be wrong):** that review found the "unverified"
premise directly checkable and false — FastMCP itself exposes `get_http_headers()` /
`get_http_request()`, backed by a `ContextVar` set by its own `RequestContextMiddleware`, for
exactly this purpose. Draft 2 concluded from this that a bloommcp-owned `ContextVar`, set by the
identity middleware and read inside `register()`'s per-tool wrapper, would reliably carry
identity into tool-dispatch code — enabling real per-tool attribution instead of coarser
section-level attribution.

**Why draft 2 was wrong:** a second, deeper review (of the actual implementation, not just the
proposal) traced FastMCP's `StreamableHTTPSessionManager` (`mcp/server/streamable_http_manager.py`
in the installed `mcp` package) directly and found that for a **reused** `streamable-http`
session — the common case: one client session spans many tool calls, exactly how
`fastmcp.Client`'s `async with` block and any real chat-agent client work — the actual
tool-dispatch loop (`self.app.run(...)`) runs inside a single long-lived task, started **once**,
at session creation (`_handle_stateful_request`, `self._task_group.start(run_server)`). A *later*
HTTP request in that same session does not re-enter that task at all; it only pushes a message
into a stream the already-running task reads from
(`await transport.handle_request(scope, receive, send)`, the existing-session branch). Python's
`asyncio`/`anyio` task creation snapshots `contextvars.Context` at spawn time — a `ContextVar.set()`
call made later, in a *different* request's own (separate) task, cannot reach a task whose context
was already snapshotted at session creation. This was confirmed independently, not just asserted:
by reading `streamable_http_manager.py`'s actual control flow, and by the well-established,
documented semantics of `asyncio`/`anyio` task context snapshotting (not something that needed an
empirical repro to settle). It equally invalidates draft 2's justification: FastMCP's own
`get_http_headers()`/`get_http_request()` rely on the identical mechanism
(`RequestContextMiddleware`, set fresh per incoming HTTP request) and inherit the exact same
limitation for a reused session — confirmed further by reading `mcp.shared.message.SessionMessage`
(the wrapper carried through the transport's internal read/write streams), whose `metadata` field
carries only a `related_request_id`, no HTTP header information a per-message re-set inside the
persistent task could use to reconstruct "the current request" some other way. So there is no
available mechanism — not FastMCP's, not a bloommcp-owned one built the same way — that correctly
carries a *per-request* value into that persistent dispatch task for a session spanning more than
one tool call.

**Net effect the bug would have had, had it shipped:** for a real multi-call session, only the
*first* request's resolved identity would ever be visible to `register()`'s wrapper — every
subsequent tool call in that session would be silently attributed to whatever identity was live
at session creation (frequently anonymous, if the very first call in a session happened to omit
the header), not the caller each individual request actually named. Wrong, not crashing —
exactly the kind of defect a plain test wouldn't catch: the contract test for this
(`test_register_usage.py`, now deleted) used FastMCP's in-memory `Client` transport, which
bypasses `IdentityMiddleware`/`build_app()` entirely, and no test drove two tool calls through one
real session.

**Final decision (this revision):** revert to draft 1's design almost exactly — usage is recorded
by `IdentityMiddleware` itself, keyed on the mounted surface the request resolved to (via
`_action_from_path`, matching against `bloom_mcp.sections.SECTIONS`'s keys, or `"combined"` for
the root surface), not the specific MCP tool name. This sidesteps the whole problem structurally:
the middleware always sees the *current* request correctly, with no dependency on session reuse
or task/context propagation, because it runs once per incoming HTTP request regardless of which
task eventually consumes that request's message. `contract/wrap.py`'s `register()` and
`as_mcp_tool` are both reverted to their pre-this-change form — no ContextVar, no per-tool
wrapper, no coupling between Tier 1's contract layer and identity/usage at all.

**Also fixed in this revision — non-blocking recording:** the middleware is a genuine `async def
__call__`, unlike a per-tool wrapper around a synchronous tool function (which cannot `await`
anything). Recording now happens via `bloom_mcp.usage.record_usage_async`, which submits the
blocking `call_rpc()` round-trip to a small dedicated `ThreadPoolExecutor` and returns immediately
— the request/response cycle it's attributed to is never delayed by the DB write. This also
resolves a related review finding independent of the ContextVar bug: the very first version of
this recording design ran the DB round-trip synchronously in a `finally` block around every tool
call, adding undisclosed latency (and a failure-coupling risk) to *every* current tool call today,
not just future/inert traffic — despite the "inert in production" framing elsewhere in this
proposal, which described the *feature*, not this specific cost.

- **Consequence for the `/health` endpoint:** now that recording happens per-*request*, not
  per-tool-call, `/health` is excluded explicitly (`if path != "/health"`) rather than
  structurally — the earlier per-tool design got this for free, coincidentally, as a side effect
  of the (buggy) mechanism it used; this revision states it directly instead.
- **Alternative considered (draft 2, superseded — see above):** per-tool attribution via a
  `register()` wrapper + ContextVar. Rejected: confirmed broken for a reused session, which is
  the common real-world case, not an edge case.
- **Alternative considered:** parse the incoming JSON-RPC request body in the middleware (peeking
  at the `"method"`/tool name being called) to get per-tool granularity without relying on
  cross-task propagation at all. Rejected for this change — correctly buffering and replaying an
  ASGI request body from middleware (so the downstream app can still read it) is a real,
  non-trivial pattern in its own right, and the added risk didn't seem proportionate to fix in the
  same pass as the ContextVar bug. Left as a real option for a future change that specifically
  wants per-tool granularity back, now that the cross-task limitation is well understood.
- **Alternative considered:** switch bloommcp's `FastMCP`/`http_app()` instances to
  `stateless_http=True`, which creates a fresh transport and dispatch task per request
  (`_handle_stateless_request` in the same installed-package file) — this would make the original
  ContextVar design correct, since task creation and context capture would happen fresh on every
  request. Rejected as disproportionate scope for this fix: it changes MCP session behavior for
  the *entire* server (every tool, every client), not just usage tracking, and needs its own
  review of what (if anything) currently depends on session reuse — not something to change as a
  side effect of an identity/usage-tracking feature.

**Fourth stage (this revision) — recording is gated on the downstream response, and bounded.** A
review of the reverted-to-middleware-level implementation (not just the design) found two further
issues, both fixed here:

- **Recording fired unconditionally, before the wrapped app even ran** — meaning it fired for
  *every* HTTP request reaching bloommcp, including one with no valid `BLOOMMCP_API_KEY` and no
  `X-Bloom-Identity` header, which FastMCP's own bearer check (inside the wrapped app) would go on
  to reject with `401` anyway. Confirmed reachable: nothing about this middleware's own logic
  requires authentication first, and it is wired in regardless of whether any real client sends
  the identity header yet. Fixed by wrapping `send` to capture the downstream response's status
  and moving the `record_usage_async` call to *after* `await self.app(...)` returns, firing only
  when that status is not `401` — an unauthenticated hit still gets rejected exactly as before,
  but is no longer recorded. (A downstream status other than `401` — e.g. a `4xx`/`5xx` from the
  tool/protocol layer itself, unrelated to auth — still records; only `401` specifically is
  treated as "this caller was never authenticated to begin with.")
- **The fire-and-forget submission had no bound.** `ThreadPoolExecutor`'s own work queue is
  unbounded, so — especially now that unauthenticated traffic could reach it at all before the fix
  above — a burst of requests could queue arbitrarily many recording calls, all eventually
  serializing on the same hot `bloommcp_usage` row (most commonly the `anonymous` sentinel, or any
  single popular identity). Fixed with a `threading.Semaphore(_MAX_INFLIGHT=64)` gate:
  `record_usage_async` acquires non-blocking before submitting, and drops (logs, does not queue or
  block) if the cap is already reached. Usage tracking is observability, not a delivery guarantee,
  so dropping under sustained overload is the right failure mode — blocking the caller or growing
  an unbounded queue would not be.

### Decision 5 — `JWT_SECRET` is validated lazily, only when a request actually carries the header

Mirrors the existing convention: `bloom_mcp.supabase_client._require_env()`
([supabase_client.py:46-68](../../../bloommcp/src/bloom_mcp/supabase_client.py#L46-L68)) defers
validation to call time so `import bloom_mcp` and the fakes-based unit tests run with no runtime
env at all. `JWT_SECRET` follows the same pattern: unset in an environment that never receives
an `X-Bloom-Identity` header is a no-op (today, that's every environment — see proposal.md
Scope). If a request *does* carry the header and `JWT_SECRET` is unset, that is a server
misconfiguration, not a client error: the middleware returns a `500`-class response naming the
missing variable, rather than either crashing the process or silently treating the caller as
anonymous (which would hide a real deploy defect behind what looks like ordinary anonymous
traffic).

**Note the deliberate divergence from `deps.py` here:** `langchain/deps.py:21-23` requires
`JWT_SECRET` unconditionally at **import** time, hard-failing the process if unset. This proposal
does the opposite for bloommcp — lazy, request-time-only validation. Decision 1's "mirrors
`deps.py` exactly" framing applies to the JWT *decode* logic only; this decision deliberately
follows bloommcp's own `supabase_client.py` convention instead for the env-var-requiredness
question, not `deps.py`'s. The two services will end up enforcing the same secret with opposite
fail-fast postures, which is intentional, not an oversight.

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

`bloommcp_usage.identity` is the table's primary key, so it cannot be `NULL`. Every tool call with
no `X-Bloom-Identity` header (all current production traffic — see proposal.md Scope) upserts
against the literal string `'anonymous'`, accumulating `request_count` and `last_seen` across all
unauthenticated callers combined. Decision 1's `sub`-shape guard prevents a verified-but-malicious
token from ever supplying `sub = "anonymous"` itself, so this sentinel cannot collide with a real
identity.

- **Alternative considered:** key anonymous usage by caller IP or MCP session id. Rejected — no
  session concept exists at this transport layer today, IP-based tracking raises its own
  privacy/retention questions this issue never asked for, and the issue's own wording ("anonymous
  entries when no identity header is present") reads as a single aggregate bucket, not
  per-anonymous-caller tracking.

### Decision 8 — `call_rpc()` is new code introduced by this change, not a reuse (correcting a fabricated citation)

The first draft of this design claimed bloommcp already had a `call_rpc()` function at
`supabase_client.py:134-150` and that this change would "reuse" it. That was wrong — see Context
above for the root cause. bloommcp's actual current surface (on `staging`, this change's real
base) is exactly two public functions, `get_postgrest_client()` and `read_input_csv()`; there is
no RPC-calling code anywhere in the file today.

This change adds `call_rpc(function_name: str, params: dict) -> list[dict]` to
`supabase_client.py` as new code, following `get_postgrest_client()`'s existing per-call-fresh-
client convention (`client = get_postgrest_client(); return client.rpc(function_name,
params).execute().data`).

**Disclosed coordination note:** a separate, currently-open, unmerged PR — #557, implementing
issue #551 (`egao28/bloommcp-supabase-reader-db-tier2-551`, "rewrite SupabaseReader's raw tier to
query Postgres directly") — independently adds a function with the **same name and signature**
to the same file, for an unrelated purpose (reading experiment/trait data). If both changes land
independently, whichever merges to `staging` second will hit a trivial git conflict on a
duplicate function definition — mechanically easy to resolve (keep one copy; the two
implementations are expected to be identical or near-identical in shape), but a real, disclosed
merge-order risk rather than a silent one, in the same spirit as the `add-bloommcp-local-root` /
`add-bloommcp-local-experiment-reader` archive-ordering precedent (Decision 7 in that change's
design.md). Not treated as a hard dependency — this change does not require #551/PR #557 to merge
first, and should not block on an unrelated, unreviewed PR.

- **Alternative considered:** take a hard dependency on PR #557 merging first, so this change can
  genuinely reuse an already-existing `call_rpc()`. Rejected — it would make this change's
  mergeability hostage to an unrelated PR's review timeline for no benefit proportional to that
  coupling; the duplicate-definition conflict this creates instead is small and disclosed.

### Testing strategy for the new RPC call (new — review found the original task list under-specified this)

`call_rpc()` and `record_bloommcp_usage` need two different kinds of test, at two different
tiers, matching an existing split already present in this repo (not invented for this change):

- **Fast unit tier** (`bloommcp/tests/`, runs in every PR via `pytest tests/ -m "not
  integration"`): a new fake-RPC test double, mirroring the existing `fake_supabase_storage`
  fixture's shape (which monkeypatches the six storage-helper module-level names in
  `supabase_client.py`) but for `call_rpc` — records calls in-memory, returns a caller-supplied
  fixture response, never touches a network or real Postgres. This is what the middleware's own
  recording tests (write-failure-non-fatal, per-identity upsert bookkeeping at the Python-call
  level) run against; `record_usage_async`'s own tests (background-thread submission,
  non-blocking) additionally use a `threading.Event` to deterministically wait for the background
  call rather than racing it, since that function's whole point is to run off the calling thread.
- **Real-Postgres tier**, for genuine upsert/concurrency semantics (`ON CONFLICT` behavior under
  two simultaneous first-time requests from the same new identity): placed under the repo's
  **existing** root-level `tests/integration/` (not `bloommcp/tests/` with
  `@pytest.mark.integration` — that marker means something different inside bloommcp's own
  `pyproject.toml`, "full-fixture statsmodels/umap oracle tests over turface_19," and is excluded
  from every automated CI job). The root-level `tests/integration/` convention already has a
  `pg_conn` fixture (real local Postgres, `supabase_admin`/BYPASSRLS) wired into the
  `compose-health-check` CI job for exactly this kind of DB-correctness test (precedent:
  `test_cyl_writeback_rpc.py`). This proposal's Postgres-level test follows that existing
  convention rather than inventing a new one.

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

`identity` stays `TEXT` rather than Postgres `UUID` because it must also hold the non-UUID
sentinel `'anonymous'`; the column's integrity instead relies on the application-layer guard
(Decision 1) — every value that reaches this table has already been verified to be either a
UUID-shaped `sub` or the literal `anonymous`, never arbitrary attacker-controlled text. The
upsert is a single Postgres function, `record_bloommcp_usage(p_identity text, p_action text)`,
called through the new `call_rpc()` helper (Decision 8):

```sql
INSERT INTO bloommcp_usage (identity, last_action)
  VALUES (p_identity, p_action)
  ON CONFLICT (identity) DO UPDATE SET
    last_seen = now(),
    request_count = bloommcp_usage.request_count + 1,
    last_action = EXCLUDED.last_action;
```

`params` on a PostgREST `.rpc()` call is sent as a JSON body PostgREST binds as function
arguments, not string-interpolated SQL, and this function body contains no dynamic SQL
(`EXECUTE format(...)`) — so an attacker-influenced `p_identity`/`p_action` string is not a SQL
injection vector regardless of content. `INSERT ... ON CONFLICT (identity) DO UPDATE` (no
filtering `WHERE` on the conflict target) is Postgres's standard atomic-upsert idiom: under two
concurrent first-time requests for the same new identity, the unique index serializes the two
INSERTs, the loser hits the conflict and applies the unconditional `DO UPDATE`, correctly landing
at `request_count = 2` with one row — no lost update, no duplicate-key error, under Postgres's
default READ COMMITTED isolation.

The exact migration filename/timestamp and matching rollback are implementation (tasks.md), not
fixed by this design doc.

## Risks / Trade-offs

- **`bloommcp_usage` is a rolling aggregate, not an append-only log.** Once any identity makes a
  second (different) tool call, the record of the first is gone — only the count and the latest
  action survive. This means the table can answer "what has identity X most recently done, and
  how often," but never "what did identity X do over time," even in the best case (valid header,
  the still-unfiled langchain-agent sibling shipped). This is a real, disclosed limitation, not an
  oversight: the issue's own acceptance-criteria shape (`identity, first/last seen, request
  count, last action`) literally describes this aggregate, not a log, so building an append-only
  log here would be scope beyond what was asked. If full historical audit logging is later
  needed (e.g. for compliance), that is a separate, larger change.
- **No retention policy.** The table's size is bounded by the number of distinct callers
  (identities + the one anonymous sentinel), not by request volume, so unbounded growth isn't a
  concern — but there is also no TTL/purge for a table that, once the langchain-agent sibling
  ships, links real researcher identities to activity indefinitely. Accepted as proportionate for
  this change's scope; revisit if a compliance/data-minimization requirement emerges.
- **Section/mounted-surface granularity, not per-tool.** Decision 4's history (draft 1 → draft 2 →
  this final revision) means this proposal ended up back where it started, after a detour that
  turned out to rest on a mechanism (cross-task `ContextVar` propagation into a reused session's
  persistent dispatch task) that doesn't actually work. `last_action` answers "who hit the
  `sleap_roots` surface, and how often" — not "who ran `qc_clean` specifically." Real per-tool
  attribution remains possible for a future change (via request-body parsing in the middleware,
  or by switching to `stateless_http=True` — see Decision 4's alternatives), just not attempted
  here given the added risk of either approach.
- **The "never grants DB/Storage authority" invariant is enforced by construction and tested by
  regression, not by a type-level guarantee.** `get_postgrest_client()` takes zero parameters
  today and no identity is threaded into it; the regression test (tasks.md) iterates every
  currently-registered tool/section, not one representative sample, to make this coverage real
  rather than a sampling gap. A future change (e.g., a careless #388 namespacing implementation)
  could still thread identity into a query or Storage path without violating this spec's literal
  text — that would be a new, separate regression to catch at that time.
- **`request_count`/`last_action` count raw qualifying HTTP requests, not MCP tool invocations —
  disclosed here so a future reader of `bloommcp_usage` doesn't misinterpret the numbers once real
  identities start flowing through.** Since recording now happens in the middleware itself (per
  Decision 4's final revision), it fires for every HTTP request the middleware sees and doesn't
  reject — including MCP protocol-level messages (`initialize`, `notifications/initialized`, and
  similar handshake/keepalive traffic) that carry no tool call at all. This means `request_count`
  is inflated above the caller's actual number of tool invocations, and `last_action` can reflect
  a protocol message rather than the caller's most recent real tool use. This is the direct,
  accepted cost of Decision 4's fix (attributing at request granularity, since that's the only
  granularity provably free of the ContextVar/session bug) — not something a future change should
  "fix" by trying to filter which HTTP requests count without re-introducing tool-call-level
  awareness (and its cross-task problems) into this middleware.
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

Purely additive: two new modules (`identity.py`, `usage.py`), a new middleware wired into
`build_app()`, one new table, one new env var read only when the (currently never-sent) header is
present. `contract/wrap.py` (`as_mcp_tool`, `register()`) is untouched — Decision 4's reverted
per-tool design would have modified it, but the final design doesn't. No existing behavior changes
for any current deployment. Rollback is reverting the code change and running the migration's
paired rollback SQL (removing `bloommcp_usage`) — note this destroys any usage data accumulated by
then; if the langchain-agent sibling has shipped and real usage has accumulated, consider a
`pg_dump` of the table before rolling back.

## Open Questions

- Whether identity should later be threaded into `Provenance` (`contract/provenance.py`'s
  `agent` field, currently the fixed literal `"bloom_agent"`) once a caller identity can
  meaningfully reach that layer — explicitly deferred, not decided here (see proposal.md Scope).
- Ordering with #265 (hard-fail on missing `BLOOMMCP_API_KEY`) — no ordering dependency for this
  change to implement or merge, but production defense-in-depth wants both.
- Ordering with PR #557 (#551) re: the duplicate `call_rpc()` definition — see Decision 8. Not
  blocking, but whoever merges second should expect a small conflict.
