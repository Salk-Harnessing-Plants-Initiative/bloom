## 1. Backend interface + selection (TDD)

- [x] 1.1 (red) Add `bloommcp/tests/test_storage_backend.py`: (a) `BLOOM_STORAGE_BACKEND` unset/`supabase` selects the Supabase backend, `local` selects the local backend, and an unrecognized value raises a clear error naming the bad value + accepted values; (b) selection re-reads the env across the three values in one session via the reset seam (no memoized cross-contamination); (c) a fresh-interpreter `import bloom_mcp.server` with no env reads no env / touches no FS (import purity, mirroring `tests/test_package_baseline.py`).
- [x] 1.2 Add `bloom_mcp/storage_backend.py`: a `StorageBackend` protocol with the **five** ops (`upload_file`, `download_file`, `write_json`, `read_json`, `list_prefix`), a `SupabaseStorageBackend` wrapping today's `supabase.create_client(...).storage` calls, a lazy `active_backend()` selector driven by `BLOOM_STORAGE_BACKEND`, and a test-only `reset_backend_for_tests()`. No env read / no FS touch at import.
- [x] 1.3 Repoint the five `bloom_mcp/supabase_client.py` helper bodies to delegate to `active_backend()`, keeping the module-level names and signatures identical (callers + `fake_supabase_storage` unchanged). Leave `read_input_csv` / `get_postgrest_client` untouched (out of the seam).
- [x] 1.4 (green) Confirm 1.1 passes and the fixture-based suites stay green untouched: run the full `bloommcp/tests/` suite and explicitly `test_store_parity`, `test_supabase_result_store`, `test_supabase_reader`, `test_workflow_persistence`, `test_package_baseline`.

## 2. Local filesystem backend (TDD)

- [x] 2.1 (red) Extend `test_storage_backend.py` for `LocalStorageBackend`: key → `<root>/<key>` mapping; round-trip `upload_file`/`download_file` and `write_json`/`read_json`; `list_prefix` returns bare immediate children (files + first-level dir names, **no trailing slash**), root listing for `""`, and `[]` for a missing prefix (not raise); missing-key read raises; **overwrite** re-write replaces content and `mkdir` is idempotent; **escape guard** rejects `../../etc/passwd`, an absolute-path key, and a symlink-escape via resolved-path (`realpath`) containment with no I/O; **verbatim bytes** (write then read yields identical bytes incl. `\n`, no newline translation); `download_file` copies to dest and leaves/does-not-symlink the backing file; surfaced errors carry no absolute host path.
- [x] 2.2 (red) Add atomicity tests: `write_json`/`upload_file` write via a temp file on the root's filesystem + `os.replace`; assert the temp file is co-located with the target (same dir/filesystem, not `/tmp`); assert an interrupted/partial write never leaves a truncated file (e.g. monkeypatch to fail between write and replace and assert the prior content survives intact).
- [x] 2.3 Implement `LocalStorageBackend` in `storage_backend.py`: join `/`-keys onto the root, resolved-path containment guard, idempotent `mkdir(parents=True, exist_ok=True)`, temp-then-`os.replace` atomic binary writes, `os.listdir`-based `list_prefix` (catch `FileNotFoundError` → `[]`), verbatim `read_bytes`/`write_bytes`, copy-out `download_file`, and non-leaking error surface.
- [x] 2.4 (green) Confirm 2.1–2.2 pass.

## 3. Root resolution + startup validation (TDD)

- [x] 3.1 (red) Add tests: with `local`, root = `BLOOM_STORAGE_LOCAL_ROOT` when set else `BLOOM_OUTPUT_DIR`; a missing/non-writable resolved root and an invalid `BLOOM_STORAGE_BACKEND` both fail fast **through the boot-time validation path** (assert via the validator `server.main()` actually calls, not a standalone helper).
- [x] 3.2 Resolve the local root (`BLOOM_STORAGE_LOCAL_ROOT` → `BLOOM_OUTPUT_DIR`) in `storage_backend.py`; extend **`experiment_utils.validate_env`** (already invoked by `server.main()` at `server.py:123-124`, and it owns the dir existence/writability checks) to validate the `BLOOM_STORAGE_BACKEND` value and, when `local`, the resolved root.
- [x] 3.3 (green) Confirm 3.1 passes.

