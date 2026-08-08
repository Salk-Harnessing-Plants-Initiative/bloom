## Why

[PR #613](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/613) ("OAuth 2.1 login
for external MCP clients") lets a person connect to bloommcp directly — via Claude Desktop, MCP
Inspector, or Claude Code — through a Supabase-issued OAuth access token instead of the shared
`BLOOMMCP_API_KEY`. `bloom_mcp.auth.SupabaseOAuthVerifier` verifies that token and resolves the
caller's own Supabase user id into its `subject` claim. But `bloommcp_usage` recording
(`bloom_mcp.identity.IdentityMiddleware`, from `add-bloommcp-caller-identity` / issue #406) still
reads only the `X-Bloom-Identity` header — a header no OAuth client sends, and which was designed
for a completely different calling pattern (a shared service caller, e.g. langchain-agent, layering
a per-end-user hint on top of its own static API key). So every OAuth-authenticated caller today
still gets recorded as `anonymous`, even though bloommcp already knows exactly who they are. PR
#613's own description discloses this directly: *"bloommcp authenticates the OAuth user, but
`IdentityMiddleware` still reads only the `X-Bloom-Identity` header, so `bloommcp_usage` records
`anonymous`. Follow-up PR, stacked on this one."* This proposal is that follow-up.

No separate GitHub issue tracks this yet — PR #613's own text is the request. (Compare: the
`add-bloommcp-caller-identity` change has an analogous still-unfiled follow-up of its own — the
langchain-agent header-wiring issue, tasks.md 7.1 — disclosed the same way there.) The parent
tracking issue for this whole line of work is #554 ("Epic: bloommcp hosted-server UX"), which maps
#406 under its "Connect" section; #406 itself specified a stricter, ID-token-only verification rule
that PR #613's access-token-based OAuth login doesn't follow — a real, disclosed-not-resolved
tension this proposal inherits rather than introduces; see design.md Context for the full
discussion.

**Why this isn't just "the same problem, solved already":** `add-bloommcp-caller-identity`'s
design.md Decision 4 already investigated, and rejected, a `ContextVar`-based mechanism for
carrying per-request identity into MCP tool-dispatch code, because it cannot survive a *reused*
`streamable-http` session (FastMCP's session manager runs the actual dispatch loop in one
long-lived task per session; a later request's own task can't set a `ContextVar` that task will
ever observe). A reviewer encountering this proposal could reasonably ask "didn't we already
confirm this doesn't work?" — this proposal's design.md answers that directly: reading the verified
`AccessToken` off `scope["user"]` is not the rejected mechanism. It never crosses a task boundary
at all (`scope` is one dict, passed by reference through `IdentityMiddleware`'s own single
`await self.app(...)` call, in its own task), so Decision 4's specific finding does not apply here.
Verified by tracing the installed `fastmcp`/`mcp` package source directly, not assumed — see
design.md.

## What Changes

- **`bloom_mcp.identity.IdentityMiddleware` gains a second identity source, consulted only when
  the first yields nothing.** Precedence, in order:
  1. A verified `X-Bloom-Identity` header (unchanged — existing behavior, existing tests,
     untouched).
  2. The `AccessToken.subject` of whichever credential FastMCP's own bearer-auth layer verified
     for this request, read from `scope["user"].access_token.subject` after
     `await self.app(...)` returns (same position recording already happens, same request, same
     task). Only `bloom_mcp.auth.SupabaseOAuthVerifier`-issued tokens carry a `subject`;
     `ApiKeyVerifier`'s shared-key credential never does (mirrors today's shared-key semantics —
     "names no individual" — exactly).
  3. `anonymous`, if neither source resolves.
- **No change to the `X-Bloom-Identity` rejection path, to `bloommcp_usage`'s schema, or to
  recording granularity** — this only widens which requests resolve to a real identity instead of
  `anonymous`; everything about *how* a resolved identity gets recorded (request/mounted-surface
  granularity, non-blocking, gated on a non-401 downstream response, atomic upsert) is unchanged.
