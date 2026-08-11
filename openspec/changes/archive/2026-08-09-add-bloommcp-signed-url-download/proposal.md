## Why

Every bloommcp analysis tool returns only metadata (`run_ref`, `outputs: {name: key}`)
pointing into the private `bloommcp-data` Supabase bucket — there is no way for a user to
actually retrieve the CSV/JSON bytes an analysis produced. Plots reach the user today (via
the separate `BLOOM_PLOTS_URL`-served legacy path); CSV/JSON outputs have no equivalent, so
a user can see the chart but never the numbers behind it. `StorageBackend` (#389) shipped
the seam this needs and has sat unused since 2026-07-06. This is the small, high-value,
no-open-design-questions third of #388 ("return output CSVs") that #388's own review already
scoped out as the place to start.

## What Changes

- Add `create_signed_url(key: str, expires_in: int) -> str` to the `StorageBackend` protocol
  (`storage_backend.py`), both adapters (`SupabaseStorageBackend`, `LocalStorageBackend`), and
  a matching `bloom_mcp.supabase_client.create_signed_url` re-export — the module's existing
  six-helper delegate-to-active-backend pattern becomes seven.
- Rewrite the Supabase backend's signed URL from bloommcp's internal `SUPABASE_URL`
  (`http://kong:8000` in prod/staging) to a public base via a new `BLOOM_PUBLIC_SUPABASE_URL`
  env var — the same internal-host-rewrite pattern `services/workflows/video.py` and
  `web/lib/supabase/storage-url.ts` already ship for their own signed URLs. Without this, every
  signed URL bloommcp returned in prod/staging would be unreachable outside the Docker network.
- Extend `hash_outputs` (`result_store/_artifacts.py`) to also return each artifact's byte
  size alongside its existing SHA-256 (the bytes are already read into memory to hash).
- Add an `OutputLink` Pydantic model (`key`, `url`, `sha256`, `size_bytes`) to
  `bloom_mcp.contract.models`; add an **additive** `output_links: dict[str, OutputLink]` field
  to `RunLinks` — the existing `outputs: dict[str, str]` field is retained unchanged.
- Populate `output_links` inside `ResultStore.commit()` only (both `SupabaseResultStore` and
  `FakeResultStore`) — **not** `get_run`/`list_runs` — so every consumer tool's own freshly
  committed result carries a signed URL + hash + size per output, without eagerly re-signing
  URLs for potentially many historical versions on every `list_existing_analyses` call.
- Wire `output_links=stored.output_links` into all 8 tool result models that persist via
  `ResultStore` (`qc_clean`, `qc_inspect`, `pca_analysis`, `remove_outliers`,
  `descriptive_stats`, `cross_experiment_correlations`, `umap_analysis`, `clustering`) — a
  one-line, mechanical addition per tool.
- Decide and document a concrete inline-vs-link size threshold (100 KB / 102,400 bytes),
  backed by a spec requirement (extending `bloommcp-storage-backend`'s existing "Documentation
  of Output Destinations" requirement); expose `size_bytes` per output so a caller can apply the
  decision itself. Does **not** add an inline-content return path — see Non-Goals.
- Document the new `BLOOM_STORAGE_URL` (local-backend served-URL base) and
  `BLOOM_PUBLIC_SUPABASE_URL` (prod/staging signed-URL host rewrite) env vars in
  `bloommcp/docs/storage-backends.md`, and wire both into `docker-compose.dev.yml` /
  `docker-compose.prod.yml`'s `bloommcp` service blocks (mirroring how `BLOOM_PLOTS_URL` and
  `WORKFLOWS_PUBLIC_SUPABASE_URL` are already wired there).
- Add `create_signed_url` to `bloommcp/tests/conftest.py`'s `_InMemoryObjectStore` (the
  `fake_supabase_storage` fixture backing the real `SupabaseResultStore` in ~12 existing test
  files) so those tests keep working once `commit()` starts calling the real seven-helper set.

## Impact

- Affected specs: `bloommcp-storage-backend`, `bloommcp-result-store`, `bloommcp-tool-contract`
- Affected code: `bloom_mcp/storage_backend.py`, `bloom_mcp/supabase_client.py`,
  `bloom_mcp/contract/models.py`, `bloom_mcp/contract/__init__.py`,
  `bloom_mcp/result_store/{ports,_artifacts,supabase_store,fake_store}.py`, all 8
  `sections/sleap_roots/analysis/*.py` tool files, `bloommcp/tests/conftest.py`,
  `bloommcp/docs/storage-backends.md`, `bloommcp/docs/roadmap.md`,
  `docker-compose.dev.yml`, `docker-compose.prod.yml`
- Unaffected: `manifest.json` schema and bytes (no version bump — signed URLs are ephemeral
  and are never persisted into the manifest), `get_run`/`list_runs`/`list_existing_analyses`
  (deliberately out of scope — see Non-Goals), the `supabase`/`storage3` dependency pin
  (`storage3==2.31.0` already implements `create_signed_url`, confirmed by reading the
  installed package — no version bump needed)
- Closes #581

## Non-Goals

- Ad-hoc upload, ephemeral inline-content analysis, a file explorer, and cross-backend
  lineage (#388's other two-thirds) — tracked separately, per #581's own scope.
- Actually inlining small-output content in a tool's response. Every consumer tool's
  docstring already documents a deliberate "never return matrices/scores/loadings inline"
  contract (e.g. `pca_analysis.py`: "returns a variance summary + links (never the
  score/loadings matrices inline)"); reversing that is bigger, orthogonal scope that belongs
  with #388's still-open upload/explorer thirds, not here.
- Signed URLs for historical runs surfaced via `get_run`/`list_runs`/`list_existing_analyses`
  — only the run a tool call just committed gets a signed URL in this change.
- Standing up an HTTP static file server for the local backend's storage root — this change
  only builds the URL string from an operator-supplied `BLOOM_STORAGE_URL`; serving that root
  is separate infra, exactly as `BLOOM_PLOTS_URL` already assumes for `PLOTS_DIR` today.
- Unifying the 3-of-8 result models (`QCCleanResult`, `QCInspectResult`, `ClusteringResult`)
  that duplicate `RunLinks`'s four fields inline instead of inheriting it — pre-existing
  inconsistency, orthogonal to this change's goal.
- Consolidating this change's internal-host-rewrite logic with the two other, independent
  implementations of the identical pattern already shipped in `services/workflows/video.py` and
  `web/lib/supabase/storage-url.ts` — a real DRY observation, but a cross-service/cross-language
  refactor orthogonal to shipping this capability.
