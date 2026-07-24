## 0. Housekeeping

- [ ] 0.1 Run `gh pr list --state open --json number,headRefName` and, for any bloommcp-touching
       PR, `gh pr diff <n> --name-only`. **Confirmed overlap:** PR #464
       (`egao28/bloommcp-resultstore-durability-324`, open into `staging`) touches
       `result_store/{supabase_store,fake_store}.py`, `storage_backend.py`, `tests/conftest.py`,
       `tests/test_storage_backend.py`, `tests/result_store/test_supabase_result_store.py` —
       nearly the same files this rename touches. Land whichever is ready first; rebase the
       other before merging. (PR #463, `umap_analysis`, checked and does not overlap.)

## 1. Guard-set cleanup (no ordering dependency — safe any time)

- [ ] 1.1 Update `bloommcp/tests/test_persistence_import_guard.py`'s `_FORBIDDEN` set: remove
       `"AnalysisWriter"` (the class is deleted in §2), keep `"AnalysisDir"` and `"supabase"`.
       This is a pure subset-shrink of an AST-based "imported ∩ forbidden" check — it cannot
       newly fail, so it has no ordering dependency on the rest of this change.
- [ ] 1.2 In the same file, add a permanent regression assertion (not a throwaway test): after
       the rename, `import bloom_mcp.manifest` succeeds and exposes `AnalysisDir`, `Manifest`,
       `VersionEntry`, `get_code_versions`, `next_version_id`, `slugify`, `version_dir_name`,
       `read_manifest`, `write_manifest`, `validate_schema`; `import bloom_mcp.storage` raises
       `ModuleNotFoundError`; and neither `bloom_mcp.manifest.AnalysisWriter` nor
       `bloom_mcp.manifest.writer` exists. This guards the **BREAKING** contract permanently
       against a future partial revert — not "add/finish, either is acceptable."

## 2. Rename the package

- [ ] 2.1 `git mv bloommcp/src/bloom_mcp/storage bloommcp/src/bloom_mcp/manifest`
- [ ] 2.2 Delete `bloommcp/src/bloom_mcp/manifest/writer.py` (`AnalysisWriter`)
- [ ] 2.3 Edit `bloommcp/src/bloom_mcp/manifest/__init__.py`: remove `from .writer import
       AnalysisWriter` and drop `"AnalysisWriter"` from `__all__`; update the module docstring
       if it references `storage/` by name

## 3. Repoint every consumer import (verified full-repo grep, not just the obvious 3 files)

Module-level imports — a miss here fails the CI wheel-import gate (`bloom_mcp.server` transitively
imports all of these):

- [ ] 3.1 `bloommcp/src/bloom_mcp/result_store/supabase_store.py`: `from bloom_mcp.storage import
       (...)` → `from bloom_mcp.manifest import (...)`; update the module docstring's "wraps the
       deployed storage layer" wording if it names `storage/`
- [ ] 3.2 `bloommcp/src/bloom_mcp/result_store/fake_store.py`: `from bloom_mcp.storage.versioning
       import version_dir_name` → `from bloom_mcp.manifest.versioning import version_dir_name`
- [ ] 3.3 `bloommcp/src/bloom_mcp/contract/provenance.py` (missed in the first pass — this is the
       widest-reaching consumer since `contract` is imported by nearly every tool module):
       `from bloom_mcp.storage.code_versions import get_code_versions` → `from
       bloom_mcp.manifest.code_versions import get_code_versions`; `from bloom_mcp.storage.schema
       import CodeVersions, VersionEntry` → `from bloom_mcp.manifest.schema import CodeVersions,
       VersionEntry`

Function-local/lazy import — **not** caught by the wheel-import gate (only surfaces at runtime
when the helper is actually called), so this one needs care rather than relying on CI:

- [ ] 3.4 `bloommcp/src/bloom_mcp/experiment_utils.py`: `from bloom_mcp.storage import
       AnalysisDir, ManifestSchemaError` → `from bloom_mcp.manifest import AnalysisDir,
       ManifestSchemaError`

`TYPE_CHECKING`-only import — never executes, and this repo has no mypy/pyright CI step, so this
one is invisible to any automated check and must be caught by this manual pass:

- [ ] 3.5 `bloommcp/src/bloom_mcp/result_store/ports.py`: `from bloom_mcp.storage.schema import
       VersionEntry` (inside `if TYPE_CHECKING:`) → `from bloom_mcp.manifest.schema import
       VersionEntry`

Test files with direct imports:

- [ ] 3.6 `bloommcp/tests/conftest.py`: `import bloom_mcp.storage.manifest as _manifest` →
       `import bloom_mcp.manifest.manifest as _manifest` (the `fake_supabase_storage` fixture —
       named explicitly by the `bloommcp-storage-backend` spec's "Callers and the test fake are
       unchanged" scenario)
- [ ] 3.7 `bloommcp/tests/test_storage_backend.py`, `bloommcp/tests/result_store/test_supabase_result_store.py`,
       `bloommcp/tests/contract/test_code_versions_installed_only.py`,
       `bloommcp/tests/contract/test_environment_pointer.py`,
       `bloommcp/tests/contract/test_provenance_roundtrip.py`,
       `bloommcp/tests/contract/test_provenance_to_version_entry.py`,
       `bloommcp/tests/contract/test_schema_v3.py`, `bloommcp/tests/contract/test_v2_backcompat.py`:
       repoint every `bloom_mcp.storage.*` reference to `bloom_mcp.manifest.*`

Repo-root pre-migration placeholder tests (both already fully `pytest.skip(...,
allow_module_level=True)`'d — neither currently executes an assertion, so this is lower-risk than
it looks):

- [ ] 3.8 Delete `tests/integration/test_versioned_storage_phase_b.py` (repo root, **not**
       `bloommcp/tests/integration/` — that path doesn't exist). This is `AnalysisWriter`'s
       dedicated test; it has no subject once the class is deleted.
- [ ] 3.9 Update the sibling `tests/integration/test_versioned_storage_phase_a.py`'s
       `bloom_mcp.storage` references to `bloom_mcp.manifest` for consistency (still skipped;
       not executed by CI either way, but should not be left referencing a deleted package)

Final safety net — confirm nothing was missed:

- [ ] 3.10 Grep the **whole repo** (not just `bloommcp/`) for `bloom_mcp\.storage`, `bloom_mcp
       import storage`, `storage/writer`, `storage/analysis_dir`, `AnalysisWriter` and repoint/
       remove any remaining hit. Exclude other OpenSpec change proposals under
       `openspec/changes/*` (including `openspec/changes/archive/`) — those are historical
       records of already-shipped or unrelated in-flight work and are out of scope for this
       rename.

## 4. Repo-root regression-guard tests that hardcode the old name (will fail CI otherwise)

- [ ] 4.1 `tests/unit/test_bloommcp_wheel_import_gate.py`: change the `REQUIRED_IMPORTS` tuple's
       `"bloom_mcp.storage"` entry to `"bloom_mcp.manifest"`. Run
       `uv run --extra test pytest tests/unit/test_bloommcp_wheel_import_gate.py -v` — must pass.
- [ ] 4.2 `tests/unit/test_bloommcp_data_mount_rename.py`: change the `RENAMED_FILES` entry
       `"bloommcp/src/bloom_mcp/storage/analysis_dir.py"` to
       `"bloommcp/src/bloom_mcp/manifest/analysis_dir.py"`. Run
       `uv run --extra test pytest tests/unit/test_bloommcp_data_mount_rename.py -v` — must pass.
- [ ] 4.3 `bloommcp/tests/test_package_baseline.py::test_no_stale_prototype_imports`: change its
       forbidden bare-import set from `{"source", "tools", "storage"}` to `{"source", "tools",
       "manifest"}` to match the reworded `bloommcp-packaging` spec scenario ("No stale
       prototype imports remain").
- [ ] 4.4 `tests/unit/test_pr_checks_workflow_shape.py`: no change needed (confirmed it's scoped
       to the GHCR-migration invariants, contains no `bloom_mcp.storage` reference) — run it
       anyway as a general workflow-shape regression check.

## 5. CI wheel-import gate

- [ ] 5.1 `.github/workflows/pr-checks.yml` (`python-audit` job, ~line 168): change
       `import bloom_mcp, bloom_mcp.tools, bloom_mcp.storage, bloom_mcp.server` →
       `import bloom_mcp, bloom_mcp.tools, bloom_mcp.manifest, bloom_mcp.server`

## 6. Documentation

- [ ] 6.1 `bloommcp/docs/roadmap.md`: correct the Tier 2 row's "wrapping the deployed
       `AnalysisWriter`/`AnalysisDir`" to describe what `SupabaseResultStore` actually does
       (reuses `AnalysisDir` + the manifest/versioning primitives directly, builds the v3
       `VersionEntry` itself — no `AnalysisWriter` wrap; the class is deleted). Also fix the
       separate "Deferred (out of slice scope)" list entry that cites "`AnalysisWriter` has no
       CAS today" as a live trigger condition — reword to reference `SupabaseResultStore`'s
       manifest writes instead, since the class naming the gap no longer exists. Optionally add
       a one-line forward-pointer to the Tier 0 row ("later renamed `manifest/`, #487") so a
       reader scanning top-to-bottom isn't confused by the earlier `storage/` mention.
- [ ] 6.2 `bloommcp/README.md`: update the package "Layout" section's `` `bloom_mcp.storage` ``
       line to `` `bloom_mcp.manifest` ``.
- [ ] 6.3 `_WIKI/BLOOMMCP/storage-workflow.md`: this is a **live, actively-maintained reference
       doc** (last touched by the immediately-preceding merged PR #477, which itself propagated
       a bloommcp directory rename here — established repo convention), not historical. Update
       literally: the title/intro's `storage/` reference, all ~8 relative links to
       `../../bloommcp/src/bloom_mcp/storage/*.py` files, and all `AnalysisWriter` mentions
       (including the "no CAS" warning callout and the conventions-table row citing
       `AnalysisWriter.commit`).
- [ ] 6.4 `_WIKI/BLOOMMCP/writing-a-new-tool.md`: update the `storage/__init__.py` link
       (canonical location for `CANONICAL_TOOL_CLASSES`) to the new `manifest/` path.
- [ ] 6.5 `_WIKI/BLOOMMCP/README.md`: update the directory-tree line `├── storage/` to
       `├── manifest/`.
- [ ] 6.6 `bloommcp/docs/2026-06-15-bloom-mcp-phase2-design.md` and
       `bloommcp/docs/2026-06-15-bloom-mcp-phase2-persistence-design.md`: these are genuinely
       dated design logs (frontmatter proves point-in-time draft status with an explicit
       amendment chain) — do **not** rewrite their historical narrative. Both repeat the same
       stale "wraps `AnalysisWriter`" claim as roadmap.md's Tier 2 row (this is one substantive
       error duplicated across 3 files, not 3 independent issues) — add one correction note per
       file using the same in-place "corrected here" pattern already established in
       `bloommcp/docs/data-access-roadmap.md` (~line 69-78), rather than editing the historical
       prose itself.

## 7. Validation

- [ ] 7.1 Run `pre-commit run --files` over every changed path
- [ ] 7.2 Run the full bloommcp suite: `uv run --extra test pytest bloommcp/tests/ -x`
- [ ] 7.3 Run the repo-root suites this rename actually touches (§3.8-3.9, §4 all live here —
       §3.5/6.3 in earlier drafts of this plan never exercised these, which is how two breaking
       tests were originally missed): `uv run --extra test pytest tests/unit/ tests/integration/ -x`
- [ ] 7.4 Build the wheel and smoke-import it exactly as CI does:
       `cd bloommcp && uv build --wheel && uv run --no-project --with dist/bloommcp-*.whl python -c
       "import bloom_mcp, bloom_mcp.tools, bloom_mcp.manifest, bloom_mcp.server; assert
       'site-packages' in bloom_mcp.__file__, bloom_mcp.__file__; bloom_mcp.server.build_app()"`
       with `SUPABASE_URL=""` / `BLOOM_AGENT_KEY=""`
- [ ] 7.5 Run `openspec validate rename-bloommcp-storage-manifest --strict` and resolve any
       issues

## 8. Spec deltas

- [ ] 8.1 Apply the `bloommcp-packaging` delta (import-smoke module list + the two new
       "old name/AnalysisWriter are gone" scenarios)
- [ ] 8.2 Apply the `bloommcp-storage-backend` delta (consumer module names)
- [ ] 8.3 Apply the `bloommcp-result-store` delta (stale `AnalysisWriter` wrap wording)
- [ ] 8.4 Apply the `bloommcp-experiment-read` delta (consumer-guard wording naming `storage/`
       directly in the "ExperimentReader Port" requirement)
