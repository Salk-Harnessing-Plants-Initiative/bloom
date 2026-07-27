## Why

`SupabaseResultStore.commit` is correct for the single-writer happy path — the manifest is written last, so `latest` never points at a half-written version. But two durability gaps, both inherited from `AnalysisWriter` and already documented as "currently safe" in the persistence design (`bloommcp/docs/2026-06-15-bloom-mcp-phase2-persistence-design.md` §8), should be hardened before bloom-mcp runs anything but a single serial writer:

- **Orphaned remote objects on partial commit failure.** `commit()` uploads each output file in a loop, then writes the manifest last. If upload *N* fails after uploads `1..N-1` succeeded, those objects persist in the `bloommcp-data` bucket unreferenced — `finally` only cleans up the **local** staging dir, never the already-uploaded remote objects, and `next_version_id` never reuses that `v<N>`, so they are unreachable storage litter forever.
- **No manifest compare-and-swap (lost-update).** `version_id` is allocated at `create_run` from `read_manifest()`, and `commit` re-reads + appends without re-checking. Two interleaved `create_run → commit` cycles can allocate the same `v<N>`; the second commit appends a duplicate id, and `get_version('v<N>')` silently returns only the first match.

Tracked from issue #324 (deferred out of the #323 review) and referenced from the persistence design's deferred section (§8).

## What Changes

- On commit failure, best-effort delete every output object already uploaded for the failed run's `version_dir` before re-raising `CommitFailedError` — closing gap A. A delete failure is logged server-side and never masks or replaces the original commit error.
- Add a small bulk-delete primitive to the storage-backend seam (`bloom_mcp.supabase_client` + both `SupabaseStorageBackend`/`LocalStorageBackend` implementations) so the adapter can clean up without reaching into `supabase`/filesystem details directly.
- **New Supabase migration**: grant `bloom_agent` a DELETE policy scoped to `bucket_id = 'bloommcp-data' AND name ~ '^bloommcp_output/'` (never `bloommcp_input/`; a regex anchor, not `LIKE`, to avoid `LIKE`'s unescaped-`_`-is-a-wildcard footgun). Without this, gap A's cleanup 403s against real Supabase on every call and only ever appears closed in tests against the in-memory fake. This **reverses part of a deliberate prior decision** (`20260605000000`: "DELETE intentionally NOT granted... cleanup is admin-only") and needs explicit RLS-migration-owner sign-off, not just OpenSpec approval — the trade-off is disclosed in design.md. Applied and behaviorally verified against a live dev stack: `bloom_agent` deletes under `bloommcp_output/`, and a delete attempt under `bloommcp_input/` is confirmed silently blocked (object still present on read-back).
- Guard `commit` against duplicate/lost-update version ids with a **per-`(experiment, tool_class)` lock around the whole commit critical section**, so two `commit()` calls for the same manifest — including genuinely concurrent ones dispatched by FastMCP's thread pool, not only sequentially-interleaved ones — are fully mutually exclusive: whichever acquires the lock first finishes entirely (upload + manifest write) before the other's pre-upload check even runs. A two-phase manifest re-check (before upload, and again immediately before the write) remains as defense-in-depth, finalizing `version_id`/`version_dir`/every derived key together so they can never disagree with each other. This closes gap B for all same-process concurrency without depending on unverified conditional-write support from the self-hosted storage-api. (An earlier draft of this change omitted the lock, reasoning that sequential interleaving was the only realistic case under bloom-mcp's single-process topology — that reasoning didn't hold: two commits can race within one process via the thread pool, and the two-phase check alone could let a loser's cleanup delete a winner's already-committed bytes.)
- Document the residual limitation precisely: the lock guarantees a committed entry's `id`/`version_dir`/`output_keys` are always mutually consistent with the bytes stored for every same-process race, but does not provide cross-process atomic CAS for genuinely multi-instance writers. Same trigger as the design doc already carries: bloom-mcp scaling past a single instance/process.
- No change to the `ResultStore`/`ports.py` public surface or the manifest schema. `FakeResultStore` gets the identical per-key lock (and the collision-loop fix) for parity — tracked as issue #325's scope, implemented as commits directly on this same PR rather than a separate later one.

## Impact

- Affected specs: `bloommcp-result-store` (MODIFIED: `SupabaseResultStore Adapter`; ADDED: `Manifest Write Guards Against Duplicate Version IDs`)
- Affected code:
  - `bloommcp/src/bloom_mcp/result_store/supabase_store.py` (`commit`, plus the per-key lock)
  - `bloommcp/src/bloom_mcp/result_store/fake_store.py` (mirrored lock + collision-loop fix, via #325)
  - `bloommcp/src/bloom_mcp/supabase_client.py` (new bulk-delete helper)
  - `bloommcp/src/bloom_mcp/storage_backend.py` (`StorageBackend` Protocol + both concrete implementations gain delete support)
  - `bloommcp/tests/conftest.py` (`fake_supabase_storage`/`_InMemoryObjectStore` gain delete support)
  - `bloommcp/tests/result_store/test_supabase_result_store.py` (new failure-mode tests)
  - `bloommcp/tests/result_store/test_store_parity.py` (concurrent-commit + failure-boundary parity tests, via #325)
  - `supabase/migrations/` (new scoped DELETE grant — see above)
- Not affected: `ResultStore` port signatures, manifest schema version (stays v3). `FakeResultStore`'s happy-path behavior is unchanged; its collision/failure-injection parity (issue #325) is a distinct OpenSpec change (`update-bloommcp-resultstore-fake-parity`) delivered in this same PR.
