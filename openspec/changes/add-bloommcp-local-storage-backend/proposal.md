## Why

bloommcp has **no way to write analysis outputs as local files**. Every output
(CSV, JSON manifest, PNG) is uploaded to Supabase Storage: `AnalysisWriter.commit`
/ `SupabaseResultStore.commit` loop over staged files and call `upload_file`
([writer.py:100-103](../../../bloommcp/src/bloom_mcp/storage/writer.py#L100-L103),
[supabase_store.py:106-147](../../../bloommcp/src/bloom_mcp/result_store/supabase_store.py#L106-L147)),
and there is no filesystem branch anywhere in the storage layer. The env vars
people reach for don't help: `BLOOM_OUTPUT_DIR` post-migration only feeds a
startup dir-existence check and a legacy read fallback
([experiment_utils.py:24-52,344-356](../../../bloommcp/src/bloom_mcp/experiment_utils.py#L24-L52)) —
nothing writes new outputs there — and `BLOOM_USE_LOCAL` is dead/commented-out and
was only ever about CLI login credentials. In local dev the bytes live inside
MinIO's object store, not as files under the mounted `./bloommcp/data/ANALYSIS_OUTPUT`,
so that folder stays empty and the outputs are invisible as files. This blocks
quick local inspection, offline runs, and confuses anyone reading the env-var names.

The write and output-read paths already funnel through **one narrow seam** — the
five object-storage helpers in `bloom_mcp.supabase_client` (`upload_file`,
`download_file`, `write_json`, `read_json`, `list_prefix`). The unit suite already
substitutes exactly these five with an in-memory fake
([conftest.py `fake_supabase_storage`](../../../bloommcp/tests/conftest.py)), which
proves the interface is narrow and swappable. A local-filesystem implementation can
sit behind the same seam, selected by an env var, with the default path unchanged.

## What Changes

- Introduce a small **storage-backend interface** over the five object-storage
  helpers, with two implementations: `supabase` (the current behavior) and `local`
  (real files under a root dir; storage keys → relative paths).
- Select the backend from **`BLOOM_STORAGE_BACKEND`** (default `supabase`); an
  unrecognized value fails fast at startup with a clear message. Selection is resolved
  lazily (never at import — import stays side-effect-free), and the module-level
  `supabase_client` helper names stay stable, so every caller (`writer`, `manifest`,
  `supabase_store`, `experiment_utils`) and the existing test fake are unchanged.
- Add **`BLOOM_STORAGE_LOCAL_ROOT`** for the local root; when unset it falls back to
  the already-required, already-mounted `BLOOM_OUTPUT_DIR`, so `BLOOM_STORAGE_BACKEND=local`
  works in dev with no extra config and finally populates `./bloommcp/data/ANALYSIS_OUTPUT`.
- With `local`, an analysis run writes CSV/JSON/PNG as real files laid out by storage
  key, and all output-read paths (manifest resolution, versioned-cleaned lookup, MCP
  read tools) resolve against those files.
- **Preserve the object-store's implicit integrity guarantees on the filesystem**: the
  local backend writes the manifest (and every object) atomically (temp-then-rename on
  the root's filesystem), copies bytes verbatim (no newline/encoding translation), keeps
  recorded `output_sha256` equal to the bytes on disk, produces a byte-identical
  provenance manifest across backends, and redacts absolute host paths from agent-facing
  errors — matching what the Supabase path provides for free.
- **Docs**: document where outputs actually go by default (Supabase Storage, backed by
  MinIO in dev) and how to reach them; clarify that `BLOOM_OUTPUT_DIR` / `BLOOM_USE_LOCAL`
  do **not** produce local CSVs by default; document the opt-in `local` backend,
  `BLOOM_STORAGE_LOCAL_ROOT`, and the **do-not-mix-backends** caveat.
- Tests cover the local backend behind the same interface the fake already uses:
  supabase-fake vs local **parity** (asserting a byte-identical manifest), a workflow
  run end-to-end under `local` (asserting real files on disk, read-back, and
  hash-equality), and an explicit guard that the **default path writes no local files**.

## Impact

- **Affected specs:** ADDs a new `bloommcp-storage-backend` capability. No change to
  `bloommcp-result-store` / `bloommcp-experiment-read` behavior — the local backend is
  transparent beneath the `ResultStore` / `ExperimentReader` adapters, the default stays
  `supabase`, and raw-experiment-input reads (local `BLOOM_TRAITS_DIR`) and `read_input_csv`
  (PostgREST) are deliberately outside the swapped seam, so the reader's input contract is
  untouched.
- **Affected code:**
  - `bloommcp/src/bloom_mcp/supabase_client.py` — the five helpers delegate to the active backend (names + signatures unchanged).
  - New `bloommcp/src/bloom_mcp/storage_backend.py` — interface + `SupabaseStorageBackend` + `LocalStorageBackend` + lazy selection (import-time pure).
  - `bloommcp/src/bloom_mcp/experiment_utils.py` (`validate_env`, called by `server.main()`) — validate `BLOOM_STORAGE_BACKEND` and, when `local`, the resolved root, at boot.
  - `docker-compose.dev.yml` — document the new opt-in vars (commented; default off). These are commented, not `${VAR}` references, so the env-parity CI check (`scripts/validate_env.sh`, `tests/unit/test_env_defaults.py`) is unaffected and needs no `.env.*.defaults` entry.
  - Docs: new `bloommcp/docs/storage-backends.md`; env-var note in `_WIKI/BLOOMMCP/README.md`.
  - Tests under `bloommcp/tests/` — backend unit + parity + a `local` workflow round-trip; default-no-local-files guard; startup-validation guard through the boot path.

## Scope / Non-Goals

- **Default is unchanged**: production/staging stay on Supabase Storage (`prod` is
  `read_only` with `BLOOM_OUTPUT_DIR` on a writable bind mount, but `local` is opt-in and
  off by default, so prod is untouched). Manifest and versioning semantics are identical —
  only the bytes' destination changes.
- **Not** a flag on the writer — it's a backend selection at the object-storage boundary.
- **Out of scope:** PostgREST/table reads via `get_postgrest_client` (that is the
  database, not object storage; `read_input_csv` rides this client and is likewise
  untouched) and the deprecated raw-input reads from local `BLOOM_TRAITS_DIR`. Only the
  five object-storage helpers are swapped.
- **Not a migration and not a mixed-mode store:** a backend is not a data migration; the
  two stores are independent catalogs. Flipping `BLOOM_STORAGE_BACKEND` mid-experiment would
  split version history across stores (re-allocating `v1`, orphaning the other store's
  lineage). This is a documented non-goal, warned in the docs — not silently relied upon.
- **Related but separate:** #388 (user-facing upload/download of bloommcp files) will build
  signed-URL downloads on this same `supabase_client.py` seam. This change deliberately does
  **not** add that user-facing surface; it only reshapes the boundary #388 will build on.
