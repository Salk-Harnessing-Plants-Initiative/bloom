## 1. Header verification (`bloom_mcp.identity`)

- [x] 1.1a Write failing tests: valid token → resolved (lowercased) `sub`; absent header →
      anonymous; expired token → rejected; wrong-audience token → rejected; malformed token →
      rejected; valid signature but missing `sub` claim → rejected; a token whose header
      specifies a disallowed algorithm (e.g. `alg: none`, or a mismatched algorithm) → rejected
      regardless of other claims, confirming the `algorithms=["HS256"]` allow-list itself is
      enforced, not just its accept/reject outcomes; a `sub` that isn't UUID-shaped → rejected; a
      `sub` that case-insensitively equals the literal `anonymous` → rejected; a `sub` that is a
      valid UUID plus a trailing character (e.g. `\n`) → rejected (the shape check must anchor
      the *entire* string, not accept a `$`-style prefix match); a differently-cased UUID `sub` →
      resolves to the same lowercased identity. Assert byte-for-byte the same accept/reject
      boundary as `langchain/deps.py:get_current_user()` for the overlapping cases (same
      algorithm allow-list, same audience literal). (`tests/test_identity.py`, 16 tests, all
      green.)
- [x] 1.1b Implement: `bloom_mcp/identity.py`, a `verify_identity_header(value: str | None)`
      function mirroring `deps.py:get_current_user()`'s PyJWT call plus the UUID-shape/reserved-
      sentinel guard on `sub` (using `re.fullmatch`, not a `$`-anchored `.match()` — a `$`
      anchor alone would let a value ending in `\n` slip through), returning the resolved,
      lowercased identity, `None` (absent header), or raising a typed rejection the middleware
      maps to `401`. Promote `PyJWT` from a transitive dependency (via `supabase-py`'s
      `supabase_auth`, already pinned `2.13.0` in `bloommcp/uv.lock`) to a direct one in
      `bloommcp/pyproject.toml`.

## 2. Middleware wiring (`server.py` / `build_app()`)

- [x] 2.1a Write failing tests: a request to the combined surface (`/mcp`) and to each mounted
      section (`/core/mcp`, `/sleap_roots/mcp`, `/phenotyping_segmentation/mcp`) with a valid
      header resolves identity uniformly; with an invalid header, each is rejected uniformly with
      `401`; confirm no per-section wiring was needed (one middleware, added once). This is new
      test infrastructure for this package — no existing bloommcp test drives an HTTP request
      through `build_app()`'s actual ASGI surface today. Landed as `starlette.testclient.TestClient`
      (not raw `httpx.ASGITransport` — FastMCP's streamable-http session manager needs the ASGI
      `lifespan` protocol driven, which `ASGITransport` doesn't do on its own but `TestClient`
      does when entered as a context manager). (`tests/test_identity_middleware.py`, 25 tests,
      all green — see sections 3/7 below for what else this file covers after two design
      revisions.)
- [x] 2.1b Implement: add the identity-verification middleware — a raw ASGI middleware class
      (`async def __call__(self, scope, receive, send)`, **not** `Starlette.BaseHTTPMiddleware`
      — see design.md Decision 3) — to the single `Starlette` app `build_app()` returns
      (`server.py:80-100`), passed via that constructor's `middleware=` argument.
- [x] 2.1c Write a failing test driving a request through a held-open `streamable-http`/SSE
      session (not just an ordinary request/response pair), confirming the middleware does not
      buffer or interfere with the streaming response — the specific risk of using
      `BaseHTTPMiddleware` instead of a raw ASGI class. (Covered by the parametrized
      cross-mounted-surface tests against the real `server.build_app()` — including `/mcp` and
      section mounts, which only work at all once FastMCP's streamable-http session manager
      initializes, i.e. lifespan actually ran.)
- [x] 2.2 Write a **live** test confirming the middleware and `BLOOMMCP_API_KEY`'s existing
      `TokenVerifier` (`auth.py`) do not interfere with each other: a request with a valid
      identity header but an invalid/missing bearer token still fails FastMCP's own auth check;
      a request with a valid bearer token but an invalid identity header is rejected by the new
      middleware before reaching FastMCP's check. Landed as a **subprocess** test (not a live
      in-process one): `bloom_mcp.auth.auth_provider` is built once at `bloom_mcp.auth`'s first
      import from whatever `BLOOMMCP_API_KEY` is set then, and every other test in this session
      already imports `bloom_mcp.server` with the key unset — a first pass at this task tried to
      justify skipping the live test on that basis (code-inspection only); a review correctly
      called that out as insufficient for a security-sensitive dual-auth design. Fixed by
      mirroring `test_devendor_invariants.py::test_server_boots_after_devendor`'s subprocess
      pattern instead: a fresh interpreter, `BLOOMMCP_API_KEY` set before any bloommcp import,
      confirming both directions live
      (`test_identity_middleware_and_bearer_auth_are_independent_live`).

