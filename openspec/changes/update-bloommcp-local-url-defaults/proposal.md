## Why

Standing up `bloommcp` fully local (`uv run bloom-mcp`, no docker-compose) requires 5 hand-set
env vars where 2 would do (#642): `BLOOM_PLOTS_URL` stays unconditionally required even though
`storage-backends.md`'s own 2-var quick-start omits it, and — per the issue's follow-up
discussion — the local backend should not need a signed/served URL for output artifacts at all,
since the caller already has direct filesystem access to a file bloommcp just wrote. See
design.md Context for the full history, including a design pivot mid-implementation (this
proposal originally self-served `BLOOM_STORAGE_URL` over HTTP; the issue author's follow-up
comment redirected outputs to a direct-path model instead — see design.md's "Revision History").

## What Changes

- `bloom_mcp.result_store._artifacts.build_output_links` and `bloom_mcp.contract.models.OutputLink`
  SHALL support a `path_for` alternative to `url_for`: `OutputLink` gains an `Optional[str] path`
  field alongside a now-`Optional[str] url`; exactly one of the two is populated per link.
- `SupabaseResultStore.commit()` SHALL use `path_for` (never `url_for`/`create_signed_url`) for
  every output link when `BLOOM_STORAGE_BACKEND=local`, resolving each key to its absolute
  filesystem path under `storage_backend.local_output_root()`. The default (Supabase) backend is
  unaffected — it continues to use `url_for`/`create_signed_url` exactly as before.
  `LocalStorageBackend.create_signed_url` is unchanged (still requires `BLOOM_STORAGE_URL`,
  raising when unset) — it is simply no longer called by this pipeline; it remains available only
  for an operator who deliberately wants a real served URL from their own external server.
- `experiment_utils.PLOTS_URL`, when `BLOOM_PLOTS_URL` is unset and the single `BLOOM_LOCAL_ROOT`
  variable supplies a default (mirroring how `PLOTS_DIR` itself already resolves), SHALL default
  to `storage_backend.self_serve_base_url()` plus `/plots`, and `BLOOM_PLOTS_URL` SHALL join
  `BLOOM_TRAITS_DIR`/`BLOOM_OUTPUT_DIR`/`BLOOM_PLOTS_DIR` as individually optional in
  `validate_env()`'s required-vars check under that same condition. `bloom_mcp.server.build_app()`
  SHALL mount `StaticFiles` at `/plots` (the resolved plots directory) whenever
  `BLOOM_STORAGE_BACKEND=local`, so that self-served default actually resolves standalone. Plots
  are unaffected by the output-path pivot above (the issue's follow-up comment did not ask for
  this, and plots have no direct-filesystem-access alternative today — see design.md Decision 2).
- `bloommcp/docs/storage-backends.md` SHALL be corrected to describe outputs as resolving via a
  direct filesystem path for the local backend (no URL, no self-serving needed) and plots as
  resolving via the self-served `/plots` default — two different mechanisms for two different
  artifact kinds, stated as such. `BLOOMMCP_PUBLIC_URL`'s existing OAuth-only documentation
  (`bloom_mcp/auth.py`, `docker-compose.dev.yml`) SHALL gain a one-clause cross-reference noting
  its reuse as the plots self-serve base.
- **Non-goal:** no new auth is added for the `/plots` static route — unauthenticated, mirroring
  the existing `/health` route and the already-shipped `langchain-agent` `StaticFiles` mount.
- **Non-goal:** the granular explicit-override tier (`BLOOM_PLOTS_DIR` set directly, without
  `BLOOM_LOCAL_ROOT`) keeps `BLOOM_PLOTS_URL` unconditionally required at boot, unchanged — the
  output-path behavior is unaffected either way, since it never depends on any URL var.

## Impact

- Affected specs: `bloommcp-result-store` (MODIFIED "Per-Output Signed Links And Size At Commit"
  — local backend surfaces `path` instead of `url`), `bloommcp-packaging` (extends the
  still-unarchived `add-bloommcp-local-root` / `add-bloommcp-local-experiment-reader` changes'
  "Lazy Environment Validation" / "Server Boot Fail-Fast Preserved" requirements with the
  `BLOOM_PLOTS_URL` carve-out and the `/plots` mount). No delta against `bloommcp-storage-backend`
  in this revision — `create_signed_url` itself is unchanged from its still-unarchived
  `add-bloommcp-signed-url-download` contract.
- Affected code: `bloommcp/src/bloom_mcp/contract/models.py`,
  `bloommcp/src/bloom_mcp/result_store/_artifacts.py`,
  `bloommcp/src/bloom_mcp/result_store/supabase_store.py`,
  `bloommcp/src/bloom_mcp/experiment_utils.py`, `bloommcp/src/bloom_mcp/server.py`,
  `bloommcp/docs/storage-backends.md`, `bloommcp/src/bloom_mcp/auth.py` (comment),
  `docker-compose.dev.yml` (comments).
- Affected tests: `bloommcp/tests/result_store/test_artifacts.py`,
  `bloommcp/tests/result_store/test_store_parity.py`, `bloommcp/tests/test_storage_backend.py`,
  `bloommcp/tests/test_local_mode.py`, `bloommcp/tests/test_identity_middleware.py`,
  `bloommcp/tests/test_local_static_mounts.py`.
- **Pre-existing spec drift (not fixed here):** `openspec/specs/bloommcp-packaging/spec.md` and
  `openspec/specs/bloommcp-result-store/spec.md` are stale relative to shipped code —
  `add-bloommcp-local-root`, `add-bloommcp-local-experiment-reader`,
  `add-bloommcp-signed-url-download`, `add-bloommcp-signed-url-key-scoping`, and
  `update-dev-local-mode-toggle` are all fully implemented but never archived. This change's
  deltas are written against the _actual current shipped behavior_ (synthesizing those changes'
  still-unarchived deltas), not the stale canonical text — see design.md. Reconciling the
  archive backlog itself is out of scope.
