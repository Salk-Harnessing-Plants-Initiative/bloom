## Why

Three separately named, overlapping "storage" concepts live at the top of `bloom_mcp/`:
`bloom_mcp/storage/` (manifest/versioning primitives — `AnalysisDir`, `Manifest`,
`code_versions`, `versioning`, plus the legacy `AnalysisWriter`), `bloom_mcp/storage_backend.py`
(physical backend selection — Supabase Storage vs. local disk, sitting underneath
`supabase_client.py`), and `bloom_mcp/result_store/` (the real Tier-2 `ResultStore` port tools
use today). The real dependency chain is `result_store/` → `storage/` → `supabase_client.py` →
`storage_backend.py`, but by directory layout `storage_backend.py` sits as a *sibling* of
`storage/`, backwards from how the names read.

Separately, `storage/writer.py`'s `AnalysisWriter` is confirmed dead code: no live tool imports
it (`tests/test_persistence_import_guard.py`'s `_FORBIDDEN` set already asserts this), its last
production caller (the legacy `run_*_workflow` tools) was retired by PR #438, and
`SupabaseResultStore` was built to reimplement its commit logic directly rather than wrap it
(its own docstring says so — `AnalysisWriter.commit` "hand-rolls a provenance-lossy entry").
`bloommcp/docs/roadmap.md`'s Tier 2 row still describes `SupabaseResultStore` as "wrapping the
deployed `AnalysisWriter`/`AnalysisDir`", which is stale on both counts once this lands.

## What Changes

- **Rename** `bloom_mcp/storage/` → `bloom_mcp/manifest/` — the package's actual content
  (`AnalysisDir`, `Manifest`/`VersionEntry`/schema, `code_versions`, `versioning` helpers) is
  about versioned-run bookkeeping, not physical storage backend selection; renaming it removes
  the naming collision from the module that's actually misnamed, and leaves
  `bloom_mcp/storage_backend.py` (correctly named — it *is* the storage backend) untouched in
  place as a sibling of the renamed package. **Rejected alternative:** nesting
  `storage_backend.py` inside `storage/` as `storage/backend.py` (see `design.md`) — it would
  make `supabase_client.py` (which `manifest/manifest.py` itself depends on) reach back into a
  submodule of the package that depends on it, formalizing a real dependency cycle across the
  package boundary.
- **Delete** dead code: `storage/writer.py` (`AnalysisWriter`), its re-export from
  `storage/__init__.py`, and its dedicated test `tests/integration/test_versioned_storage_phase_b.py`.
- **Update** all intra-repo imports of `bloom_mcp.storage.*` to `bloom_mcp.manifest.*`
  (`result_store/supabase_store.py`, `result_store/fake_store.py`, `experiment_utils.py`).
- **Update** the CI wheel-import gate (`.github/workflows/pr-checks.yml`, the `python-audit`
  job) to import `bloom_mcp.manifest` instead of `bloom_mcp.storage`.
- **Correct** `bloommcp/docs/roadmap.md`'s Tier 2 row: `SupabaseResultStore` reuses `AnalysisDir`
  and the manifest/versioning primitives directly — it does not wrap `AnalysisWriter` (which no
  longer exists after this change).
- **BREAKING** (internal only, no deployed-behavior change): any out-of-tree code importing
  `bloom_mcp.storage` or `bloom_mcp.storage.writer.AnalysisWriter` will break. Nothing outside
  this repo depends on `bloom_mcp` as a library today, so this is a zero-external-impact rename.

No observable behavior changes: manifest schema, storage keys, on-disk/Supabase layout, and the
`ResultStore`/`ExperimentReader` ports are unaffected. This is a pure rename + dead-code deletion.

## Impact

**Note on scope:** an initial pass of this proposal undercounted its own blast radius (claimed
"3 consumer files"); a 5-subagent review (`/review-openspec`) traced the real import graph and
found roughly 20 files. The list below reflects the corrected, verified footprint.

- Affected specs: `bloommcp-packaging` (import-smoke module list + two new scenarios asserting
  the old name is gone), `bloommcp-storage-backend` (consumer module names in the "Storage
  Backend Interface" requirement), `bloommcp-result-store` (stale "wraps
  `AnalysisWriter`/`AnalysisDir`" wording), `bloommcp-experiment-read` (consumer-guard wording
  in the "ExperimentReader Port" requirement names `storage/` directly)
- Affected code (package rename + consumer repoint):
  - `bloommcp/src/bloom_mcp/storage/` → `bloommcp/src/bloom_mcp/manifest/` (rename, minus
    `writer.py`)
  - Consumers with **module-level** imports (repointing these is required for the CI
    wheel-import gate to stay green): `bloommcp/src/bloom_mcp/result_store/supabase_store.py`,
    `bloommcp/src/bloom_mcp/result_store/fake_store.py`, `bloommcp/src/bloom_mcp/experiment_utils.py`
    (function-local/lazy import — a miss here would NOT be caught by the wheel-import gate, only
    by a later runtime call), `bloommcp/src/bloom_mcp/contract/provenance.py` (imported
    transitively by nearly every tool module — the widest-reaching consumer, missed in the
    original scan)
  - Consumer with a **`TYPE_CHECKING`-only** import (no CI tooling — no mypy/pyright step exists
    in this repo — would ever catch a miss here): `bloommcp/src/bloom_mcp/result_store/ports.py`
  - Test files with direct imports: `bloommcp/tests/conftest.py` (the `fake_supabase_storage`
    fixture the `bloommcp-storage-backend` spec names explicitly), `bloommcp/tests/test_storage_backend.py`,
    `bloommcp/tests/result_store/test_supabase_result_store.py`,
    `bloommcp/tests/contract/{test_code_versions_installed_only,test_environment_pointer,test_provenance_roundtrip,test_provenance_to_version_entry,test_schema_v3,test_v2_backcompat}.py`
  - Repo-root regression-guard tests that hardcode the old name and **will fail CI** if not
    updated alongside the rename: `tests/unit/test_bloommcp_wheel_import_gate.py`
    (`REQUIRED_IMPORTS` tuple), `tests/unit/test_bloommcp_data_mount_rename.py` (`RENAMED_FILES`
    path string), `bloommcp/tests/test_package_baseline.py` (`test_no_stale_prototype_imports`'s
    forbidden bare-import set)
  - Repo-root pre-migration placeholder tests (both already fully `pytest.skip`'d, so neither is
    a currently-executing test): `tests/integration/test_versioned_storage_phase_b.py` (deleted —
    `AnalysisWriter`'s dedicated test, no subject once the class is gone),
    `tests/integration/test_versioned_storage_phase_a.py` (updated for consistency, still skipped)
  - `.github/workflows/pr-checks.yml` (wheel-import gate), `bloommcp/tests/test_persistence_import_guard.py`
    (`_FORBIDDEN` set + a permanent "old name and AnalysisWriter are gone" assertion)
  - Documentation: `bloommcp/docs/roadmap.md` (Tier 2 row + the "deferred items" AnalysisWriter
    mention), `bloommcp/README.md`, `_WIKI/BLOOMMCP/{README,storage-workflow,writing-a-new-tool}.md`
    (a live, actively-maintained reference wiki, not historical — updated literally, not
    annotated), and the two dated `bloommcp/docs/2026-06-15-bloom-mcp-phase2-*.md` design logs
    (annotated with a correction note in place, using the existing convention already established
    in `bloommcp/docs/data-access-roadmap.md`, since both repeat the same stale
    "wraps `AnalysisWriter`" claim as the roadmap — one coordinated fix, not independent ones)
- Unaffected: `bloom_mcp/storage_backend.py` (name and location), `bloom_mcp/supabase_client.py`,
  `bloom_mcp/data_access/local_reader.py`, `bloom_mcp/server.py`, `AnalysisDir` (kept, just
  re-homed), all manifest schema/on-disk layout, all tool-facing behavior
- **Coordination note:** PR #464 (`egao28/bloommcp-resultstore-durability-324`, open into
  `staging` at time of writing) touches `result_store/{supabase_store,fake_store}.py`,
  `storage_backend.py`, `tests/conftest.py`, `tests/test_storage_backend.py`, and
  `tests/result_store/test_supabase_result_store.py` — nearly the same file set this rename
  touches. Whichever lands second should rebase onto the other before merging.
- Closes #487
