## 1. Header verification (`bloom_mcp.identity`)

- [ ] 1.1a Write failing tests: valid token → resolved `sub`; absent header → anonymous; expired
      token → rejected; wrong-audience token → rejected; malformed token → rejected; valid
      signature but missing `sub` claim → rejected; a token whose header specifies a disallowed
      algorithm (e.g. `alg: none`, or a mismatched algorithm) → rejected regardless of other
      claims, confirming the `algorithms=["HS256"]` allow-list itself is enforced, not just its
      accept/reject outcomes; a `sub` that isn't UUID-shaped → rejected; a `sub` that
      case-insensitively equals the literal `anonymous` → rejected. Assert byte-for-byte the same
      accept/reject boundary as `langchain/deps.py:get_current_user()` for the overlapping
      cases (same algorithm allow-list, same audience literal).
- [ ] 1.1b Implement: `bloom_mcp/identity.py`, a `verify_identity_header(value: str | None)`
      function mirroring `deps.py:get_current_user()`'s PyJWT call plus the UUID-shape/reserved-
      sentinel guard on `sub`, returning a resolved identity, `None` (absent header), or raising
      a typed rejection the middleware maps to `401`. Promote `PyJWT` from a transitive
      dependency (via `supabase-py`'s `supabase_auth`, already pinned `2.13.0` in
      `bloommcp/uv.lock`) to a direct one in `bloommcp/pyproject.toml`.

## 2. Middleware wiring (`server.py` / `build_app()`)

- [ ] 2.1a Write failing tests: a request to the combined surface (`/mcp`) and to each mounted
      section (`/core/mcp`, `/sleap_roots/mcp`, `/phenotyping_segmentation/mcp`) with a valid
      header resolves identity uniformly (assert via the ContextVar or a probe tool); with an
      invalid header, each is rejected uniformly with `401`; confirm no per-section wiring was
      needed (one middleware, added once). This is new test infrastructure for this package —
      no existing bloommcp test drives an HTTP request through `build_app()`'s actual ASGI
      surface today (existing tool tests use `fastmcp.Client(server.mcp)`'s in-memory transport,
      bypassing `Mount` routing and middleware entirely); use `httpx.ASGITransport` wrapping
      `build_app()`'s output (`httpx>=0.27.0` is already a bloommcp dependency).
- [ ] 2.1b Implement: add the identity-verification middleware — a raw ASGI middleware class
      (`async def __call__(self, scope, receive, send)`, **not** `Starlette.BaseHTTPMiddleware`
      — see design.md Decision 3) — to the single `Starlette` app `build_app()` returns
      (`server.py:80-100`), passed via that constructor's `middleware=` argument. On success (or
      absent header), it sets bloommcp's own `contextvars.ContextVar` to the resolved identity
      (or `"anonymous"`) and continues the ASGI chain; it does not itself write to
      `bloommcp_usage` (see section 3).
- [ ] 2.1c Write a failing test driving a request through a held-open `streamable-http`/SSE
      session (not just an ordinary request/response pair), confirming the middleware does not
      buffer or interfere with the streaming response — the specific risk of using
      `BaseHTTPMiddleware` instead of a raw ASGI class.
- [ ] 2.2 Write a test confirming the middleware and `BLOOMMCP_API_KEY`'s existing
      `TokenVerifier` (`auth.py`) do not interfere with each other: a request with a valid
      identity header but an invalid/missing bearer token still fails FastMCP's own auth check;
      a request with a valid bearer token but an invalid identity header is rejected by the new
      middleware before reaching FastMCP's check.

## 3. Per-tool usage attribution + bloommcp_usage table

- [ ] 3.1 Write the migration (`supabase/migrations/<timestamp>_create_bloommcp_usage.sql`) and
      matching rollback (`supabase/rollbacks/<timestamp>_create_bloommcp_usage_rollback.sql`):
      `bloommcp_usage` table per design.md's Data Model, RLS enabled, `admin_all_bloommcp_usage`
      / `agent_read_bloommcp_usage` / `agent_insert_bloommcp_usage` /
      `agent_update_bloommcp_usage` policies, explicit `GRANT INSERT, UPDATE ... TO bloom_agent`,
      and the `record_bloommcp_usage(p_identity text, p_action text)` upsert function.
