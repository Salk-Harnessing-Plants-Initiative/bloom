## Why

Standing up `bloommcp` fully local (`uv run bloom-mcp`, no docker-compose) requires 5 hand-set
env vars where 2 would do (#642): `BLOOM_PLOTS_URL` stays unconditionally required even though
`storage-backends.md`'s own 2-var quick-start omits it (so following that doc literally fails
boot), and `BLOOM_STORAGE_URL`/`BLOOM_PLOTS_URL` must point at something bloommcp itself never
serves — standalone, any generated `output_links[...].url` or plot URL 404s. See design.md
Context for the full history (three related, still-unarchived prior changes touch this exact
seam).

## What Changes

- `LocalStorageBackend.create_signed_url` (`bloommcp/src/bloom_mcp/storage_backend.py`)
  SHALL default `BLOOM_STORAGE_URL`, when unset, to `bloommcp`'s own address (`BLOOMMCP_PUBLIC_URL`
  when set, else `http://localhost:8811`) plus `/output`, instead of raising.
- `bloom_mcp.server.build_app()` SHALL mount `StaticFiles` at `/output` (the resolved local
  storage root) and `/plots` (the resolved plots directory) whenever
  `BLOOM_STORAGE_BACKEND=local`, so those self-served defaults actually resolve when bloommcp
  runs standalone (`uv run bloom-mcp`), not only under `docker-compose.dev.yml`.
- `experiment_utils.PLOTS_URL`, when `BLOOM_PLOTS_URL` is unset and the single `BLOOM_LOCAL_ROOT`
  variable supplies a default (mirroring how `PLOTS_DIR` itself already resolves), SHALL default
  to the same self-served base plus `/plots`, and `BLOOM_PLOTS_URL` SHALL join
  `BLOOM_TRAITS_DIR`/`BLOOM_OUTPUT_DIR`/`BLOOM_PLOTS_DIR` as individually optional in
  `validate_env()`'s required-vars check under that same condition.
- `bloommcp/docs/storage-backends.md` SHALL be corrected to state that the 2-var
  (`BLOOM_STORAGE_BACKEND` + `BLOOM_LOCAL_ROOT`) quick-start actually boots and that
  `output_links`/plot URLs resolve out of the box, replacing the current "bloommcp does not
  itself serve ... over HTTP" language for that configuration (including the trailing "without
  it configured, `create_signed_url` raises" sentence in the same bullet, which becomes false
  too). The corresponding `BLOOM_STORAGE_URL` comment in `docker-compose.dev.yml` making the same
  now-false claim SHALL be corrected too. `BLOOMMCP_PUBLIC_URL`'s existing OAuth-only
  documentation (`bloom_mcp/auth.py`, both compose files) SHALL gain a one-clause cross-reference
  noting its reuse as the self-serve base, so a reader of either doesn't miss the other use.
- **Non-goal:** the granular explicit-override tier (`BLOOM_STORAGE_LOCAL_ROOT` /
  `BLOOM_PLOTS_DIR` set directly, without `BLOOM_LOCAL_ROOT`) keeps `BLOOM_PLOTS_URL`
  unconditionally required at boot, unchanged — only the `BLOOM_STORAGE_URL` default (which is
  never a boot-time requirement) benefits in that tier too, since it is resolved lazily per-call
  rather than validated at boot.
- **Non-goal:** no new auth is added for the `/output`/`/plots` static routes — they are
  unauthenticated, mirroring the existing `/health` route and the already-shipped
  `langchain-agent` `StaticFiles` mounts. Local mode's documented threat model is a solo
  dev/offline convenience.

## Impact

- Affected specs: `bloommcp-storage-backend` (MODIFIED "Signed URL Generation" — see note below),
  `bloommcp-packaging` (extends the still-unarchived `add-bloommcp-local-root` /
  `add-bloommcp-local-experiment-reader` changes' "Lazy Environment Validation" / "Server Boot
  Fail-Fast Preserved" requirements with the `BLOOM_PLOTS_URL` carve-out and the `/plots` mount).
- Affected code: `bloommcp/src/bloom_mcp/storage_backend.py`, `bloommcp/src/bloom_mcp/server.py`,
  `bloommcp/src/bloom_mcp/experiment_utils.py`, `bloommcp/docs/storage-backends.md`, and the
  `BLOOM_STORAGE_URL` comment in `docker-compose.dev.yml` (lines ~181-185, which currently states
  "bloommcp does not run that server itself" — becomes false after this change).
- Affected tests: `bloommcp/tests/test_storage_backend.py`, `bloommcp/tests/test_local_mode.py`,
  `bloommcp/tests/test_identity_middleware.py`, new `bloommcp/tests/test_local_static_mounts.py`.
- **Supersedes one scenario of a still-unarchived change:** `add-bloommcp-signed-url-download`'s
  own delta (`openspec/changes/add-bloommcp-signed-url-download/specs/bloommcp-storage-backend/spec.md`)
  added `Signed URL Generation`'s "Local backend fails closed... raises rather than returning a
  `file://` URI" scenario for an unset `BLOOM_STORAGE_URL`. This change's delta for the same
  requirement (below) replaces that scenario: an unset `BLOOM_STORAGE_URL` is no longer a failure
  mode. Authored as `MODIFIED Requirements` against `Signed URL Generation` (pasting that
  change's full current text) rather than a disconnected `ADDED` requirement, specifically so the
  two deltas don't leave the eventual archived spec self-contradictory.
- **Pre-existing spec drift (not fixed here):** `openspec/specs/bloommcp-packaging/spec.md` and
  `openspec/specs/bloommcp-storage-backend/spec.md` are stale relative to shipped code —
  `add-bloommcp-local-root`, `add-bloommcp-local-experiment-reader`,
  `add-bloommcp-signed-url-download`, `add-bloommcp-signed-url-key-scoping`, and
  `update-dev-local-mode-toggle` are all fully implemented but never archived. This change's
  deltas are written against the _actual current shipped behavior_ (synthesizing those changes'
  still-unarchived deltas), not the stale canonical text — see design.md. Reconciling the
  archive backlog itself is out of scope.
