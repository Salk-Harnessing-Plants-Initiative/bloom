## Why

`SupabaseReader`'s raw tier (`bloommcp/src/bloom_mcp/data_access/supabase_reader.py`)
reads raw experiment inputs from the local `BLOOM_TRAITS_DIR` disk path instead of Bloom's
actual Postgres tables — a stopgap the module's own docstring already flags as deprecated.
This is Tier 2 of `bloommcp`'s data-access roadmap
(`bloommcp/docs/data-access-roadmap.md`), filed as
[bloom#551](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/551) now that
Tier 1 has landed (`get_experiment_traits`/`list_experiment_trait_sources`,
[bloom#546](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/546), merged
via PR #548). Landing this also makes
[bloom#476](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/476)'s
remaining `BLOOM_TRAITS_DIR` bypass dead code — that issue is explicitly blocked on this one.

## What Changes

- **`SupabaseReader.load_experiment`** — the raw-tier fallback (any `name` that isn't
  resolved by `_resolve_versioned_cleaned`'s cleaned-output tiers, which are **untouched**)
  now resolves a concrete DB source first, then calls Tier 1's
  `get_experiment_traits(experiment_id_=int(name), source_id_=<resolved>)` **always
  explicitly pinned** (never unpinned — see Decision D2), pivots the long-format result
  wide, and renames columns to canonical roles instead of reading a local CSV.
- **DB-only resolution** *(Decision D1 — see `design.md`)*: a non-numeric `name` is a
  structured `ExperimentNotFoundError` rather than a local-disk read — this is what
  actually retires the `BLOOM_TRAITS_DIR` bypass `bloom#476` is blocked on. (Correction:
  the original justification for this — "PR #413 closed with nothing to fall through to" —
  was incomplete; bloom#388 is a live, assigned successor. The decision itself still holds;
  see D1 for the corrected reasoning.)
- **New `SourceSelectable` capability protocol** *(Decision D2)*, `isinstance`-gated like
  the existing `RawSourced`, giving `SupabaseReader` a `list_sources(name)` discovery method
  and `load_experiment(name, source_id=..., run_id=...)` optional pin kwargs. Supplying
  both `source_id` and `run_id` raises a structured `AmbiguousSourceSelectionError` before
  any RPC call. Every read — pinned or not — resolves and pins one concrete `source_id_`
  internally, so "one source per frame" is structural, not asserted.
- **`list_experiments()`** rewritten to enumerate experiments from `cyl_experiments` via a
  direct PostgREST table read (no new RPC/migration), deriving both `rows` and
  `trait_columns`/`total_columns` from the same per-experiment bulk fetch `load_experiment`
  itself performs *(Decision D4 — a real, accepted cost increase over the old directory
  scan, not a free lunch; see `design.md`, which also notes this bulk fetch is
  intentionally unpinned, unlike `load_experiment`)*. `ExperimentSummary.filename` is
  `str(experiment_id)` so the discovery→read round trip stays valid under D1.
- **Provenance/manifest schema v3→v4, additive** *(Decision D3)*: `Provenance`/`VersionEntry`
  gain optional `source_id`/`source_name` fields, wired through a new `source:
  Optional[SourceInfo]` parameter on `ResultStore.create_run` (mirroring the existing
  `source_csv` parameter) — **not** through `tools/_ports.py`'s `start_run`, which has zero
  real callers today. `ExperimentFrame` gains a `resolved_source` field set to whatever
  `SourceInfo` a raw-tier read actually pinned (`None` for a cleaned-tier read); every
  producer tool passes `source=frame.resolved_source` — the value its own earlier
  `load_experiment(...)` call already resolved — at its existing `store.create_run(...)`
  call site, **not** an independent re-resolution (an earlier draft did this and recorded
  an unrelated source for the 5 tools that read `require_clean=True`, i.e. a cleaned CSV,
  never the raw DB tier — caught in review, see design.md D3). `ExperimentBlock` is
  unchanged — a path-less source already produces `source_path=""`/`input_sha256=""`.
- **`sample_id` uniqueness validated at load time** *(Decision D5)*: `cyl_plants.qr_code`
  (the roadmap's `sample_id` mapping) is only unique within a wave, not experiment-wide.
  `SupabaseReader` raises a structured `AmbiguousSampleIdentityError` on a collision rather
  than silently returning a frame that mislabels two plants as one; the pivot retains
  `cyl_plants.id` as a metadata column for traceability.
- **Multiple scans for one plant is a structured error** *(Decision D6)*: the pivot keys
  one row per `plant_id` within the resolved source; a plant with more than one `scan_id`
  raises `MultipleScansPerPlantError` rather than the silent `(scan_id, plant_id)`-keyed
  pivot an earlier draft of this design claimed but never implemented.
- **A fake DB row-fetcher**, injected into `SupabaseReader`, mirrors the existing
  `fake_supabase_storage` fixture's monkeypatch-the-client-boundary shape (not
  `fake_reader.py`, which is a structurally different full alternate-adapter double).
- **Two existing tests deleted, not updated**: `tests/data_access/test_local_reader.py`'s
  `test_same_raw_bytes_yield_same_roles_as_supabase` and
  `tests/data_access/test_supabase_reader.py`'s `test_raw_source_path_rejects_path_traversal`.
- **Two existing tests fixed, not just supplemented**: `tests/data_access/
  test_supabase_reader.py`'s `test_resolves_versioned_cleaned_then_raw` (currently exercises
  the local-disk raw fallback D1 removes) and `tests/contract/{test_v2_backcompat.py,
  test_schema_v3.py}`'s hardcoded schema-version assertions (`4` was "rejected as too new";
  `3` was "current" — both now wrong post-bump).

**Explicitly out of scope**: reintroducing an upload/blob-backed raw input path (bloom#388's
job, not this change's — see D1). LLM-facing tool-schema text still saying "CSV filename" is
tracked by [bloom#552](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/552)
(filed 2026-07-29), not this change; retiring `BLOOM_TRAITS_DIR` from `_REQUIRED_DIRS`/boot
validation and `docker-compose.prod.yml`'s `SLEAP_OUT_CSV` mount has no tracking issue yet.
`LocalReader`/`BLOOM_STORAGE_BACKEND=local` is untouched.

## Impact

- **Affected specs:**
  - `bloommcp-experiment-read` — MODIFIED `SupabaseReader Adapter`; ADDED
    `SourceSelectable Capability`.
  - `bloommcp-tool-contract` — RENAMED+MODIFIED `Additive Manifest Schema v3` →
    `Additive Manifest Schema v4`; MODIFIED `Provenance Maps Into The Manifest
    VersionEntry`.
  - `bloommcp-result-store` — MODIFIED `ResultStore Port` (new `source` parameter on
    `create_run`); MODIFIED `Provenance Persisted at Commit`.
- **Affected code:**
  - `bloommcp/src/bloom_mcp/data_access/supabase_reader.py` (raw tier + `list_experiments`
    rewrite, drops `RawSourced`, adds `SourceSelectable`, adds the `sample_id` uniqueness
    check)
  - `bloommcp/src/bloom_mcp/data_access/ports.py` (new `SourceSelectable` protocol +
    `SourceInfo` value type; new `AmbiguousSourceSelectionError`/
    `AmbiguousSampleIdentityError`/`MultipleScansPerPlantError` exception classes;
    `ExperimentFrame` gains `resolved_source: Optional[SourceInfo] = None`)
  - `bloommcp/src/bloom_mcp/contract/provenance.py` (`Provenance` gains `source_id`/
    `source_name` fields; `to_version_entry()` passes them through — `Provenance.stamp()`
    itself is unchanged)
  - `bloommcp/src/bloom_mcp/manifest/schema.py` (`CURRENT_SCHEMA_VERSION` 3→4;
    `VersionEntry` v4-additive block)
  - `bloommcp/src/bloom_mcp/result_store/ports.py` (`ResultStore.create_run` gains
    `source: Optional[SourceInfo] = None`; `StoredRun` gains `source_id`/`source_name`)
  - `bloommcp/src/bloom_mcp/result_store/supabase_store.py` and `fake_store.py`
    (`create_run` merges `source` into `provenance` before storing the per-run state)
  - `bloommcp/src/bloom_mcp/tools/_ports.py` (`source_for(filename)` mirroring
    `raw_source_for`; used only by the still-unused `start_run`, not by any producer tool)
  - The 7 producer tools (`sections/sleap_roots/analysis/{qc_clean,qc_inspect,
    remove_outliers,pca_analysis,clustering,descriptive_stats,umap_analysis}.py`) — one
    added `source=frame.resolved_source` kwarg (the value each tool's own
    `load_experiment(...)` call already resolved) at each existing `store.create_run(...)`
    call site
  - `bloommcp/src/bloom_mcp/supabase_client.py` (new RPC-call and table-read helpers — no
    existing `.rpc(...)`/`.table(...)` caller to reuse)
- **Affected tests:** `tests/data_access/test_supabase_reader.py` (rewritten raw-tier
  tests including `test_resolves_versioned_cleaned_then_raw`, new fake DB fixture, one
  deletion), `tests/data_access/test_local_reader.py` (one deletion),
  `tests/contract/test_provenance_roundtrip.py` / `test_provenance_to_version_entry.py`
  (extended for `source_id`/`source_name`), `tests/contract/test_schema_v3.py` and
  `test_v2_backcompat.py` (fixed hardcoded version assertions, not just supplemented),
  a new v3-manifest backcompat case, and new coverage for `SupabaseResultStore`/
  `FakeResultStore.create_run`'s `source` parameter.
- **Coordination note — `retire-bloommcp-traits-dir-bypass` (bloom#476's in-progress
  change, 1/12 tasks):** that change makes doc/message-text-only edits to
  `supabase_reader.py`'s module docstring and `_LOCAL_RAW_DEPRECATION`, and a matching
  `SupabaseReader Adapter` wording delta on the same spec requirement this change also
  modifies. Neither the module docstring, the deprecation constant, nor the text #476's
  change edits exist in this change's rewritten `supabase_reader.py` (there is no more
  local raw-input fallback to deprecate). Recommend landing
  `retire-bloommcp-traits-dir-bypass` first (it's smaller and already in progress) and
  rebasing this change's rewrite on top.

**Non-goals:** reintroducing an upload-backed raw input path (bloom#388, not this change).
LLM-facing tool text (bloom#552) and `BLOOM_TRAITS_DIR` boot/compose cleanup — the
still-unfiled half of the roadmap's "Tier 3." No RLS or write-grant change; auth model
stays the shared `bloom_agent` role (see `design.md`'s Risks section for the resulting
application-level exposure-surface change this is explicitly not mitigating).
