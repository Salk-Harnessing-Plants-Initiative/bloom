## 0. Pre-work

- [ ] 0.1 Confirm `retire-bloommcp-traits-dir-bypass` (bloom#476's change) merge status;
      if it lands first, rebase this change's `supabase_reader.py` rewrite on top and drop
      its now-superseded module-docstring/`_LOCAL_RAW_DEPRECATION` edits rather than
      reapplying them (see `proposal.md`'s Coordination note).
- [ ] 0.2 Confirm the live `cyl_experiments` table's column set against the Supabase schema
      (design.md's Open Question) before writing the `list_experiments()` query.
- [ ] 0.3 Read `bloommcp/tests/data_access/conftest.py`'s `fake_supabase_storage` fixture in
      full — this monkeypatch-the-`supabase_client`-boundary shape, not `fake_reader.py`
      (a structurally different full alternate-adapter double), is the correct precedent
      for the new fake DB row-fetcher.
- [ ] 0.4 Read all 7 producer-tool call sites in full to confirm their exact
      `store.create_run(...)` call shape before editing: `sections/sleap_roots/analysis/
      {qc_clean,qc_inspect,remove_outliers,pca_analysis,clustering,descriptive_stats,
      umap_analysis}.py`.

## 1. Test scaffolding (RED first)

- [ ] 1.1 Add a fake DB row-fetcher fixture, monkeypatching the new
      `supabase_client` RPC-call helper (mirrors `fake_supabase_storage`'s boundary-mock
      shape), seeded with long-format rows shaped like `get_experiment_traits`'s actual
      return columns (`scan_id, date_scanned, plant_age_days, wave_number, plant_id,
      germ_day, plant_qr_code, accession_name, trait_name, source_id, trait_value`).
- [ ] 1.2 Build a long-format golden fixture from `bloommcp/tests/fixtures/cylinder_*`.
      These are **wide-format** CSVs with `accession_id` (not the long-format
      `trait_name`/`trait_value`/`accession_name` shape the RPC returns) — this requires
      melting the wide fixture and fabricating `source_id`/`trait_name`/`accession_name`
      fields it doesn't carry today, not a direct wrap.

## 2. Failing tests (RED)

- [ ] 2.1 `load_experiment(str(experiment_id))` returns the expected wide frame with
      canonical roles (`genotype`, `sample_id`, metadata columns including `plant_id`) —
      golden cylinder fixture (Scenario: Resolves the raw tier from the DB, wide-pivoted).
- [ ] 2.2 A non-numeric `name` with no cleaned output raises `ExperimentNotFoundError`, not
      a local-disk read (D1 — Scenario: Non-numeric raw-tier name is not-found, not a local
      fallback).
- [ ] 2.3 An unpinned `load_experiment` call always sends an explicit `source_id_` to the
      fake RPC (never calls it unpinned) once `resolve_source` returns a concrete source —
      assert on the fake's recorded call params, not just the output (D2 — Scenario: One
      source per frame, never mixed; this is the structural-enforcement test, not the old
      "two separate fixtures" test).
- [ ] 2.4 Both `source_id` and `run_id` supplied to `load_experiment` raises
      `AmbiguousSourceSelectionError` before the fake RPC is called at all (D2).
- [ ] 2.5 An explicit `source_id`/`run_id` pin matching nothing raises
      `ExperimentNotFoundError` (D2 — pinning something that doesn't resolve is a caller
      error, not a silent empty frame).
- [ ] 2.6 An experiment with zero trait rows (valid per Tier 1's own "no trait rows returns
      cleanly" contract) returns a frame with zero `trait_cols`, not
      `ExperimentNotFoundError` (Scenario: Empty raw read is valid, not not-found).
- [ ] 2.7 A fixture with two rows sharing the same `qr_code` across different waves raises
      `AmbiguousSampleIdentityError` (D5).
- [ ] 2.8 `SupabaseReader().list_sources(name)` returns the distinct `(source_id,
      source_name, pipeline_run_id)` tuples for a fixture experiment; for a legacy-only
      (`source_id IS NULL`) fixture, `resolve_source(name)` returns `None` while
      `load_experiment(name)` still succeeds (Scenario: Source/run discovery; Unpinned
      resolution... — including the `None`/legacy case).
- [ ] 2.9 `isinstance(SupabaseReader(), SourceSelectable)` is `True`;
      `isinstance(FakeReader(), SourceSelectable)` is `False`.
- [ ] 2.10 `isinstance(SupabaseReader(), RawSourced)` is now `False`.
- [ ] 2.11 `list_experiments()` returns DB-sourced `ExperimentSummary` entries with
      `filename == str(experiment_id)` and non-placeholder `rows`/`trait_columns`/
      `total_columns`; a fake RPC failure for one experiment excludes it from the list
      rather than failing the whole call (Scenario: List experiments enumerates database
      experiments — including the partial-failure case).
- [ ] 2.12 **Rewrite** (not "pin unmodified") `test_resolves_versioned_cleaned_then_raw`:
      it currently calls `load_experiment("exp.csv")` expecting the local-disk raw
      fallback D1 removes — rewrite against a numeric experiment id and the new fake DB
      fixture, keeping its actual purpose (cleaned-tier resolution is unaffected by this
      change) intact.
- [ ] 2.13 `SupabaseResultStore.create_run(..., source=SourceInfo(...))` and
      `FakeResultStore.create_run(..., source=SourceInfo(...))` each produce a committed
      `VersionEntry`/`StoredRun` carrying `source_id`/`source_name`; omitting `source`
      leaves both `None` (Scenario: Mapping yields a v4 VersionEntry with contract-time
      fields set; A reader with no source-versioned substrate maps to no source identity).
      This is the test that proves the wiring reaches a real commit path — not a
      `_ports.start_run`-only test.
- [ ] 2.14 Extend `test_provenance_roundtrip.py` / `test_provenance_to_version_entry.py`
      with populated and `None` `source_id`/`source_name` cases.
- [ ] 2.15 **Fix**, not just supplement, `test_schema_v3.py::test_current_schema_version_is_3`
      (hardcodes `CURRENT_SCHEMA_VERSION == 3` / `manifest_schema_version == 3` — both
      become `4`) and add v4-equivalent coverage (a v4 `VersionEntry` round-trips through
      JSON exactly).
- [ ] 2.16 **Fix**, not just supplement, `test_v2_backcompat.py::
      test_newer_schema_version_is_rejected` (hardcodes `manifest_schema_version: 4` as
      the rejected/too-new case — becomes valid post-bump; update to `5`).
- [ ] 2.17 Add pre-v4 (v2 and v3) manifest backcompat coverage — old manifests still load
      under v4 code.

## 3. Deletions

- [ ] 3.1 Delete `tests/data_access/test_local_reader.py::
      test_same_raw_bytes_yield_same_roles_as_supabase` outright.
- [ ] 3.2 Delete `tests/data_access/test_supabase_reader.py::
      test_raw_source_path_rejects_path_traversal` outright.

## 4. Implementation (GREEN)

- [ ] 4.1 `bloommcp/src/bloom_mcp/supabase_client.py`: add an RPC-call helper
      (`get_postgrest_client().rpc(name, params).execute()`) for
      `get_experiment_traits`/`list_experiment_trait_sources`, and a table-read helper for
      `cyl_experiments` plus a `count=exact` helper for the `cyl_plants` row count (D4).
- [ ] 4.2 `bloommcp/src/bloom_mcp/data_access/ports.py`: add `SourceInfo` dataclass,
      `SourceSelectable` Protocol, `AmbiguousSourceSelectionError` and
      `AmbiguousSampleIdentityError` (subclassing `ExperimentReadError`) (D2, D5).
- [ ] 4.3 `bloommcp/src/bloom_mcp/data_access/supabase_reader.py`:
      - Rewrite the raw-tier fallback: resolve a concrete source first (`resolve_source`),
        then call `get_experiment_traits` **always explicitly pinned** to that resolved
        `source_id_`, pivot long→wide, apply the canonical column-role rename including
        `plant_id` as metadata (D2, design.md's table).
      - Validate `source_id`/`run_id` mutual exclusivity before any RPC call (D2).
      - Validate `sample_id` uniqueness post-pivot; raise `AmbiguousSampleIdentityError`
        on collision (D5).
      - DB-only resolution: non-numeric `name` → `ExperimentNotFoundError` (D1).
      - Remove `raw_source_path`/`RawSourced` implementation.
      - Add `list_sources`/`resolve_source` (`SourceSelectable`) and `source_id`/`run_id`
        kwargs on `load_experiment`.
      - Rewrite `list_experiments()`: `cyl_experiments` roster + `count=exact` for `rows`
        + a bulk fetch per experiment for `trait_columns`/`total_columns`, with
        `filename = str(experiment_id)`; exclude an experiment from the list on a
        per-experiment fetch failure rather than failing the whole call (D4).
      - Update the module docstring to describe the DB-direct raw tier (coordinate with
        task 0.1).
- [ ] 4.4 `bloommcp/src/bloom_mcp/contract/provenance.py`: add `source_id`/`source_name`
      fields to `Provenance` (defaulting to `None`); `to_version_entry()` passes them
      through. Do **not** add kwargs to `Provenance.stamp()` — nothing calls it with
      source context (D3).
- [ ] 4.5 `bloommcp/src/bloom_mcp/manifest/schema.py`: bump `CURRENT_SCHEMA_VERSION` to 4;
      add the v4-additive `source_id`/`source_name` block to `VersionEntry`; update the
      module/class docstrings that currently say "schema version 3" (D3).
- [ ] 4.6 `bloommcp/src/bloom_mcp/result_store/ports.py`: add `source:
      Optional[SourceInfo] = None` to the `ResultStore.create_run` Protocol; add
      `source_id`/`source_name` to `StoredRun`, passed through in
      `StoredRun.from_version_entry` (D3).
- [ ] 4.7 `bloommcp/src/bloom_mcp/result_store/supabase_store.py` and `fake_store.py`:
      `create_run` accepts `source`, merges it into `provenance` via `model_copy` before
      constructing the per-run state dataclass (D3).
- [ ] 4.8 `bloommcp/src/bloom_mcp/tools/_ports.py`: add `source_for(filename)` mirroring
      `raw_source_for`; add the same one-line addition to `start_run` for consistency
      (still unused by any shipped tool) (D2/D3).
- [ ] 4.9 Add `source=_ports.source_for(params.experiment)` to the existing
      `store.create_run(...)` call in each of the 7 producer tools: `qc_clean.py`,
      `qc_inspect.py`, `remove_outliers.py`, `pca_analysis.py`, `clustering.py`,
      `descriptive_stats.py`, `umap_analysis.py` (D3 — this is the wiring that actually
      reaches a shipped tool's manifest).
- [ ] 4.10 `bloommcp/src/bloom_mcp/data_access/__init__.py`: export `SourceInfo`,
      `SourceSelectable`, `AmbiguousSourceSelectionError`, `AmbiguousSampleIdentityError`.

## 5. Validate

- [ ] 5.1 Full `bloommcp` test suite green, including the two deletions actually removed
      and the two schema-version tests actually fixed (not skipped/xfailed).
- [ ] 5.2 `ruff`/`black` clean on all touched files (pinned versions per repo convention).
- [ ] 5.3 `openspec validate refactor-supabase-reader-db-tier2 --strict` passes.
- [ ] 5.4 Manual sanity: `list_available_experiments` and `list_existing_analyses` against
      the fake reader still produce sensible output, and the printed example `filename`
      actually round-trips through `load_experiment` without a `ValueError`.

## 6. Docs + follow-up

- [ ] 6.1 Update `bloommcp/docs/data-access-roadmap.md`'s Tier 2 row status to ✅, link
      this change/PR, and correct the Goal-cell prose (it still describes the abandoned
      two-ID-shape dispatch — D1 simplified to DB-only).
- [ ] 6.2 Cross-reference bloom#476 (comment that its blocking dependency has landed) —
      do not close #476 from this change's PR; let its owner close it after verifying.
- [ ] 6.3 Confirm bloom#552 already tracks the LLM-facing tool-text half of "Tier 3" (it
      does, filed 2026-07-29) — file only the remaining `BLOOM_TRAITS_DIR` boot/compose
      cleanup half as its own tracking issue if not already filed.
