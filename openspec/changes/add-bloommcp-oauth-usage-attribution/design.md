## Context

**Package versions this design's citations are pinned against**: `mcp==1.28.1`,
`fastmcp==3.2.3` (per `bloommcp/uv.lock`, installed at
`bloommcp/.venv/lib/python3.11/site-packages/{mcp,fastmcp}/`). Every `file:line` citation into
those two packages below was read directly from that installed source, at those versions, not
assumed or carried over from documentation — but line numbers will drift on the next
`uv lock` bump; re-verify against the then-current `uv.lock` before trusting them, rather than
treating this document as self-updating. (`add-bloommcp-caller-identity`'s own design.md Context
section documents a prior citation-fabrication incident in this codebase caused by exactly this
class of unpinned/unrevalidated assumption — the pinning here exists to make that failure mode
checkable, not just avoided this once.)

PR #613 adds a second credential type bloommcp accepts: a Supabase OAuth access token, verified by
`bloom_mcp.auth.SupabaseOAuthVerifier`
([auth.py:70-144](../../../bloommcp/src/bloom_mcp/auth.py#L70-L144)), which sets
`AccessToken.subject = sub.lower()` ([auth.py:142](../../../bloommcp/src/bloom_mcp/auth.py#L142))
— the caller's own, already-validated (`is_valid_identity`), lowercased Supabase user id. PR #613's
own description names the gap this change closes: bloommcp knows who the OAuth caller is, but
`bloommcp_usage` still records `anonymous` for them, because
`IdentityMiddleware` ([identity.py](../../../bloommcp/src/bloom_mcp/identity.py)) only ever reads
the `X-Bloom-Identity` header.

This is the second time this codebase has had to answer "can a value produced by verifying a
caller's credential reach `IdentityMiddleware`'s usage-recording code correctly, for every request,
including the Nth request in a reused `streamable-http` session?" The first time
(`add-bloommcp-caller-identity` design.md Decision 4) the answer was **no** for the mechanism
attempted then (a bloommcp-owned `ContextVar` set by the middleware, read inside a per-tool
wrapper) — confirmed by tracing FastMCP's `StreamableHTTPSessionManager` directly and finding the
actual tool-dispatch loop runs in one task spawned once, at session creation; a later request's own
task cannot set a `ContextVar` that task will ever see. This design exists to answer the same
question rigorously for a *different* mechanism (reading `scope["user"]`, not a `ContextVar`),
rather than assume the earlier "no" transfers, or assume a superficially-similar "yes" without
re-deriving it.

**Traceability note, disclosed rather than resolved here:** issue #406 (which produced
`add-bloommcp-caller-identity`) originally specified verifying an OIDC **ID token** and explicitly
rejecting an **access token** presented as identity — even a validly-signed one — precisely because
both are signed by the same Supabase keys and an access token alone doesn't carry the same
audience guarantee. PR #613 instead authenticates external MCP clients via the OAuth **access
token** itself (gated on a `client_id` claim, not that `aud` rule), and this change's
`AccessToken.subject` is read from that same access token. Neither PR #613 nor this proposal
revisits or reconciles #406's original rejection rule; the fact that stayed true throughout is
narrower than what #406 asked for — the caller identity here is used *only* for a usage-tracking
label, never as authorization or DB/Storage authority (unaffected by which token type supplied it,
per the invariant this change doesn't touch). Whether #406's stricter ID-token-only rule should
still apply to authorization decisions bloommcp doesn't make today is a question for whoever
revisits external-MCP-client auth's authorization model, not this change. (Parent tracking: issue
#554, "Epic: bloommcp hosted-server UX," maps #406 under its "Connect" section with an explicit
note that this line of work is "audit/attribution only, not access scoping" — consistent with how
this change uses the access token.)

## Goals / Non-Goals

- **Goals:** attribute `bloommcp_usage` recording to an OAuth caller's verified `subject` when no
  `X-Bloom-Identity` header resolved one; prove the mechanism is safe for a reused
  `streamable-http` session (not just a fresh one); leave every other part of
  `add-bloommcp-caller-identity`'s design (granularity, non-blocking recording, 401 gating, schema)
  untouched.
- **Non-Goals:** per-tool attribution; fixing the 404/session-hijack gating gap (see Risks); any
  change to `bloom_mcp.auth` itself (this change only *reads* what it already produces).

## Decisions

### Decision 1 — Read `scope["user"].access_token.subject` after `await self.app(...)` returns, not a `ContextVar`

**Where FastMCP's bearer-auth actually runs, confirmed by reading the installed package, not
assumed:** `mcp.server.auth.middleware.bearer_auth.BearerAuthBackend.authenticate()`
(`.venv/lib/python3.11/site-packages/mcp/server/auth/middleware/bearer_auth.py:54-73`) reads the
`Authorization` header and calls `self.token_verifier.verify_token(token)` — i.e.
`bloom_mcp.auth.ApiKeyVerifier`/`SupabaseOAuthVerifier.verify_token()` — every time it runs. It is
wired as `starlette.middleware.authentication.AuthenticationMiddleware`
(`fastmcp/server/auth/auth.py:318-330`, `AuthProvider.get_middleware()`), an ordinary ASGI
middleware Starlette invokes on **every** incoming `http` scope — there is no session-level
caching of this check. On success it sets `scope["user"] = AuthenticatedUser(auth_info)` (Starlette
convention; `AuthenticatedUser` defined at `bearer_auth.py:13`, `self.access_token = auth_info` at
line 18) and `scope["auth"]`. `RequireAuthMiddleware.__call__` (`bearer_auth.py:102-104`) reads
`scope.get("user")` directly and does `isinstance(auth_user, AuthenticatedUser)` to decide
401-vs-proceed — the same check this change's own helper mirrors (see below), a pattern already
proven correct by the SDK's own use of it.

`server.build_app()` ([server.py:82-113](../../../bloommcp/src/bloom_mcp/server.py#L82-L113))
wraps every `Mount` — the combined app and each per-section sub-app, each carrying this same
auth-middleware stack — in a single outer `Starlette(..., middleware=[Middleware(IdentityMiddleware)])`.
So `IdentityMiddleware` holds the *same* `scope` dict, by reference, through its own one
`await self.app(scope, receive, send)` call: that call's nested chain (routing → the Mount's own
`RequestContextMiddleware` → `AuthenticationMiddleware`, which mutates `scope["user"]` in place →
`AuthContextMiddleware` → inner routing → `RequireAuthMiddleware` → the streamable-http transport)
is one synchronous await-chain in **one task** — `IdentityMiddleware`'s own task, for this one
connection. **The connective link that makes this true across a `Mount` boundary, not just within
one app**: Starlette's `Router.__call__`/`app()` mutates the *same* `scope` object in place
(`starlette/routing.py`, `scope.update(child_scope)`) before calling `route.handle(scope, receive,
send)`, and `Mount.handle` passes that identical object straight into the mounted sub-app — the
*only* place Starlette's router copies `scope` at all is a `dict(scope)` made solely for a
trailing-slash-redirect check (`starlette/routing.py`, `redirect_scope = dict(scope)`), not the
normal dispatch path this request takes. So there is no hidden scope-copy between
`IdentityMiddleware`'s outer `Starlette` app and whichever `Mount` a request resolves to. After
`await self.app(...)` returns, `scope.get("user")` reflects exactly the credential presented on
*this* request.

**Why this is not the mechanism Decision 4 rejected:** Decision 4's finding was specifically about
a value crossing an *asyncio task boundary* — a `ContextVar.set()` in one task is invisible to a
*different*, already-running task, because `asyncio`/`anyio` snapshot `contextvars.Context` at task
*spawn* time. `scope` is not a `ContextVar`; it is one plain `dict` object passed by reference
through nested function/coroutine calls *within a single task*. Nothing about Decision 4's finding
says a mutation to an object already held by reference in the *current* task becomes invisible —
that would be true of ordinary Python objects generally, and isn't. The specific place where a
per-request value *does* have to cross a task boundary in this codebase — handing a JSON-RPC
message from the request-handling task into the long-lived per-session dispatch task — happens
**deeper** than where `IdentityMiddleware` reads `scope["user"]`: that handoff carries the message
as data over an `anyio` memory-object-stream
(`mcp/server/streamable_http.py:543,566,641`, `ServerMessageMetadata(request_context=request)`),
into a task spawned freshly per message
(`mcp/server/lowlevel/server.py:679-690`, `tg.start_soon(self._handle_message, ...)`) — a
*different* problem (getting a value *into* the dispatch task) than this change's problem (reading
a value *IdentityMiddleware's own task already wrote*, after its own nested call returns).
`IdentityMiddleware` never needs to observe anything from inside the dispatch task at all; it only
needs what `AuthenticationMiddleware` already wrote into *its own* `scope`, earlier in the *same*
call. Confirmed further, not merely inferred: FastMCP's own
`get_access_token()`/`get_http_request()` (`fastmcp/server/dependencies.py:646-690,750-814`) are
*not* usable from `IdentityMiddleware`'s position — not because of a cross-task problem, but
because they read a `ContextVar` (`_current_http_request`/`request_ctx`) that
`RequestContextMiddleware` explicitly resets in a `finally` (`fastmcp/server/http.py:80-111`,
`set_http_request`) before control returns up through `IdentityMiddleware`'s own
`await self.app(...)` — the *value*, not the read mechanism, is what's gone by then. This is a
second, independent reason those specific helper functions don't fit here (wrong lifetime for this
call position), distinct from — and not evidence for — Decision 4's task-boundary finding.
`scope["user"]` has no such reset; it lives for the ASGI call's whole duration.

**Reused-session safety, the crux of what Decision 4 needed and didn't have:**
`mcp/server/streamable_http_manager.py:217-262` (`_handle_stateful_request`) reads
`user = scope.get("user")` **on every request**, including the "existing session" branch (line
238) — proven by the SDK's own subsequent use of it: it compares
`authorization_context(user)` against the credential that created the session
(`self._session_owners.get(request_mcp_session_id)`, lines 240-256) and returns `404` on mismatch.
This could only work at all if `scope["user"]` is re-derived correctly for *every* request in a
reused session (including request #2, #3, ...), not frozen at session creation — which is exactly
what request-scoped `AuthenticationMiddleware` re-running on every request (above) guarantees.
There is no code path in the installed source where a later request in a reused session observes
an earlier request's stale `scope["user"]`.

- **Alternative considered:** use `fastmcp.server.dependencies.get_access_token()` directly.
  Rejected — wrong lifetime for `IdentityMiddleware`'s call position (see above); it is designed to
  be called *during* dispatch, inside the persistent session's own per-message task, not from an
  outer wrapper after the whole request has already returned.
- **Alternative considered:** reintroduce a bloommcp-owned `ContextVar`, set by
  `AuthenticationMiddleware`'s effect and read by `IdentityMiddleware`. Rejected as unnecessary —
  `scope` already carries the value with the right lifetime and no cross-task risk; adding a
  `ContextVar` would only reintroduce the exact class of risk Decision 4 warns about, for no gain.

### Decision 2 — Precedence: `X-Bloom-Identity` header wins over the OAuth `AccessToken.subject`

If both somehow resolve on the same request (not a real scenario today — no current client sends
`X-Bloom-Identity`, and OAuth callers connect directly with no intermediary that would also inject
it), the header wins. The header is bloommcp's own explicit, independently-verified assertion
mechanism (Decision 1/2 of `add-bloommcp-caller-identity`); the `AccessToken.subject` is a
by-product of *which credential authenticated the transport*, not a separate assertion. Preferring
the more specific, already-tested mechanism means every existing test and spec scenario for the
header path is untouched by this change — this change is purely additive to the *fallback* path.

- **Alternative considered:** OAuth subject wins over the header. Rejected — no concrete scenario
  motivates it today, and it would mean re-verifying every existing header-path test still holds
  once a second source can override it, for no present benefit.

### Decision 3 — Only a `SupabaseOAuthVerifier`-issued token supplies a usable subject

`ApiKeyVerifier.verify_token()` ([auth.py:64-67](../../../bloommcp/src/bloom_mcp/auth.py#L64-L67))
constructs `AccessToken(token=token, client_id="bloom-client", scopes=["tools"])` — no `subject`
kwarg, so it inherits `mcp.server.auth.provider.AccessToken.subject`'s field default of `None`
(confirmed directly: `class AccessToken(BaseModel):` at
`.venv/lib/python3.11/site-packages/mcp/server/auth/provider.py:39`, whose `subject` field at
line 45 reads `subject: str | None = None  # RFC 7662/9068 sub: resource owner...`).
`fastmcp.server.auth.AccessToken` ([confirmed]
`fastmcp/server/auth/auth.py:54`, `class AccessToken(_SDKAccessToken)`) is a bare subclass with no
new fields — the same default applies. So a shared-API-key-authenticated request's
`scope["user"].access_token.subject` is reliably `None`, and this change's helper naturally falls
through to `anonymous` for it — matching the shared key's existing, intentional semantics ("names
no individual", `auth.py` module docstring) with no extra branching required to preserve that.

**Invariant this decision depends on, stated explicitly for whoever adds a third `TokenVerifier`
later:** `_oauth_subject_from_scope` does no independent validation of whatever it finds in
`AccessToken.subject` — it trusts it completely. That's safe today only because both existing
verifiers uphold it by construction: `SupabaseOAuthVerifier` routes every `subject` it sets through
`is_valid_identity()` and lowercases it first (Context, `auth.py:142`), and `ApiKeyVerifier` never
sets one at all. A future `TokenVerifier` that populates `subject` with an unvalidated or
non-lowercased value would silently break `bloommcp_usage.identity`'s collision-safety guarantee
(Decision 1 of `add-bloommcp-caller-identity`) — through this fallback path, not through the
header path that guarantee was originally written for. Not enforced by a type or a runtime check
here (doing so would mean re-implementing `is_valid_identity`'s UUID-shape/reserved-sentinel guard
a second time, redundantly, against every verifier's output); recorded as a contract future
verifiers must uphold, not re-derive.

### Decision 4 — `_oauth_subject_from_scope(scope)` is a small, purely defensive helper; dev mode (no `auth`) is handled by absence, not a special case

`FastMCP`'s auth middleware is added only `if auth:` (`fastmcp/server/http.py:182,307`) — when
`bloom_mcp.auth.build_auth_provider()` returns `None` (dev mode, no `BLOOMMCP_API_KEY` and no OAuth
env configured — `auth.py:170-200`), no `AuthenticationMiddleware` runs at all, and `scope` never
gains a `"user"` key. The helper uses `scope.get("user")` (never `scope["user"]`) and
`getattr(..., "access_token", None)` / `getattr(..., "subject", None)` at each step, returning
`None` for every "nothing to find" shape (missing key, non-`AuthenticatedUser` value, `AccessToken`
with `subject=None`, or an falsy/empty string) rather than raising — mirroring
`verify_identity_header`'s own "absent means proceed as anonymous, don't treat that as an error"
contract for the header path.

## Risks / Trade-offs

- **The SDK's session-ownership-mismatch rejection (`404`) is not covered by the existing `401`
  recording gate — a pre-existing gap, not introduced here.** `IdentityMiddleware.__call__`
  ([identity.py:227](../../../bloommcp/src/bloom_mcp/identity.py#L227)) only suppresses recording
  when the downstream status is `401`. `streamable_http_manager.py:247-255` rejects a
  session/credential mismatch with `404` instead. Today (header-only), a request that fails that
  check with an otherwise-valid header would still be recorded — this change does not make that
  better or worse: the same `404` would still not be caught by the `401`-only gate regardless of
  which source (header or `AccessToken.subject`) supplied the identity being recorded. Disclosed
  per this codebase's convention of not silently bundling an unrelated fix into a change that
  merely surfaced it; a future change tightening the gate to also exclude `404` (distinguishing it
  from an ordinary not-found route, which should *not* suppress recording) can do so independently.
- **No real-traffic exposure to validate the precedence rule against**, same caveat
  `add-bloommcp-caller-identity` already carries for the header path — no current client sends
  both credentials on one request. Accepted; covered by an explicit test instead (Decision 2).
- **Depends on PR #613, unmerged.** This change's own branch is stacked on `feat/bloommcp-login`
  and cannot be exercised against `staging` until that PR merges. See proposal.md Impact.
- **This change is the first thing that links `bloommcp_usage` rows to a real individual with no
  intermediary, more directly than the risk `add-bloommcp-caller-identity`'s own design.md already
  disclosed.** That design's "No retention policy" risk frames real-researcher-identity
  accumulation as contingent on *"once the langchain-agent sibling ships"* — a header minted by a
  service on a user's behalf, still not shipped today. This change ships a more direct trigger: an
  external individual logging into bloommcp with their own Supabase account via OAuth, no
  intermediary, landing in `bloommcp_usage` under their own subject as soon as PR #613 is enabled
  anywhere. The underlying risk and its acceptance are unchanged from that design's own framing
  (bounded table, no PII beyond a Supabase user id, no new retention policy needed) — this bullet
  exists so a reader of *this* proposal doesn't have to cross-reference the other one to learn that
  merging this starts that accumulation for real, not conditionally.
- **Not a new correctness risk, but worth naming:** a request rejected by the SDK's session-hijack
  guard (the `404` gap, above) would, after this change, be recorded under the caller's *real*
  subject instead of `anonymous` when they authenticated via OAuth — arguably more accurate
  attribution than today, not a new misattribution.

## Migration Plan

Purely additive to `identity.py`: one new small helper function, one new fallback branch in
`IdentityMiddleware.__call__`'s identity resolution (the line that currently reads
`identity or ANONYMOUS`). No schema change, no new migration, no change to `usage.py`'s contract.

**Two interpretability consequences, disclosed here rather than left implicit in "no data
implications" (which is true only of the schema/rollback mechanics, not of what the numbers mean):**

- **The `anonymous` row's `request_count` growth rate will slow going forward** — traffic that used
  to collapse into that one sentinel row (any OAuth-authenticated request with no header) now
  splits out into per-subject rows instead. Anyone watching that row's trend over time would see
  this as a drop in traffic rather than what it actually is: a change in attribution. Not a defect;
  worth knowing before reading too much into that row post-deploy.
- **Rolling back does not merge per-subject rows back into `anonymous` or delete them** — reverting
  the code change stops new activity from updating them, but rows already created while this was
  live simply go stale in place. If a rollback is ever needed after real usage accumulated, decide
  explicitly whether to leave those rows (harmless, just inert) or clean them up — this plan doesn't
  do either automatically.

## Open Questions

- Whether a GitHub issue should be filed to formally track this (PR #613's own text is the only
  current reference) — not blocking; noted the same way `add-bloommcp-caller-identity` tasks.md
  7.1 discloses its own unfiled sibling.
- Whether the `404`/session-hijack gating gap (Risks) warrants its own follow-up change — not
  decided here; flagged for whoever picks it up next.
