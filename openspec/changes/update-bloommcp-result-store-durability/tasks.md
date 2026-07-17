## 0. Supabase migration — scoped DELETE grant (backs #324 gap A)

- [x] 0.1 Add `supabase/migrations/<timestamp>_grant_bloommcp_agent_output_delete.sql`: `CREATE POLICY agent_delete_bloommcp_output ON storage.objects FOR DELETE TO bloom_agent USING (bucket_id = 'bloommcp-data' AND name LIKE 'bloommcp_output/%')` + `GRANT DELETE ON storage.objects TO bloom_agent`, idempotent (`DROP POLICY IF EXISTS` first) matching `20260605000000`'s pattern. Flag explicitly for RLS-migration-owner review — see design.md's "Disclosed risk."
- [ ] 0.2 Apply via `make migrate-local` in dev; confirm `bloom_agent` can delete a test object under `bloommcp_output/` and still cannot delete under `bloommcp_input/` or any other bucket. **Not run** — no local Docker/Supabase stack available in the implementation environment; needs to be run before merge.

## 1. Bulk-delete storage primitive + test-boundary support

- [x] 1.1 Add `delete_files(keys: list[str]) -> None` to `bloom_mcp.supabase_client`, delegating to `active_backend()` like the existing five helpers.
- [x] 1.2 Implement it on `SupabaseStorageBackend` via `storage3`'s `Bucket.remove(paths)`.
- [x] 1.3 Implement it on `LocalStorageBackend` via `Path.unlink(missing_ok=True)` under the existing root-containment guard (reuse `_resolve`); missing keys are a no-op, not an error.
- [x] 1.4 Add `delete_files` to the `StorageBackend` Protocol in `storage_backend.py`, and update its docstring ("five object-storage operations" → six).
- [x] 1.5 Extend `bloommcp/tests/conftest.py`'s `_InMemoryObjectStore` with a `delete_files` method and add `"delete_files"` to `fake_supabase_storage`'s monkeypatch list.
- [x] 1.6 Unit tests for both concrete `StorageBackend` implementations (bulk delete of existing + already-absent keys) — `test_local_delete_files_removes_existing_and_ignores_missing`, `test_local_delete_files_empty_list_is_noop`, `test_supabase_backend_delete_files_calls_bucket_remove`, `test_supabase_backend_delete_files_empty_list_skips_client` in `tests/test_storage_backend.py`.

## 2. Orphan-object cleanup on commit failure (#324 gap A)

- [x] 2.1 In `SupabaseResultStore.commit`, track which output keys were successfully uploaded before any exception.
- [x] 2.2 On exception (upload or manifest write), best-effort `delete_files` the tracked keys via `_cleanup_uploaded`; catch and log (server-side only) any delete failure without altering the raised `CommitFailedError`.
- [x] 2.3 Test: `test_commit_failure_cleans_up_orphaned_objects_from_partial_upload`.
- [x] 2.4 Test: `test_cleanup_failure_does_not_mask_original_error` (uses `caplog`).

## 3. Manifest duplicate-id guard on commit (#324 gap B)

- [x] 3.1 Pre-upload collision check + bounded reallocation (`_MAX_ID_ATTEMPTS = 3`), re-reading the manifest fresh on each attempt.
- [x] 3.2 Finalized `version_id`/`version_dir` used for `key_for`, hashing, and the upload loop — never rewritten independently afterward.
- [x] 3.3 Pre-write freshness check; on late collision, best-effort cleanup + `CommitFailedError` rather than overwriting/relabeling.
- [x] 3.4 Test: `test_interleaved_commits_get_distinct_ids_with_consistent_provenance`.
- [x] 3.5 Test: `test_retry_exhaustion_before_upload_raises_with_no_uploads`.
- [x] 3.6 Test: `test_prewrite_collision_cleans_up_and_retry_succeeds`.
- [x] 3.7 Test: `test_noncolliding_commit_reads_manifest_twice_with_no_reallocation`.

## 4. Spec + docs

- [ ] 4.1 Update `openspec/specs/bloommcp-result-store/spec.md` per this change's deltas after archival. **Deferred to Stage 3** (archival, a separate PR after deployment) per OpenSpec convention — not done in this implementation pass.
- [x] 4.2 Updated the persistence design doc's §8 deferred-item bullet for #324 to "adapter-side guard + scoped DELETE grant, shipped," with the residual multi-instance limitation documented.
- [x] 4.3 Same §8 bullet notes this change is a prerequisite for #325.

## 5. Validation

- [x] 5.1 `openspec validate update-bloommcp-result-store-durability --strict` passes.
- [x] 5.2 Full `bloommcp` unit test suite passes (513 passed), including the 6 new tests above.
- [ ] 5.3 `make bloommcp-smoke` (live Supabase smoke) — **not run**: no Docker/dev stack available in the implementation environment. Needs to be run (after `make dev-up`, `make migrate-local` including the new migration) before merge.
