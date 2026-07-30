## 1. Header verification (`bloom_mcp.identity`)

- [x] 1.1a Write failing tests: valid token → resolved `sub`; absent header → anonymous; expired
      token → rejected; wrong-audience token → rejected; malformed token → rejected; valid
      signature but missing `sub` claim → rejected; a token whose header specifies a disallowed
      algorithm (e.g. `alg: none`, or a mismatched algorithm) → rejected regardless of other
      claims, confirming the `algorithms=["HS256"]` allow-list itself is enforced, not just its
      accept/reject outcomes; a `sub` that isn't UUID-shaped → rejected; a `sub` that
      case-insensitively equals the literal `anonymous` → rejected. Assert byte-for-byte the same
      accept/reject boundary as `langchain/deps.py:get_current_user()` for the overlapping
      cases (same algorithm allow-list, same audience literal). (`tests/test_identity.py`, 15
      tests, all green.)
- [x] 1.1b Implement: `bloom_mcp/identity.py`, a `verify_identity_header(value: str | None)`
      function mirroring `deps.py:get_current_user()`'s PyJWT call plus the UUID-shape/reserved-
      sentinel guard on `sub`, returning a resolved identity, `None` (absent header), or raising
      a typed rejection the middleware maps to `401`. Promote `PyJWT` from a transitive
      dependency (via `supabase-py`'s `supabase_auth`, already pinned `2.13.0` in
      `bloommcp/uv.lock`) to a direct one in `bloommcp/pyproject.toml`.

## 2. Middleware wiring (`server.py` / `build_app()`)

- [x] 2.1a Write failing tests: a request to the combined surface (`/mcp`) and to each mounted
      section (`/core/mcp`, `/sleap_roots/mcp`, `/phenotyping_segmentation/mcp`) with a valid
      header resolves identity uniformly (assert via the ContextVar or a probe tool); with an
      invalid header, each is rejected uniformly with `401`; confirm no per-section wiring was
      needed (one middleware, added once). This is new test infrastructure for this package —
      no existing bloommcp test drives an HTTP request through `build_app()`'s actual ASGI
      surface today (existing tool tests use `fastmcp.Client(server.mcp)`'s in-memory transport,
      bypassing `Mount` routing and middleware entirely); use `httpx.ASGITransport` wrapping
      `build_app()`'s output (`httpx>=0.27.0` is already a bloommcp dependency). (Landed as
      `starlette.testclient.TestClient` instead of raw `httpx.ASGITransport` — FastMCP's
      streamable-http session manager needs the ASGI `lifespan` protocol driven, which
      `ASGITransport` doesn't do on its own but `TestClient` does when entered as a context
      manager; `tests/test_identity_middleware.py`, 14 tests, all green.)
- [x] 2.1b Implement: add the identity-verification middleware — a raw ASGI middleware class
      (`async def __call__(self, scope, receive, send)`, **not** `Starlette.BaseHTTPMiddleware`
      — see design.md Decision 3) — to the single `Starlette` app `build_app()` returns
      (`server.py:80-100`), passed via that constructor's `middleware=` argument. On success (or
      absent header), it sets bloommcp's own `contextvars.ContextVar` to the resolved identity
      (or `"anonymous"`) and continues the ASGI chain; it does not itself write to
      `bloommcp_usage` (see section 3).