- **Caller-token/DB-authority invariant is unaffected.** The `AccessToken` is read only to label a
  `bloommcp_usage` row; it is never passed to `supabase_client`, matching the already-tested
  invariant (`test_supabase_client.py`, closed separately) and this change adds no new call site
  that could threaten it.
- **New tests**, matching this codebase's existing layered convention for this middleware: a
  scope-parsing unit test (every "AccessToken has no usable subject" shape: absent, unauthenticated,
  API-key credential, empty string), a `_DummyApp`-based middleware unit test (simulating what
  FastMCP's real auth middleware writes into `scope["user"]`, without needing FastMCP wired up),
  a **live subprocess** integration test driving a real OAuth-authenticated request through
  `server.build_app()` end-to-end — `bloom_mcp.auth.auth_provider` is built once at import time
  from whatever OAuth env vars are set then (mirrors the existing dual-auth live test's own
  rationale, tasks.md 2.2 in `add-bloommcp-caller-identity`) — and a precedence test (header wins
  over a simultaneously-present OAuth subject).

## Impact

- **Affected specs:** `bloommcp-caller-identity` (MODIFIED — the `bloommcp_usage Records Caller
  Activity Per Mounted Surface` requirement's identity-resolution rule gains the OAuth-subject
  source and its precedence). That capability's own originating change
  (`add-bloommcp-caller-identity`) is not yet archived; this change's delta targets it directly,
  per the same disclosed-dependency precedent as `add-bloommcp-signed-url-key-scoping` extending
  `add-bloommcp-signed-url-download` before that one archived. **This change must not be archived
  independently of `add-bloommcp-caller-identity`.**
- **Affected code:**
  - Modified: `bloommcp/src/bloom_mcp/identity.py` (`IdentityMiddleware.__call__`, plus a new
    `_oauth_subject_from_scope(scope)` helper).
  - Tests: `bloommcp/tests/test_identity.py` (the new `_oauth_subject_from_scope` helper — a pure
    function, alongside that file's existing `verify_identity_header`/`is_valid_identity` tests, not
    a new module); `bloommcp/tests/test_identity_middleware.py` (new unit + live-subprocess cases,
    alongside its existing `IdentityMiddleware` coverage).
  - No schema, migration, or `usage.py` change — `record_usage_async`'s own contract
    (`identity: str, action: str`) is unaffected.
- **Depends on PR #613** (`feat/bloommcp-login`, not yet merged to `staging`) for
  `bloom_mcp.auth.SupabaseOAuthVerifier`, `MultiAuth`, `RemoteAuthProvider`, and the OAuth env
  vars (`BLOOMMCP_PUBLIC_URL`, `BLOOMMCP_OAUTH_AUTHORIZATION_SERVER`, `BLOOMMCP_OAUTH_JWKS_URI`)
  this proposal's tests configure. This change's branch is stacked on `feat/bloommcp-login`
  (matching `add-bloommcp-signed-url-key-scoping`'s stacking on `add-bloommcp-signed-url-download`)
  and cannot merge before it.

## Non-Goals

- **Per-tool attribution.** Stays at request/mounted-surface granularity, exactly as
  `add-bloommcp-caller-identity` Decision 4 already settled — this change does not revisit that;
  it only changes which *source* supplies the identity for the same granularity.
- **Fixing the SDK's session-ownership-mismatch (404) gating gap.** Research for this change found
  that `IdentityMiddleware` currently gates recording on `status != 401` only, while the MCP SDK's
  own reused-session-hijack guard (`mcp/server/streamable_http_manager.py`) rejects a
  session/credential mismatch with `404`, not `401` — such a request is recorded today (via the
  header path) and would continue to be recorded (via either source) after this change. This is a
  **pre-existing** gap, not introduced or worsened by this change, and not fixed here — see
  design.md Risks.
- **Enabling OAuth in staging/prod, or generating real ES256 secrets for them.** Both remain
  exactly as PR #613 left them; out of scope for this change.
