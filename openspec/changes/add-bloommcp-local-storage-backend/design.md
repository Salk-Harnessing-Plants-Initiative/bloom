## Context

Issue #386. bloommcp's write path and output-read path both funnel through a single
narrow module, `bloom_mcp.supabase_client`, which exposes five object-storage helpers —
and the unit suite already substitutes exactly these five behind `fake_supabase_storage`
(`bloommcp/tests/conftest.py:86-95`):

| Helper | Used by |
| --- | --- |
| `upload_file(key, path)` | `AnalysisWriter.commit`, `SupabaseResultStore.commit` |
| `download_file(key, path)` | `experiment_utils._resolve_versioned_cleaned` (versioned-cleaned read) |
| `write_json(key, payload)` | `storage.manifest.write_manifest` |
| `read_json(key)` | `storage.manifest.read_manifest` |
| `list_prefix(prefix)` | `storage.manifest.read_manifest`, `_resolve_versioned_cleaned` |

Higher layers depend only on the `ResultStore` / `ExperimentReader` ports — never on
`supabase` directly. So the boundary is proven swappable; this change adds a second
concrete implementation and a selector.

Three adjacent data paths are deliberately **out of scope**: `get_postgrest_client()`
(PostgREST/table reads — the database, not object storage); `read_input_csv()`, which —
despite living in `supabase_client` — rides the **PostgREST client**
(`supabase_client.py:126`, `get_postgrest_client().storage…`), is **not** one of the five
faked helpers, and has **no production caller** in `src/`; and raw-experiment-CSV reads
from the local `BLOOM_TRAITS_DIR` (already local files, read directly in
`load_experiment_data`, not through the storage boundary). The seam this change swaps is
exactly the five faked object-storage helpers — no more, no less.

## Goals / Non-Goals

- **Goals**
  - An opt-in `local` backend that writes/reads bloommcp object storage as real files,
    preserving the object store's *implicit* integrity guarantees on a POSIX filesystem
    (atomic manifest, verbatim bytes, hash-truthful, non-leaking errors).
  - Zero behavior change when the backend is unset or `supabase` (byte-for-byte).
  - No churn for callers or the existing test fake — the `supabase_client` helper
    names stay put; only their bodies delegate.
  - Docs that state the true output destination and de-mystify `BLOOM_OUTPUT_DIR`.
- **Non-Goals**
  - Changing the production default, the manifest schema, or versioning semantics.
  - Swapping PostgREST/table access, `read_input_csv`, or the deprecated `BLOOM_TRAITS_DIR` raw read.
  - A new higher-level port — selection lives at the object-storage seam, below the
    `ResultStore` / `ExperimentReader` adapters, so both use the chosen backend transparently.
  - Cross-store reconciliation or migration. A backend is not a migration; mixing backends
    for one experiment splits its history (see Risks).

## Decisions

- **Decision: put the seam at `bloom_mcp.supabase_client`, not at the ports.**
  A new module `bloom_mcp/storage_backend.py` defines a `StorageBackend` protocol with the
  five operations and two implementations (`SupabaseStorageBackend` wrapping today's client
  calls; `LocalStorageBackend` mapping keys to paths). `supabase_client` keeps its
  module-level functions as the public surface — each becomes a thin delegate to the
  process's active backend. This preserves every import site and the `fake_supabase_storage`
  monkeypatch (it patches the module-level names, which still exist as wrapper objects).
  - *Verified:* the `from bloom_mcp.supabase_client import upload_file` callers still route
    to the newly-selected backend, because the imported wrapper object's identity is stable
    and only its internal dispatch target changes. The fake still overwrites those wrapper
    objects wholesale in both `supabase_client` and `manifest`, so it is undisturbed.
  - *Alternative considered:* add `LocalResultStore` / `LocalReader` adapters at the
    port layer. Rejected — it duplicates versioning/manifest logic across adapters,
    and the issue explicitly wants a backend at the storage boundary, not per-writer.

- **Decision: `BLOOM_STORAGE_BACKEND` selects; default `supabase`; unknown → fail fast;
  resolution is lazy and import-pure.**
  The active backend is resolved lazily on first use, **never at import**, and the resolver
  reads no env and touches no filesystem at import time — `import bloom_mcp.server` with no
  env must stay clean (enforced by `tests/test_package_baseline.py`). The value is validated
  at boot in `experiment_utils.validate_env` (which `server.main()` already calls at
  `server.py:123-124`) so a typo like `locel` fails at boot, not mid-run. A small
  test-only reset seam (e.g. `reset_backend_for_tests()` / `functools.cache` + `cache_clear`)
  lets the selection tests exercise `supabase` / `local` / invalid within one session without
  cross-contamination.

- **Decision: local root = `BLOOM_STORAGE_LOCAL_ROOT`, else `BLOOM_OUTPUT_DIR`.**
  A dedicated var (rather than repurposing `BLOOM_OUTPUT_DIR`) because it names the
  *storage-backend root* explicitly instead of overloading a var the issue already flags as
  misleading, and it leaves room for future storage-backed prefixes. Falling back to
  `BLOOM_OUTPUT_DIR` (a required, dev-mounted dir) means `BLOOM_STORAGE_BACKEND=local` needs
  no second var in dev and populates the folder people already expect. Because
  `BLOOM_OUTPUT_DIR` is already required by `validate_env`, the root always resolves under
  `local`; the only genuinely new startup check is the explicit `BLOOM_STORAGE_LOCAL_ROOT`
  path when set.