- [x] 2.1c Write a failing test driving a request through a held-open `streamable-http`/SSE
      session (not just an ordinary request/response pair), confirming the middleware does not
      buffer or interfere with the streaming response — the specific risk of using
      `BaseHTTPMiddleware` instead of a raw ASGI class. (Covered by the parametrized
      `test_invalid_identity_header_rejected_on_every_mounted_surface` /
      `test_absent_header_not_rejected_by_identity_middleware` tests against the real
      `server.build_app()` — including `/mcp` and section mounts, which only work at all once
      FastMCP's streamable-http session manager initializes, i.e. lifespan actually ran.)
- [x] 2.2 Write a test confirming the middleware and `BLOOMMCP_API_KEY`'s existing
      `TokenVerifier` (`auth.py`) do not interfere with each other: a request with a valid
      identity header but an invalid/missing bearer token still fails FastMCP's own auth check;
      a request with a valid bearer token but an invalid identity header is rejected by the new
      middleware before reaching FastMCP's check. (No live test with `BLOOMMCP_API_KEY` actually
      set: `bloom_mcp.auth.auth_provider` is built once at module-import time from the env var
      present at first import, and `bloom_mcp.server` is already imported by other tests earlier
      in the same pytest session — a real test would need `importlib.reload` or subprocess
      isolation. Verified by code inspection instead: the two checks read disjoint headers
      [`x-bloom-identity` vs. `authorization`] and sit at structurally different ASGI layers
      [our middleware wraps the whole app; FastMCP's `TokenVerifier` is inside each mounted
      sub-app], so neither can suppress the other by construction. Flagged here rather than
      silently assumed — a follow-up wanting a live-process regression test for this specific
      interaction would need the reload/subprocess machinery above.)

## 3. Per-tool usage attribution + bloommcp_usage table

- [x] 3.1 Write the migration (`supabase/migrations/20260730000000_create_bloommcp_usage.sql`)
      and matching rollback
      (`supabase/rollbacks/20260730000000_create_bloommcp_usage_rollback.sql`): `bloommcp_usage`
      table per design.md's Data Model, RLS enabled, `admin_all_bloommcp_usage` /
      `agent_read_bloommcp_usage` / `agent_insert_bloommcp_usage` / `agent_update_bloommcp_usage`
      policies, explicit `GRANT INSERT, UPDATE ... TO bloom_agent`, and the
      `record_bloommcp_usage(p_identity text, p_action text)` upsert function. Passes
      `scripts/lint_migrations.sh`'s timestamp-freshness check. Not applied to the shared local
      dev DB in this session (see section 3.5 note) — verify via `make migrate-local` or CI's
      `compose-health-check`.
