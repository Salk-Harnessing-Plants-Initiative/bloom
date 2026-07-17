## Why

`SupabaseResultStore.commit` is correct for the single-writer happy path — the manifest is written last, so `latest` never points at a half-written version. But two durability gaps, both inherited from `AnalysisWriter` and already documented as "currently safe" in the persistence design (`bloommcp/docs/2026-06-15-bloom-mcp-phase2-persistence-design.md` §8), should be hardened before bloom-mcp runs anything but a single serial writer:

- **Orphaned remote objects on partial commit failure.** `commit()` uploads each output file in a loop, then writes the manifest last. If upload *N* fails after uploads `1..N-1` succeeded, those objects persist in the `bloommcp-data` bucket unreferenced — `finally` only cleans up the **local** staging dir, never the already-uploaded remote objects, and `next_version_id` never reuses that `v<N>`, so they are unreachable storage litter forever.
- **No manifest compare-and-swap (lost-update).** `version_id` is allocated at `create_run` from `read_manifest()`, and `commit` re-reads + appends without re-checking. Two interleaved `create_run → commit` cycles can allocate the same `v<N>`; the second commit appends a duplicate id, and `get_version('v<N>')` silently returns only the first match.

Tracked from issue #324 (deferred out of the #323 review) and referenced from the persistence design's deferred section (§8).

## What Changes

- On commit failure, best-effort delete every output object already uploaded for the failed run's `version_dir` before re-raising `CommitFailedError` — closing gap A. A delete failure is logged server-side and never masks or replaces the original commit error.
- Add a small bulk-delete primitive to the storage-backend seam (`bloom_mcp.supabase_client` + both `SupabaseStorageBackend`/`LocalStorageBackend` implementations) so the adapter can clean up without reaching into `supabase`/filesystem details directly.
- **New Supabase migration**: grant `bloom_agent` a DELETE policy scoped to `bucket_id = 'bloommcp-data' AND name LIKE 'bloommcp_output/%'` (never `bloommcp_input/`). Without this, gap A's cleanup 403s against real Supabase on every call and only ever appears closed in tests against the in-memory fake. This **reverses part of a deliberate prior decision** (`20260605000000`: "DELETE intentionally NOT granted... cleanup is admin-only") and needs explicit RLS-migration-owner sign-off, not just OpenSpec approval — the trade-off is disclosed in design.md.
- Guard `commit` against duplicate/lost-update version ids by finalizing `version_id`, `version_dir`, and every derived object key **together, before any upload**: re-read the manifest fresh at the start of `commit`, re-allocate (id + version_dir together) on collision, bounded, before touching storage; then re-read once more immediately before the manifest write, treating a late collision as an ordinary retryable commit failure rather than relabeling an already-uploaded entry. This closes gap B for sequential interleaved commits — the realistic shape under bloom-mcp's single-process topology — without depending on unverified conditional-write support from the self-hosted storage-api.
- Document the residual limitation precisely: the guard guarantees a committed entry's `id`/`version_dir`/`output_keys` are always mutually consistent with the bytes stored, but does not provide storage-level atomic CAS for genuinely *simultaneous* multi-instance writers. Same trigger as the design doc already carries: bloom-mcp scaling past a single instance.
- No change to the `ResultStore`/`ports.py` public surface or the manifest schema. `FakeResultStore`'s happy-path behavior is unchanged; this proposal deliberately does not give it matching collision/failure-injection behavior — that is issue #325's scope ("fake↔adapter failure-injection parity... once #324 lands"), which this change is a prerequisite for.

## Impact

- Affected specs: `bloommcp-result-store` (MODIFIED: `SupabaseResultStore Adapter`; ADDED: `Manifest Write Guards Against Duplicate Version IDs`)
- Affected code:
  - `bloommcp/src/bloom_mcp/result_store/supabase_store.py` (`commit`)
  - `bloommcp/src/bloom_mcp/supabase_client.py` (new bulk-delete helper)
  - `bloommcp/src/bloom_mcp/storage_backend.py` (`StorageBackend` Protocol + both concrete implementations gain delete support)
  - `bloommcp/tests/conftest.py` (`fake_supabase_storage`/`_InMemoryObjectStore` gain delete support)
  - `bloommcp/tests/result_store/test_supabase_result_store.py` (new failure-mode tests)
  - `supabase/migrations/` (new scoped DELETE grant — see above)
- Not affected: `FakeResultStore` observable happy-path behavior, `ResultStore` port signatures, manifest schema version (stays v3).