- **Decision: keys are the contract; the local backend maps `key` → `<root>/<key>` with a
  resolved-path containment guard.**
  Storage keys use `/` separators and are `..`-free by construction (`AnalysisDir.key`;
  `validate_outputs` bans `..`). `LocalStorageBackend` joins the key onto the root with
  `os.sep`; before any I/O it resolves the joined path (`os.path.realpath`) and rejects
  anything not under `realpath(root)` — this stops absolute-path keys, `..` traversal, and
  symlink escapes, not just a substring `..` scan. `list_prefix(prefix)` returns the
  **bare** immediate-child names (files and first-level subdir names, **no trailing slash,
  no path prefix**) under `<root>/<prefix>/`, matching `os.listdir`, the in-memory fake, and
  Supabase `.list(prefix)`; a trailing-slash-terminated prefix lists *inside* that dir,
  `list_prefix("")` lists the root, and a missing prefix returns `[]` (catch
  `FileNotFoundError`) rather than raising. Both callers depend on this: `manifest.py:46`
  needs the file `"manifest.json"`, `experiment_utils.py:273` needs the bare dir name for
  `startswith(f"{entry.id}_")`.

- **Decision: preserve the object store's implicit guarantees explicitly.**
  - **Atomic writes.** `write_json` / `upload_file` write to a temp file *in the target's
    directory* (same filesystem as the root — a `/tmp` temp file would degrade `os.replace`
    to a non-atomic cross-mount copy) then `os.replace` into place. A crash / `kill -9` /
    `ENOSPC` mid-write leaves either the whole prior file or the whole new file — never a
    truncated `manifest.json`, which is the single catalog for every version of an experiment.
  - **Upsert / overwrite.** Writes overwrite an existing key in place (matching the Supabase
    `upsert:true`), and parent-dir creation is idempotent (`mkdir(parents=True, exist_ok=True)`).
  - **Verbatim bytes.** `upload_file` / `download_file` are binary copies (`read_bytes` /
    atomic `write_bytes`) with no newline or encoding translation on any OS, so
    `sha256(file on disk)` equals the `output_sha256` the manifest records (nothing on the
    normal read path re-verifies the hash, so a silent divergence would be invisible).
    `download_file` **copies** bytes to the caller's destination path — it never hands back
    or symlinks the canonical file under the root, whose lifetime the caller does not own.
  - **Byte-identical provenance.** Provenance (seed/agent/environment/code_versions/
    output_sha256/output_keys) is built *above* the seam, so the serialized `manifest.json`
    is byte-identical across backends for the same run (both use
    `json.dumps(indent=2, sort_keys=True)`); the parity test asserts this, not merely
    "equivalent shapes."
  - **Error redaction.** Local errors must not leak absolute host paths into agent-facing
    messages (`experiment_utils` interpolates `{e}` into caller-facing strings); the backend
    surfaces the same non-leaking shape the Supabase path guarantees, with detail logged
    server-side only.
  - **Content-type is a no-op for `local`** — extension-driven content typing lives only in
    the Supabase backend and is not an observable of the local path.

## Risks / Trade-offs

- **Non-atomic manifest on FS → catalog corruption.** → Temp-then-`os.replace` on the root's
  filesystem (above). This is the single most important durability requirement.
- **Byte divergence (e.g. Windows newline translation) breaks `output_sha256` silently**,
  since no read path re-hashes. → Verbatim binary copy + a hash-equality test on disk.
- **Mixed-backend / backend-flip splits version history.** Run v1/v2 under `supabase`, flip
  to `local`, and `next_version_id` reads the *absent* local manifest and re-allocates `v1`
  into a fresh local catalog — two artifacts both claiming "v1", the Supabase lineage
  invisible to later `local` reads. → Documented non-goal + a docs warning ("do not mix
  backends for one experiment; there is no cross-store view"); the spec states it so it is
  not silently relied upon (mirroring how `bloommcp-result-store` documents its single-writer
  limitation).
- **Single-writer / no-CAS** is inherited unchanged: the local backend provides the same
  last-write-wins, no-compare-and-swap semantics as the Supabase path — no stronger, no
  weaker. Atomicity-against-interruption (above) is distinct from and does not imply
  concurrent-writer safety.
- **Legacy-fallback collision.** The legacy read fallback reads
  `<BLOOM_OUTPUT_DIR>/qc_<stem>/<stem>_cleaned.csv` (`experiment_utils.py:352`), one level
  shallower than the local backend's `<root>/bloommcp_output/qc_<stem>/…`. They are disjoint
  *today*, but both are rooted at the same bind mount and the disjointness is load-bearing
  (a stray `<stem>_cleaned.csv` in the legacy path would be read as an un-versioned,
  un-hashed "certified" CSV and fed to PCA/UMAP). → The spec asserts disjointness and a test
  confirms a `local` run produces nothing the legacy branch would pick up.
- **Cross-platform key mapping** (Windows dev). → Keys stay `/`-joined logical strings; only
  the local backend converts to OS paths, at the leaf, behind the resolved-path guard.
- **Env-parity CI.** The new vars are added only as commented dev-compose lines, not
  `${VAR}` references, so `scripts/validate_env.sh` / `tests/unit/test_env_defaults.py` do
  not require a `.env.*.defaults` entry.

## Migration Plan

Purely additive and opt-in. No migration: default (`supabase`) is byte-for-byte unchanged,
no manifest/schema change, production untouched. Rollback = unset `BLOOM_STORAGE_BACKEND`.

## Open Questions

- None. (`read_input_csv` is resolved as **out of scope**: it rides the PostgREST client,
  is not one of the five faked helpers, and has no production caller — so the seam is the
  five object-storage helpers only.)