- [ ] 3.2 Write a failing test, then implement: add `call_rpc(function_name: str, params: dict)
      -> list[dict]` to `supabase_client.py` as **new** code (design.md Decision 8 — this does
      not exist on this change's base branch today; do not assume it does). Add a new fake-RPC
      test fixture for the fast unit tier, mirroring the shape of the existing
      `fake_supabase_storage` fixture (in-memory, monkeypatches `call_rpc`, records calls,
      returns a caller-supplied response), so subsequent tests never touch a network or real
      Postgres.
- [ ] 3.3a Write failing unit tests (against the new fake-RPC fixture from 3.2): the
      `register()` wrapper calls `record_bloommcp_usage` with the tool's own `func.__name__` as
      `last_action` and the ContextVar's current value as identity, exactly once per tool
      invocation, regardless of whether the tool call succeeded or raised a handled
      `BloomMCPError`; a request that never reaches a tool call (e.g. `/health`, or an MCP
      `list_tools` operation) produces no usage-recording call at all.
- [ ] 3.3b Implement: `register()` in `contract/wrap.py` gains a thin outer wrapper — applied
      around each already-`as_mcp_tool`-wrapped callable, not inside `as_mcp_tool` itself —
      reading the identity ContextVar (set by the middleware, section 2) and calling
      `record_bloommcp_usage` via `call_rpc()` after the tool call completes. Wrap the recording
      call in try/except; log and swallow any failure, never let it propagate to the caller.
- [ ] 3.4 Write a failing test (against 3.2's fake fixture): the tool call's own result is
      returned unchanged and no exception propagates when the usage-recording RPC call raises.
- [ ] 3.5 Write real-Postgres tests under the repo's **existing** root-level `tests/integration/`
      (using its `pg_conn` fixture convention, wired into the `compose-health-check` CI job —
      **not** `bloommcp/tests/` with `@pytest.mark.integration`, which means something different
      in bloommcp's own `pyproject.toml` and is excluded from every automated CI job): first
      call from a new identity creates a row (`request_count = 1`); a repeat call increments
      `request_count` and updates `last_seen`/`last_action` while leaving `first_seen` unchanged;
      two anonymous calls collapse into one `identity = 'anonymous'` row; two concurrent
      first-time calls from the same new identity land at `request_count = 2`, not a lost update
      or a constraint error.

## 4. JWT_SECRET lazy validation + fail-closed misconfiguration

- [ ] 4.1a Write failing tests: import and boot succeed with `JWT_SECRET` unset and no incoming
      identity header; a request carrying `X-Bloom-Identity` while `JWT_SECRET` is unset returns
      a `5xx` naming `JWT_SECRET`, and does not fall back to anonymous.
- [ ] 4.1b Implement: `JWT_SECRET` read only inside the header-verification path (not at module
      import or unconditional boot), raising a clear, caller-safe error when needed-but-unset.
- [ ] 4.2 `docker-compose.dev.yml` and `docker-compose.prod.yml`: add `JWT_SECRET: ${JWT_SECRET}`
      to bloommcp's `environment:` block in both files. No changes needed to
      `docker-compose.ci.yml`/`docker-compose.ci-cache.yml` (build-only overlays with no
      environment variables) or CI workflow config — `.env.ci` already populates `JWT_SECRET`
      from the `CI_JWT_SECRET` GitHub Secret; confirm this with a quick CI-config read, don't
      re-derive it from scratch.

## 5. Regression coverage

- [ ] 5.1 Write a test proving no current request path changes: with `X-Bloom-Identity` never
      sent (today's only real traffic shape), every existing bloommcp integration/unit test
      continues to pass unmodified — run the full existing suite as-is after this change lands
      and confirm zero unrelated failures (this is a gate on 6.1, not a separate new assertion).
- [ ] 5.2 Write a test proving the never-forwarded invariant across **every currently-registered
      tool** (iterate the tool registry, not one representative sample): with a valid
      `X-Bloom-Identity` header present, inspect each tool's resulting `get_postgrest_client()`
      call (where applicable) and assert it is still authenticated with `BLOOM_AGENT_KEY`, with
      no trace of the identity token in its arguments/headers.

## 6. Verification

- [ ] 6.1 `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration"` —
      full suite green, including all new tests above.
- [ ] 6.2 `ruff check`, `ruff format --check`, `black --check` clean on all changed files;
      `uv lock --check` clean after promoting `PyJWT` to a direct dependency.
- [ ] 6.3 `openspec validate add-bloommcp-caller-identity --strict` passes.
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
      expect a small, disclosed merge conflict whenever #557 eventually lands.
