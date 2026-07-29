## Context

Issue #487 named two options for resolving the `storage/` vs. `storage_backend.py` naming
collision and asked that one be picked (plus the bundled `AnalysisWriter` deletion regardless
of which):

- **(A)** Move `storage_backend.py` into `storage/` as `storage/backend.py`.
- **(B)** Rename the `storage/` package itself (e.g. `manifest/`).

## Decision: (B) — rename `storage/` to `manifest/`, leave `storage_backend.py` in place

### Why not (A)

The actual import graph today (verified against `bloommcp/src/bloom_mcp/`):

- `storage/manifest.py` imports `list_prefix`, `read_json`, `write_json` from
  `bloom_mcp.supabase_client` **at module level**. This dependency is not just
  `AnalysisWriter`'s (which is being deleted) — `manifest.py` itself needs it for
  `read_manifest`/`write_manifest`, and stays after the deletion.
- `supabase_client.py`'s five helpers (`upload_file`, `download_file`, `write_json`,
  `read_json`, `list_prefix`) each **lazily** import `active_backend` from
  `bloom_mcp.storage_backend` _inside the function body_ — deliberately, per the
  `bloommcp-storage-backend` spec's side-effect-free-import contract.

So the real chain is `storage/manifest.py → supabase_client.py → storage_backend.py`. Moving
`storage_backend.py` inside `storage/` as `storage/backend.py` would make `supabase_client.py`
(external to the package) reach back into a submodule of the very package that depends on it —
a dependency cycle across the package boundary. Python's lazy (call-time, not import-time)
import inside `supabase_client.py`'s helpers happens to avoid a hard `ImportError` at import
time, but the layering is backwards regardless: a directory that says "the backend lives inside
storage/" while `storage/`'s own sibling module (`manifest.py`) depends on code that depends back
into that same subdirectory is not an improvement over the current sibling-file layout — it just
hides the cycle one level deeper.

Option (A) also has a much smaller mechanical footprint (only the handful of call sites that
`import bloom_mcp.storage_backend` directly), which was tempting, but the cycle it would bake
into the directory layout is the actual problem #487 is trying to fix — not the file count.

### Why (B)

`storage/`'s actual content — `AnalysisDir` (per-experiment/tool versioned output directory),
`Manifest`/`VersionEntry`/schema, `code_versions`, and `versioning` helpers
(`next_version_id`, `slugify`, `version_dir_name`) — is about **versioned-run bookkeeping**,
not physical object-storage backend selection. "storage" is the overloaded name; renaming this
package to `manifest/` (the name issue #487 itself suggests) removes the collision from the
module that's actually misnamed, and requires no change to `storage_backend.py`, which already
has the right name for what it does. This also means every existing consumer of
`bloom_mcp.storage_backend` (`supabase_client.py`, `experiment_utils.py`, `server.py`,
`data_access/local_reader.py`, `tests/test_storage_backend.py`) needs zero changes.

### Cost accepted

Renaming the package touches more surface than (A) would have — and more than this design's
first draft estimated. A full-repo grep (re-verified by an independent 5-subagent review) found
roughly 20 files, not "~5" or "three consumer files":

- **Module-level consumer imports** (repointing required for the wheel-import gate to catch a
  miss): `result_store/supabase_store.py`, `result_store/fake_store.py`, `experiment_utils.py`
  (though this one is a function-local/lazy import — a miss here surfaces only at runtime, not
  at CI import time), and `contract/provenance.py` — the widest-reaching miss in the first draft,
  since `contract` is imported by nearly every tool module
- **A `TYPE_CHECKING`-only import** in `result_store/ports.py` — never executes, and this repo
  has no mypy/pyright CI step, so a stale reference here would be permanently invisible to any
  automated check; only a manual/full-repo grep catches it
- **~9 test files** with direct `bloom_mcp.storage.*` imports, including `tests/conftest.py`'s
  `fake_supabase_storage` fixture (named explicitly by the `bloommcp-storage-backend` spec)
- **Two pre-existing, repo-root regression-guard tests that hardcode the old name and will fail
  CI outright** if not updated in the same change: `tests/unit/test_bloommcp_wheel_import_gate.py`
  (asserts the CI workflow's import line contains the literal string `"bloom_mcp.storage"`) and
  `tests/unit/test_bloommcp_data_mount_rename.py` (asserts a literal path string
  `bloommcp/src/bloom_mcp/storage/analysis_dir.py` exists) — both live outside `bloommcp/`, so
  neither is caught by a grep scoped to `bloommcp/src` and `bloommcp/tests`
- The `bloommcp-packaging` spec's import-smoke requirement text and the `bloommcp-storage-backend`
  and `bloommcp-experiment-read` specs' consumer-list wording

This is accepted as a one-time, purely mechanical cost (rename + find/replace), not an ongoing
one, and it doesn't touch any deployed behavior, manifest schema, or storage layout. One naming
wrinkle worth flagging so a reviewer doesn't mistake it for a typo: `storage/` already contains a
same-named submodule `storage/manifest.py`; after the rename this becomes
`bloom_mcp.manifest.manifest` (package `manifest/` containing `manifest.py`) — a stutter, but
harmless, since most call sites import through `manifest/__init__.py`'s re-exports rather than
the submodule path directly.

### Coordination with in-flight work

PR #464 (`egao28/bloommcp-resultstore-durability-324`, open into `staging`, confirmed
`MERGEABLE` at time of writing) touches `result_store/{supabase_store,fake_store}.py`,
`storage_backend.py`, `tests/conftest.py`, `tests/test_storage_backend.py`, and
`tests/result_store/test_supabase_result_store.py` — nearly the same file set this rename
touches. Land whichever is ready first; the other rebases before merging. This is a real,
verified overlap (checked via `gh pr diff 464 --name-only`), not a hypothetical risk.

## Goals / Non-Goals

- **Goals:** eliminate the naming collision in the direction that matches actual content;
  delete confirmed-dead `AnalysisWriter`; correct the stale roadmap wording the deletion exposes.
- **Non-Goals:** change manifest schema, storage key layout, the `ResultStore`/`ExperimentReader`
  ports, or any tool-observable behavior. This is a rename + dead-code removal, not a redesign.

## Migration / Rollout

Single PR, mechanical: `git mv storage manifest`, delete `manifest/writer.py` and its
`__init__.py` re-export, repoint every file that imports `bloom_mcp.storage.*` (the corrected,
verified list is in `proposal.md`'s Impact section), update the CI gate string and the two
repo-root regression-guard tests that hardcode the old name/path, update the four affected spec
deltas, correct the roadmap/wiki/README references. No feature flag or staged rollout needed —
this is an atomic rename.

**"No external consumers" is a claim about a private service, not just a repo-internal grep.**
`bloom_mcp` is not published anywhere a sibling could depend on it as a library: `bloommcp/pyproject.toml`
declares no `[project.scripts]`/publish config beyond the internal `uv` build-backend, there is no
PyPI/GHCR package-publish workflow for it (only container image builds, which don't expose the
Python package), and a grep of every other service in this monorepo (`langchain/`, `bloomcli/`,
`services/*`) for `bloom_mcp` returns zero hits — they talk to bloommcp over HTTP/MCP, never via
Python import. That rules out every consumer this repository could see. It cannot rule out an
external, out-of-repo fork or vendored copy, which is why this is still called out explicitly as
**BREAKING** in `proposal.md` rather than asserted away.
