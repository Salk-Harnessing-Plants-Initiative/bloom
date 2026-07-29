## 1. Header verification (`bloom_mcp.identity`)

- [ ] 1.1a Write failing tests: valid token → resolved `sub`; absent header → anonymous; expired
      token → rejected; wrong-audience token → rejected; malformed token → rejected; valid
      signature but missing `sub` claim → rejected. Assert byte-for-byte the same
      accept/reject boundary as `langchain/deps.py:get_current_user()` for the overlapping
      cases (same algorithm allow-list, same audience literal).
- [ ] 1.1b Implement: `bloom_mcp/identity.py`, a `verify_identity_header(value: str | None)`
      function mirroring `deps.py:get_current_user()`'s PyJWT call, returning a resolved
      identity, `None` (absent header), or raising a typed rejection the middleware maps to
      `401`. Add `PyJWT` to bloommcp's dependencies.

## 2. Middleware wiring (`server.py` / `build_app()`)

- [ ] 2.1a Write failing tests: a request to the combined surface (`/mcp`) and to each mounted
      section (`/core/mcp`, `/sleap_roots/mcp`, `/phenotyping_segmentation/mcp`) with a valid
      header resolves identity uniformly; with an invalid header, each is rejected uniformly;
      confirm no per-section wiring was needed (one middleware, added once).
- [ ] 2.1b Implement: add the identity-verification middleware to the single `Starlette` app
      `build_app()` returns (`server.py:80-100`), ordered so it runs before requests are routed
      into any `Mount`.
- [ ] 2.2 Write a test confirming the middleware and `BLOOMMCP_API_KEY`'s existing
      `TokenVerifier` (`auth.py`) do not interfere with each other: a request with a valid
      identity header but an invalid/missing bearer token still fails FastMCP's own auth check;
      a request with a valid bearer token but an invalid identity header is rejected by the new
      middleware before reaching FastMCP's check.

## 3. bloommcp_usage table + upsert RPC

- [ ] 3.1 Write the migration (`supabase/migrations/<timestamp>_create_bloommcp_usage.sql`) and
      matching rollback (`supabase/rollbacks/<timestamp>_create_bloommcp_usage_rollback.sql`):
      `bloommcp_usage` table per design.md's Data Model, RLS enabled, `admin_all_bloommcp_usage`
      / `agent_read_bloommcp_usage` / `agent_insert_bloommcp_usage` /
      `agent_update_bloommcp_usage` policies, explicit `GRANT INSERT, UPDATE ... TO bloom_agent`,
      and the `record_bloommcp_usage(p_identity text, p_action text)` upsert function.
- [ ] 3.2a Write failing tests (against a real or faked Postgres, matching this repo's existing
      RPC test conventions): first request from a new identity creates a row
      (`request_count = 1`); a repeat request increments `request_count` and updates
      `last_seen`/`last_action` while leaving `first_seen` unchanged; two anonymous requests
      collapse into one `identity = 'anonymous'` row.
- [ ] 3.2b Implement: call `record_bloommcp_usage` via the existing `call_rpc()` seam
      (`supabase_client.py:134-150`) from the middleware, passing the resolved identity (or
      `"anonymous"`) and the mounted section/path as `last_action`.
- [ ] 3.3 Write a failing test: the middleware still returns the request's normal result when
      the usage-recording RPC raises, and the failure is logged. Implement the
      catch-and-log wrapper around the usage-recording call.

## 4. JWT_SECRET lazy validation + fail-closed misconfiguration

- [ ] 4.1a Write failing tests: import and boot succeed with `JWT_SECRET` unset and no incoming
      identity header; a request carrying `X-Bloom-Identity` while `JWT_SECRET` is unset returns
      a `5xx` naming `JWT_SECRET`, and does not fall back to anonymous.
- [ ] 4.1b Implement: `JWT_SECRET` read only inside the header-verification path (not at module
      import or unconditional boot), raising a clear, caller-safe error when needed-but-unset.
- [ ] 4.2 `docker-compose.dev.yml` and `docker-compose.prod.yml`: add `JWT_SECRET: ${JWT_SECRET}`
      to bloommcp's `environment:` block in both files.

## 5. Regression coverage

- [ ] 5.1 Write a test proving no current request path changes: with `X-Bloom-Identity` never
      sent (today's only real traffic shape), every existing bloommcp integration/unit test
      continues to pass unmodified.
- [ ] 5.2 Write a test proving the never-forwarded invariant: with a valid `X-Bloom-Identity`
      header present, inspect the `get_postgrest_client()` call made by a representative tool
      and assert it is still authenticated with `BLOOM_AGENT_KEY`, with no trace of the identity
      token in its arguments/headers.

## 6. Verification

- [ ] 6.1 `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration"` —
      full suite green, including all new tests above.
- [ ] 6.2 `ruff check`, `ruff format --check`, `black --check` clean on all changed files;
      `uv lock --check` clean after adding `PyJWT`.
- [ ] 6.3 `openspec validate add-bloommcp-caller-identity --strict` passes.
- [ ] 6.4 Security review (this issue is `security`-labeled) before merge, per the issue's own
      stated process expectation.

## 7. Cross-change coordination (process — no code commit)

- [ ] 7.1 Confirm with a maintainer whether the separate langchain-agent issue (per-request MCP
      tool binding so Bloom-web can actually send `X-Bloom-Identity`) has been filed before this
      change merges — proposal.md notes this change is inert in production until that ships,
      and it should not be forgotten as an unfiled follow-up.