## 4. Parity, integrity + workflow round-trip (TDD)

- [x] 4.1 (red) Add a parity test (mirroring `result_store/test_store_parity.py`, whose oracle is the in-memory fake): the same write → manifest write/read → `list_prefix` → `get_run("latest")` round-trip yields equivalent observable results **and a byte-identical serialized manifest** across the fake and the local backend on a temp root; assert `/`-separated logical keys on every OS.
- [x] 4.2 (red) Add a workflow round-trip test: run one workflow (e.g. qc) end-to-end with `BLOOM_STORAGE_BACKEND=local` against a temp root; assert real files exist under the root by storage key (outputs + `manifest.json`), the read path reads the run back **through `_resolve_versioned_cleaned`** (the `download_file` cleaned-CSV leg), and `sha256(<on-disk artifact>) == output_sha256` recorded in the manifest.
- [x] 4.3 (red) Add guards: (a) default (unset) commit writes **no** files under a temp local root while the faked Supabase boundary receives the bytes; (b) a `local` run produces nothing at the legacy fallback path `<BLOOM_OUTPUT_DIR>/qc_<stem>/<stem>_cleaned.csv` (disjoint from `<root>/bloommcp_output/…`).
- [x] 4.4 (green) Make 4.1–4.3 pass.

## 5. Config wiring

- [x] 5.1 Add commented `BLOOM_STORAGE_BACKEND` / `BLOOM_STORAGE_LOCAL_ROOT` to the `bloommcp` service env in `docker-compose.dev.yml` (default off; note the root falls back to the already-mounted `BLOOM_OUTPUT_DIR`). Keep them commented (not `${VAR}` references) so `scripts/validate_env.sh` / `tests/unit/test_env_defaults.py` need no `.env.*.defaults` entry.
- [x] 5.2 Confirm `docker-compose.prod.yml` needs no change (default `supabase`; `local` is opt-in even though the prod `BLOOM_OUTPUT_DIR` bind mount is writable under `read_only`); add a one-line comment only if it aids parity review.

## 6. Documentation

- [x] 6.1 Add `bloommcp/docs/storage-backends.md`: default destination (Supabase Storage / MinIO in dev) + how to reach outputs (MinIO console, Supabase Studio, MCP read tools); explicit note that `BLOOM_OUTPUT_DIR` / `BLOOM_USE_LOCAL` do not produce local CSVs by default; the opt-in `local` backend, `BLOOM_STORAGE_LOCAL_ROOT` + fallback, the on-disk layout keyed by storage key, and a **do-not-mix-backends** warning (no cross-store view; version ids can collide).
- [x] 6.2 Add the two new env vars to the bloommcp env-var reference in `_WIKI/BLOOMMCP/README.md` (and cross-link the new doc). Note the sibling relationship to #388 (user-facing upload/download builds on this same seam).

## 7. Validate

- [x] 7.1 `openspec validate add-bloommcp-local-storage-backend --strict`.
- [x] 7.2 Run the full bloommcp unit suite (`uv run pytest` in `bloommcp/`) — all green, no live Supabase.
- [x] 7.3 Run lint/format (ruff + black) on new/changed Python.
- [x] 7.4 Verified the dev behavior via automated equivalents (stronger than a one-off manual docker run): `test_qc_workflow_local_roundtrip_with_hash_equality` drives a real workflow under `BLOOM_STORAGE_BACKEND=local` and asserts real files on disk by key + read-back; `test_default_path_writes_no_local_files` asserts the default path writes none. (A live `make dev-up` container check was not run.)