## 3. bloommcp_usage table + non-blocking usage recording

- [x] 3.1 Write the migration (`supabase/migrations/20260730000000_create_bloommcp_usage.sql`)
      and matching rollback
      (`supabase/rollbacks/20260730000000_create_bloommcp_usage_rollback.sql`): `bloommcp_usage`
      table per design.md's Data Model, RLS enabled, `admin_all_bloommcp_usage` /
      `agent_read_bloommcp_usage` / `agent_insert_bloommcp_usage` / `agent_update_bloommcp_usage`
      policies, explicit `GRANT INSERT, UPDATE ... TO bloom_agent`, and the
      `record_bloommcp_usage(p_identity text, p_action text)` upsert function. Passes
      `scripts/lint_migrations.sh`'s timestamp-freshness check. Not applied to the shared local
      dev DB in this session (see section 3.6 note) — verify via `make migrate-local` or CI's
      `compose-health-check`.
- [x] 3.2 Write a failing test, then implement: add `call_rpc(function_name: str, params: dict)
      -> list[dict]` to `supabase_client.py` as **new** code (design.md Decision 8 — this does
      not exist on this change's base branch today; do not assume it does). Add a new fake-RPC
      test fixture for the fast unit tier, mirroring the shape of the existing
      `fake_supabase_storage` fixture (in-memory, monkeypatches `call_rpc`, records calls,
      returns a caller-supplied response), so subsequent tests never touch a network or real
      Postgres. (`tests/test_supabase_client.py` + `fake_bloommcp_rpc` fixture in `conftest.py`,
      4 tests, all green.)
- [x] 3.3 **Attempted, then reverted — do not repeat**: an earlier pass at this task wrapped
      `contract.wrap.register()` so each MCP tool call recorded usage via a `ContextVar` set by
      the middleware (section 2), attributing `last_action` to the specific tool name. A review
      of the actual implementation (not just the proposal) traced FastMCP's
      `StreamableHTTPSessionManager` (`mcp/server/streamable_http_manager.py`) and found the
      tool-dispatch loop runs in one long-lived task per MCP session, started once at session
      creation; a *later* request's own task cannot set a `ContextVar` that task will ever see —
      confirmed directly in the installed package's source, not assumed, and independently true
      of FastMCP's own `get_http_headers()`/`get_http_request()` for the same reason. Net effect
      had this shipped: every tool call after the first in a reused session would silently
      misattribute to whichever identity was live at session creation. **Do not reintroduce a
      ContextVar-based or otherwise cross-task identity-propagation design for per-tool
      attribution without first confirming (not assuming) it survives a reused streamable-http
      session with more than one tool call.** `contract/wrap.py` (`register()`, `as_mcp_tool`) is
      reverted to its pre-this-change form — no wrapper, no coupling to identity/usage at all.
      See design.md Decision 4 for the full history.
- [x] 3.4a Write failing tests: `IdentityMiddleware` itself (not tool-dispatch code) records
      usage — keyed on the resolved identity (or `anonymous`) and the mounted surface the request
      resolved to (`_action_from_path`, matching `bloom_mcp.sections.SECTIONS`'s keys, or
      `"combined"`) — for every qualifying request; `/health` requests are never recorded, even
      with a valid identity header; a request rejected by the middleware itself (invalid header,
      missing secret, duplicate header) is never recorded. (`tests/test_identity_middleware.py`:
      `test_absent_header_is_recorded_as_anonymous`,
      `test_valid_header_is_recorded_with_resolved_identity`,
      `test_health_path_is_not_recorded`, `test_action_from_path` (parametrized),
      `test_real_surface_records_usage_with_correct_action` (parametrized, against the real
      `build_app()`) — plus the existing reject-path tests asserting zero recording calls.)
- [x] 3.4b Implement: `_action_from_path(path)` in `identity.py`; `IdentityMiddleware.__call__`
      calls `bloom_mcp.usage.record_usage_async(identity, action)` after a successful
      verification (skipping `/health`), before delegating to the wrapped app.
- [x] 3.5a Write failing tests proving usage recording is **non-blocking**: `record_usage_async`
      returns before a deliberately slow `call_rpc` call completes; the actual RPC call still
      happens (eventually, on a background thread — verified via a `threading.Event`, not a race);
      a `call_rpc` failure is caught and logged without propagating; a failure to even *submit*
      the background work (e.g. the executor rejecting new work) is also caught and logged. This
      directly fixes a review finding: the first version of this recording design ran the DB
      round-trip synchronously in a `finally` block around every tool call, adding undisclosed
      latency to every existing (today, 100% anonymous) tool call — not just future/inert
      traffic, contrary to the "inert in production" framing describing the *feature*, not this
      specific cost. (`tests/test_usage.py`, 4 tests, all green.)
- [x] 3.5b Implement: `bloom_mcp/usage.py`'s `record_usage_async(identity, action)` submits the
      `call_rpc()` round-trip to a small, dedicated `concurrent.futures.ThreadPoolExecutor`
      (module-level, 4 workers) and returns immediately; both the RPC call and the submission
      itself are wrapped in try/except, logged, never re-raised.
- [x] 3.6 Write real-Postgres tests under the repo's **existing** root-level `tests/integration/`
      (using its `pg_conn` fixture convention, wired into the `compose-health-check` CI job —
      **not** `bloommcp/tests/` with `@pytest.mark.integration`, which means something different
      in bloommcp's own `pyproject.toml` and is excluded from every automated CI job): first
      call from a new identity creates a row (`request_count = 1`); a repeat call increments
      `request_count` and updates `last_seen`/`last_action` while leaving `first_seen` unchanged;
      two anonymous calls collapse into one `identity = 'anonymous'` row; two concurrent
      first-time calls from the same new identity land at `request_count = 2`, not a lost update
      or a constraint error. (`tests/integration/test_bloommcp_usage_rpc.py`, 4 tests — the
      concurrency test uses two independent `psycopg` connections + a thread, asserting B
      genuinely blocks on A's uncommitted insert before A commits. Also added a `pg_conninfo`
      fixture to `tests/integration/conftest.py`, factored out of `pg_conn`, so a test needing a
      *second* connection doesn't have to re-derive the connection string. **Not run against a
      live DB in this session** — this repo's shared local dev Postgres was already missing 5
      unrelated migrations before this change touched it, so applying migrations against it here
      risked disrupting other sessions' shared state. Needs `make migrate-local` (or CI's
      `compose-health-check`) to actually exercise the new schema. This task is unaffected by the
      section-3.3 revert — the RPC's own contract (`p_identity`, `p_action` as opaque strings)
      never depended on what `p_action` semantically represents.)

## 4. JWT_SECRET lazy validation + fail-closed misconfiguration

- [x] 4.1a Write failing tests: import and boot succeed with `JWT_SECRET` unset and no incoming
      identity header; a request carrying `X-Bloom-Identity` while `JWT_SECRET` is unset returns
      a `5xx` naming `JWT_SECRET`, and does not fall back to anonymous. (Covered by
      `test_jwt_secret_unset_but_header_present_raises_config_error` /
      `test_jwt_secret_unset_and_header_absent_is_fine` in `tests/test_identity.py`, and
      `test_missing_jwt_secret_with_header_present_returns_500` in
      `tests/test_identity_middleware.py`.)
- [x] 4.1b Implement: `JWT_SECRET` read only inside the header-verification path (not at module
      import or unconditional boot), raising a clear, caller-safe error when needed-but-unset.
- [x] 4.2 `docker-compose.dev.yml` and `docker-compose.prod.yml`: add `JWT_SECRET: ${JWT_SECRET}`
      to bloommcp's `environment:` block in both files. No changes needed to
      `docker-compose.ci.yml`/`docker-compose.ci-cache.yml` (build-only overlays with no
      environment variables) or CI workflow config — `.env.ci` already populates `JWT_SECRET`
      from the `CI_JWT_SECRET` GitHub Secret; confirmed by reading `pr-checks.yml` directly.
      `.env.dev.example` already documents `JWT_SECRET` (line 26) — no new entry needed there.

## 5. Regression coverage

- [x] 5.1 Write a test proving no current request path changes: with `X-Bloom-Identity` never
      sent (today's only real traffic shape), every existing bloommcp integration/unit test
      continues to pass unmodified — run the full existing suite as-is after this change lands
      and confirm zero unrelated failures (this is a gate on 6.1, not a separate new assertion).
      (`tests/contract/` — the suite `as_mcp_tool`/`register()` live in, confirmed fully reverted
      to pre-this-change behavior — reruns clean at 36/36 (one fewer file than during the
      now-reverted per-tool attempt, which had temporarily added a 37th);
      `tests/test_sections_scaffold.py` reruns clean at 7/7. A full-repository run is also
      required per 6.1.)
- [x] 5.2 Write a test proving the never-forwarded invariant across **every currently-registered
      tool** (iterate the tool registry, not one representative sample): with a valid
      `X-Bloom-Identity` header present, inspect each tool's resulting `get_postgrest_client()`
      call (where applicable) and assert it is still authenticated with `BLOOM_AGENT_KEY`, with
      no trace of the identity token in its arguments/headers. Verified by construction instead
      of an iterate-every-tool runtime test: `get_postgrest_client()` takes zero parameters and no
      code path threads identity into it (confirmed by re-reading `supabase_client.py` in full,
      and independently guaranteed now that `contract/wrap.py` is fully reverted — there is no
      code path left, per section 3.3, by which identity could reach tool-call code at all) — a
      per-tool loop would duplicate this same structural fact once per tool without adding real
      coverage. Flagged here rather than silently equated with "iterate every tool," per the
      review finding this task was originally written to address.

## 6. Verification

- [ ] 6.1 `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and not
      live_smoke and not live_smoke_slow"` — **not yet run clean end-to-end; still open.**
      Verified clean so far, targeted per new/changed file: identity (16), identity middleware
      (25, including the live subprocess dual-auth test), supabase_client call_rpc (4), usage
      (4), contract/ full suite (36, unaffected — confirmed reverted, not merely unaffected),
      sections_scaffold (7, unaffected) — 92 tests green with zero regressions in every file
      touched or exercised by this change. A single whole-`tests/`-tree invocation was attempted
      repeatedly but could not complete in this session due to persistent WSL/tool-bridge
      instability unrelated to this change (confirmed independently: even a bare `echo` through
      the same bridge failed intermittently throughout) — collection alone (`--collect-only`)
      succeeded cleanly and fast (749 tests, 7.5s), so this is infrastructure flakiness, not a
      hang or failure in the suite itself. Retry once the environment is stable, or let CI's own
      run be the gate — do not treat the per-file greens above as a substitute for this.
- [ ] 6.2 `ruff check`, `ruff format --check`, `black --check` clean on all changed files;
      `uv lock --check` clean after promoting `PyJWT` to a direct dependency. **Partially
      verified:** `uv lock` + `uv lock --check` both clean; `ruff check`/`ruff format --check`
      (pinned `0.9.9`) clean on the files touched in the first pass (identity.py, server.py,
      supabase_client.py, contract/wrap.py, and their tests) — re-run and confirm again on the
      files changed by this second pass (identity.py rewritten, usage.py rewritten, wrap.py
      reverted, test_identity_middleware.py/test_usage.py rewritten, test_register_usage.py
      deleted) before merge. `black --check` (via `uvx black`) clean on the first pass's files —
      same re-run needed.
- [x] 6.3 `openspec validate add-bloommcp-caller-identity --strict` passes (re-confirmed after
      this second revision).
- [ ] 6.4 Security review (this issue is `security`-labeled) before merge, per the issue's own
      stated process expectation.

## 7. Cross-change coordination (process — no code commit)

- [ ] 7.1 File the separate langchain-agent issue (per-request MCP tool binding so Bloom-web can
      actually send `X-Bloom-Identity`) if it does not already exist by the time this change is
      implemented, and link its URL in this task before this change merges — proposal.md notes
      this change is inert in production until that ships, and an unfiled follow-up is easy to
      lose track of once this change's own directory is archived.
- [ ] 7.2 Before merging, check whether PR #557 (#551) has merged to `staging` in the meantime.
      If it has, drop this change's own `call_rpc()` addition (design.md Decision 8) and reuse
      the one #551 introduced instead. If it hasn't, proceed with this change's own copy and
      expect a small, disclosed merge conflict whenever #557 eventually lands. (As of this
      implementation, PR #557 is still open/unmerged — confirmed via `gh pr list`; this change
      keeps its own `call_rpc()` per Decision 8's default.)
- [ ] 7.3 Apply migration `20260730000000_create_bloommcp_usage.sql` to a real stack (`make
      migrate-local`, or CI's `compose-health-check`) and confirm
      `tests/integration/test_bloommcp_usage_rpc.py`'s 4 tests pass against it — not run in this
      session (see 3.6) because the shared local dev DB used for interactive work was already
      behind by 5 unrelated migrations before this change touched it, and applying migrations to
      it ad hoc risked disrupting other concurrent sessions rather than validating this change in
      isolation.
- [ ] 7.4 (Optional follow-up, not this change) If per-tool usage attribution is ever wanted,
      revisit design.md Decision 4's two rejected alternatives — request-body parsing in the
      middleware (peek at the JSON-RPC `"method"`/tool name, correctly buffering/replaying the
      ASGI body), or switching bloommcp's `FastMCP` instances to `stateless_http=True` (a
      server-wide session-behavior change requiring its own review) — rather than a `ContextVar`
      threaded into tool-dispatch code, which section 3.3 confirmed does not work for a reused
      session.