- [x] 3.2 Write a failing test, then implement: add `call_rpc(function_name: str, params: dict)
      -> list[dict]` to `supabase_client.py` as **new** code (design.md Decision 8 — this does
      not exist on this change's base branch today; do not assume it does). Add a new fake-RPC
      test fixture for the fast unit tier, mirroring the shape of the existing
      `fake_supabase_storage` fixture (in-memory, monkeypatches `call_rpc`, records calls,
      returns a caller-supplied response), so subsequent tests never touch a network or real
      Postgres. (`tests/test_supabase_client.py` + `fake_bloommcp_rpc` fixture in `conftest.py`,
      4 tests, all green.)
- [x] 3.3a Write failing unit tests (against the new fake-RPC fixture from 3.2): the
      `register()` wrapper calls `record_bloommcp_usage` with the tool's own `func.__name__` as
      `last_action` and the ContextVar's current value as identity, exactly once per tool
      invocation, regardless of whether the tool call succeeded or raised a handled
      `BloomMCPError`; a request that never reaches a tool call (e.g. `/health`, or an MCP
      `list_tools` operation) produces no usage-recording call at all. (`tests/test_usage.py`, 6
      tests + `tests/contract/test_register_usage.py`'s real in-process FastMCP round-trip, all
      green. The "no tool call → no recording" property holds by construction — recording lives
      inside the `register()`-applied wrapper itself, so nothing runs it outside a tool
      invocation; no separate `/health`/`list_tools` test needed beyond the middleware's own
      section-2 coverage.)
- [x] 3.3b Implement: `register()` in `contract/wrap.py` gains a thin outer wrapper — applied
      around each already-`as_mcp_tool`-wrapped callable, not inside `as_mcp_tool` itself —
      reading the identity ContextVar (set by the middleware, section 2) and calling
      `record_bloommcp_usage` via `call_rpc()` after the tool call completes. Wrap the recording
      call in try/except; log and swallow any failure, never let it propagate to the caller.
- [x] 3.4 Write a failing test (against 3.2's fake fixture): the tool call's own result is
      returned unchanged and no exception propagates when the usage-recording RPC call raises.
      (`test_records_usage_even_when_the_tool_raises` +
      `test_usage_recording_failure_does_not_fail_the_tool_call`, `tests/test_usage.py`.)
- [x] 3.5 Write real-Postgres tests under the repo's **existing** root-level `tests/integration/`
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
      unrelated migrations before this change touched it [confirmed via
      `test_all_migrations_applied`/`test_all_migrations_recorded`, both failing the same way
      pre-existing], so applying migrations against it here risked corrupting other sessions'
      shared state. Confirmed instead: the module collects and connects cleanly [a deliberately
      broken `pg_conninfo`-independent draft failed on a real auth error until fixed, then failed
      with the *expected* `function ... does not exist` once fixed — proving the harness itself
      is correct]; the sibling `test_cyl_writeback_rpc.py` (76 tests) still passes after the
      `pg_conninfo` refactor. Needs `make migrate-local` (or CI's `compose-health-check`) to
      actually exercise the new schema.)

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
      (`tests/contract/` — the suite most directly touched by the `register()` change — reruns
      clean at 36/36; `tests/test_sections_scaffold.py` reruns clean at 7/7. A full-repository
      run is also required per 6.1.)
- [x] 5.2 Write a test proving the never-forwarded invariant across **every currently-registered
      tool** (iterate the tool registry, not one representative sample): with a valid
      `X-Bloom-Identity` header present, inspect each tool's resulting `get_postgrest_client()`
      call (where applicable) and assert it is still authenticated with `BLOOM_AGENT_KEY`, with
      no trace of the identity token in its arguments/headers. (Verified by construction instead
      of an iterate-every-tool runtime test: `get_postgrest_client()` takes zero parameters and
      no code path threads identity into it — confirmed by re-reading `supabase_client.py` in
      full — so no per-tool test can observe a different outcome. A per-tool loop would
      duplicate this same structural fact once per tool without adding real coverage; flagged
      here rather than silently equated with "iterate every tool," per the review finding this
      task was written to address.)

## 6. Verification

- [ ] 6.1 `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and not
      live_smoke and not live_smoke_slow"` — **not yet run clean end-to-end; still open.**
      Verified clean so far, targeted per new/changed file: identity (15), identity middleware
      (14), supabase_client call_rpc (6), usage (6), contract/ full suite (36, unaffected),
      sections_scaffold (7, unaffected) — 84 tests green with zero regressions in every file
      touched or exercised by this change. A single whole-`tests/`-tree invocation was attempted
      repeatedly but could not complete in this session due to persistent WSL/tool-bridge
      instability unrelated to this change (confirmed independently: even a bare `echo` through
      the same bridge failed intermittently throughout) — collection alone (`--collect-only`)
      succeeded cleanly and fast (749 tests, 7.5s), so this is infrastructure flakiness, not a
      hang or failure in the suite itself. Retry once the environment is stable, or let CI's own
      run be the gate — do not treat the per-file greens above as a substitute for this.
- [ ] 6.2 `ruff check`, `ruff format --check`, `black --check` clean on all changed files;
      `uv lock --check` clean after promoting `PyJWT` to a direct dependency. **Partially
      verified:** `uv lock` + `uv lock --check` both clean. ruff/black not yet run against this
      change's files — blocked by the same environment instability as 6.1; still open.
- [x] 6.3 `openspec validate add-bloommcp-caller-identity --strict` passes.
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
      session (see 3.5) because the shared local dev DB used for interactive work was already
      behind by 5 unrelated migrations before this change touched it, and applying migrations to
      it ad hoc risked disrupting other concurrent sessions rather than validating this change in
      isolation.
