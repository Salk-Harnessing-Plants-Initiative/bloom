## 1. Scope-parsing helper (`bloom_mcp.identity`)

- [x] 1.1a Write failing tests, in `bloommcp/tests/test_identity.py` (alongside that file's
      existing `verify_identity_header`/`is_valid_identity` pure-function tests — not a new test
      module), for `_oauth_subject_from_scope(scope: dict) -> str | None`: a scope whose `"user"`
      is an `AuthenticatedUser`-shaped object with `access_token.subject` set to a non-empty string
      returns it; a scope with no `"user"` key at all returns `None`; a scope whose `"user"` is not
      an `AuthenticatedUser` instance (e.g. Starlette's own `UnauthenticatedUser`, or a plain
      object) returns `None`; an `AuthenticatedUser` whose `access_token` attribute is itself
      `None` (distinct from an `AccessToken` instance with `subject=None`) returns `None`; a scope
      whose `access_token` has `subject=None` (the `ApiKeyVerifier` shape) returns `None`; a scope
      whose `access_token` has `subject=""` returns `None`. Use
      `mcp.server.auth.middleware.bearer_auth.AuthenticatedUser` and
      `mcp.server.auth.provider.AccessToken` directly (real classes, not hand-rolled stand-ins) so
      this test can't drift from what the real auth stack actually produces.
- [x] 1.1b Implement `_oauth_subject_from_scope` in `identity.py`: `scope.get("user")`, then
      `getattr(user, "access_token", None)`, then `getattr(access_token, "subject", None)`, at each
      step falling through to `None` rather than raising; returns `None` for a falsy/empty subject
      too (`or None`-style normalization), never `""`.

## 2. Middleware wiring — precedence and fallback

- [x] 2.1a Write failing tests (unit-level, mirroring `test_identity_middleware.py`'s existing
      `_DummyApp`-based style): a dummy downstream app that mutates `scope["user"]` to an
      `AuthenticatedUser`-wrapped `AccessToken` with a `subject` set (simulating what FastMCP's
      real auth middleware would have written by the time `await self.app(...)` returns) — no
      `X-Bloom-Identity` header — records that subject, not `anonymous`. A downstream app whose
      `AccessToken` has `subject=None` (simulating `ApiKeyVerifier`) with no header — records
      `anonymous`. A downstream app that sets no `scope["user"]` at all (simulating dev mode / no
      `auth` configured) with no header — records `anonymous`, no error. A request carrying both a
      valid `X-Bloom-Identity` header AND a downstream-set `AccessToken.subject` that differs —
      records the header's identity, not the `AccessToken`'s.
- [x] 2.1b Implement: in `IdentityMiddleware.__call__`, replace the recording call's
      `identity or ANONYMOUS` with `identity or _oauth_subject_from_scope(scope) or ANONYMOUS` —
      read after `await self.app(...)` returns, at the same point recording already happens (no
      change to *when* recording happens or *what* gates it — only which value is recorded).
- [x] 2.2 Write a failing **live subprocess** test (mirroring
      `test_identity_middleware_and_bearer_auth_are_independent_live`'s pattern exactly, for the
      same reason: `bloom_mcp.auth.auth_provider` and its module-level `PUBLIC_URL`/
      `AUTHORIZATION_SERVER` are built once at import time from whatever env is set then, and every
      other test in this session already imports `bloom_mcp.server` without OAuth configured) —
      then make it pass: a fresh interpreter sets `JWT_SECRET`, `BLOOMMCP_PUBLIC_URL`, and
      `BLOOMMCP_OAUTH_AUTHORIZATION_SERVER` before any bloommcp import, monkeypatches
      `bloom_mcp.usage.record_usage_async` to a recorder (plain attribute assignment — no pytest
      fixture available in a subprocess script), builds a real Supabase-shaped OAuth JWT (`sub`,
      `aud=authenticated`, `client_id`, `exp`, signed against `JWT_SECRET`, matching
      `test_auth_oauth.py`'s `_oauth_token()` shape), drives one request through the real
      `server.build_app()` via `TestClient` (entered as a context manager, so FastMCP's lifespan
      runs) with `Authorization: Bearer <token>` and no `X-Bloom-Identity` header, and asserts the
      recorded identity is the token's `sub`. This is the one test in this change that exercises
      the full real chain end to end (`AuthenticationMiddleware` → `BearerAuthBackend` →
      `SupabaseOAuthVerifier` → `scope["user"]` → `IdentityMiddleware`), not a simulation of it.

## 3. Regression coverage

- [x] 3.1 Run the full existing `test_identity.py` + `test_identity_middleware.py` suites unmodified
      except by this change's own additions, confirming every existing header-path test still
      passes — Decision 2's precedence rule and Decision 4's dev-mode handling must not change any
      currently-passing scenario's outcome.
- [x] 3.2 Confirm (by reading, not re-testing — already covered by the separately-closed
      caller-token/DB-authority invariant tests in `test_supabase_client.py`) that this change adds
      no new call site reaching `supabase_client.create_client`/`get_postgrest_client` — the
      `AccessToken` is read only to compute a string label for `record_usage_async`, never passed
      to any DB/Storage helper.

## 4. Verification

- [x] 4.1 `cd bloommcp && uv run --extra test pytest tests/test_identity.py tests/test_identity_middleware.py -v`
      — all green, including the new live subprocess test.
- [x] 4.2 `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and not
      live_smoke"` — matching CI's actual invocation
      ([pr-checks.yml:147](../../../.github/workflows/pr-checks.yml#L147)), not the plainer
      `-m "not integration"` a prior proposal's design.md used (that filter predates the
      `live_smoke` marker's current 16 files under `tests/smoke/`, which need a live dev stack and
      would otherwise fail/hang here for reasons unrelated to this change). Full fast suite, zero
      regressions.
- [x] 4.3 `ruff check`, `ruff format --check` (pinned per `.pre-commit-config.yaml`, currently
      `v0.9.9`), `black --check` (pinned, currently `26.3.1`) clean on `identity.py` and its tests —
      or, more simply, `uvx pre-commit run --files bloommcp/src/bloom_mcp/identity.py bloommcp/tests/test_identity.py bloommcp/tests/test_identity_middleware.py` clean.
- [x] 4.4 `openspec validate add-bloommcp-oauth-usage-attribution --strict` passes.

## 5. Cross-change coordination (process — no code commit)

- [ ] 5.1 Do not merge before PR #613 (`feat/bloommcp-login`) merges to `staging` — this change's
      branch is stacked on it and its new live-subprocess test imports
      `bloom_mcp.auth.SupabaseOAuthVerifier`/`MultiAuth`, which do not exist on `staging` today.
- [ ] 5.2 Do not archive this change independently of `add-bloommcp-caller-identity` — its delta
      targets that change's not-yet-archived `bloommcp-caller-identity` capability directly (see
      proposal.md Impact).
