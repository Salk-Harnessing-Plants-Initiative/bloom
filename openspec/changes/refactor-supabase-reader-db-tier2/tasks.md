## 0. Pre-work

- [ ] 0.1 Confirm `retire-bloommcp-traits-dir-bypass` (bloom#476's change) merge status;
      if it lands first, rebase this change's `supabase_reader.py` rewrite on top and drop
      its now-superseded module-docstring/`_LOCAL_RAW_DEPRECATION` edits rather than
      reapplying them (see `proposal.md`'s Coordination note).
- [x] 0.2 Confirm the live `cyl_experiments` table's column set against the Supabase schema
      (design.md's Open Question) before writing the `list_experiments()` query.
- [x] 0.3 Read `bloommcp/tests/data_access/conftest.py`'s `fake_supabase_storage` fixture in
      full — this monkeypatch-the-`supabase_client`-boundary shape, not `fake_reader.py`
      (a structurally different full alternate-adapter double), is the correct precedent
      for the new fake DB row-fetcher.
- [x] 0.4 Read all 7 producer-tool call sites in full to confirm their exact
      `store.create_run(...)` call shape before editing: `sections/sleap_roots/analysis/
      {qc_clean,qc_inspect,remove_outliers,pca_analysis,clustering,descriptive_stats,
      umap_analysis}.py`.

## 1. Test scaffolding (RED first)

- [x] 1.1 Add a fake DB row-fetcher fixture, monkeypatching the new
      `supabase_client` RPC-call helper (mirrors `fake_supabase_storage`'s boundary-mock
      shape), seeded with long-format rows shaped like `get_experiment_traits`'s actual
      return columns (`scan_id, date_scanned, plant_age_days, wave_number, plant_id,
      germ_day, plant_qr_code, accession_name, trait_name, source_id, trait_value`).
- [x] 1.2 Build a long-format golden fixture from `bloommcp/tests/fixtures/cylinder_*`.
      These are **wide-format** CSVs with `accession_id` (not the long-format
      `trait_name`/`trait_value`/`accession_name` shape the RPC returns) — this requires
      melting the wide fixture and fabricating `source_id`/`trait_name`/`accession_name`
      fields it doesn't carry today, not a direct wrap.

## 2. Failing tests (RED)

- [x] 2.1 `load_experiment(str(experiment_id))` returns the expected wide frame with
      canonical roles (`genotype`, `sample_id`, metadata columns including `plant_id`) —
      golden cylinder fixture (Scenario: Resolves the raw tier from the DB, wide-pivoted).
- [x] 2.2 A non-numeric `name` with no cleaned output raises `ExperimentNotFoundError`, not
      a local-disk read (D1 — Scenario: Non-numeric raw-tier name is not-found, not a local
      fallback).
- [x] 2.3 An unpinned `load_experiment` call always sends an explicit `source_id_` to the
      fake RPC (never calls it unpinned) once `resolve_source` returns a concrete source —
      assert on the fake's recorded call params, not just the output (D2 — Scenario: One
      source per frame, never mixed; this is the structural-enforcement test, not the old
      "two separate fixtures" test).
- [x] 2.4 Both `source_id` and `run_id` supplied to `load_experiment` raises
      `AmbiguousSourceSelectionError` before the fake RPC is called at all (D2).
- [x] 2.5 An explicit `source_id`/`run_id` pin matching nothing raises
      `ExperimentNotFoundError` (D2 — pinning something that doesn't resolve is a caller
      error, not a silent empty frame).
- [x] 2.6 An experiment with zero trait rows (valid per Tier 1's own "no trait rows returns
      cleanly" contract) returns a frame with zero `trait_cols`, not
      `ExperimentNotFoundError` (Scenario: Empty raw read is valid, not not-found).
- [x] 2.7 A fixture with two rows sharing the same `qr_code` across different waves raises
      `AmbiguousSampleIdentityError` (D5).
- [x] 2.8 `SupabaseReader().list_sources(name)` returns the distinct `(source_id,
      source_name, pipeline_run_id)` tuples for a fixture experiment; for a legacy-only
      (`source_id IS NULL`) fixture, `resolve_source(name)` returns `None` while
      `load_experiment(name)` still succeeds (Scenario: Source/run discovery; Unpinned
      resolution... — including the `None`/legacy case).
- [x] 2.9 `isinstance(SupabaseReader(), SourceSelectable)` is `True`;
      `isinstance(FakeReader(), SourceSelectable)` is `False`.
- [x] 2.10 `isinstance(SupabaseReader(), RawSourced)` is now `False`.
- [x] 2.11 `list_experiments()` returns DB-sourced `ExperimentSummary` entries with
      `filename == str(experiment_id)` and non-placeholder `rows`/`trait_columns`/
      `total_columns`; a fake RPC failure for one experiment excludes it from the list
      rather than failing the whole call (Scenario: List experiments enumerates database
      experiments — including the partial-failure case).
- [x] 2.12 **Rewrite** (not "pin unmodified") `test_resolves_versioned_cleaned_then_raw`:
      it currently calls `load_experiment("exp.csv")` expecting the local-disk raw
      fallback D1 removes — rewrite against a numeric experiment id and the new fake DB
      fixture, keeping its actual purpose (cleaned-tier resolution is unaffected by this
      change) intact.
- [x] 2.13 `SupabaseResultStore.create_run(..., source=SourceInfo(...))` and
      `FakeResultStore.create_run(..., source=SourceInfo(...))` each produce a committed
      `VersionEntry`/`StoredRun` carrying `source_id`/`source_name`; omitting `source`
      leaves both `None` (Scenario: Mapping yields a v4 VersionEntry with contract-time
      fields set; A reader with no source-versioned substrate maps to no source identity).
      This is the test that proves the wiring reaches a real commit path — not a
      `_ports.start_run`-only test.
- [x] 2.14 Extend `test_provenance_roundtrip.py` / `test_provenance_to_version_entry.py`
      with populated and `None` `source_id`/`source_name` cases.
- [x] 2.15 **Fix**, not just supplement, `test_schema_v3.py::test_current_schema_version_is_3`
      (hardcodes `CURRENT_SCHEMA_VERSION == 3` / `manifest_schema_version == 3` — both
      become `4`) and add v4-equivalent coverage (a v4 `VersionEntry` round-trips through
      JSON exactly).
- [x] 2.16 **Fix**, not just supplement, `test_v2_backcompat.py::
      test_newer_schema_version_is_rejected` (hardcodes `manifest_schema_version: 4` as
      the rejected/too-new case — becomes valid post-bump; update to `5`).
- [x] 2.17 Add pre-v4 (v2 and v3) manifest backcompat coverage — old manifests still load
      under v4 code.
- [x] 2.18 **Added after PR review**: `frame.resolved_source` is set (pinned or unpinned)
      for a raw-tier read, and `None` for a cleaned-tier read — proves provenance lineage
      is correct for `require_clean=True` consumers, not just that `create_run(source=...)`
      plumbing works when handed a `SourceInfo` directly (D3).
- [x] 2.19 **Added after PR review**: a frame's `resolved_source` reflects what was true at
      load time even when a newer source lands before the caller acts on the frame — proves
      the race window is actually closed, not just less likely (D3).
- [x] 2.20 **Added after PR review**: a plant with two distinct `scan_id`s in the resolved
      source raises `MultipleScansPerPlantError` (D6) — previously untested, and raised a
      generic `ExperimentReadError` with no dedicated scenario.

## 3. Deletions

- [x] 3.1 Delete `tests/data_access/test_local_reader.py::
      test_same_raw_bytes_yield_same_roles_as_supabase` outright.
- [x] 3.2 Delete `tests/data_access/test_supabase_reader.py::
      test_raw_source_path_rejects_path_traversal` outright.

## 4. Implementation (GREEN)

- [x] 4.1 `bloommcp/src/bloom_mcp/supabase_client.py`: add a `call_rpc(function_name,
      params)` helper (`get_postgrest_client().rpc(...).execute()`) for
      `get_experiment_traits`/`list_experiment_trait_sources`. `list_experiments()` reads
      `cyl_experiments` directly through the already-public `get_postgrest_client()` (no
      separate table-read wrapper needed). **Revised during implementation**: no separate
      `count=exact` helper for the `cyl_plants` row count was built — see 4.3's note.
- [x] 4.2 `bloommcp/src/bloom_mcp/data_access/ports.py`: add `SourceInfo` dataclass,
      `SourceSelectable` Protocol, `AmbiguousSourceSelectionError`,
      `AmbiguousSampleIdentityError`, and `MultipleScansPerPlantError` (subclassing
      `ExperimentReadError`) (D2, D5, D6). `ExperimentFrame` gains
      `resolved_source: Optional[SourceInfo] = None` (D3, added in response to PR review —
      see 4.9's note).
- [x] 4.3 `bloommcp/src/bloom_mcp/data_access/supabase_reader.py`:
      - Rewrite the raw-tier fallback: resolve a concrete source first (`resolve_source`),
        then call `get_experiment_traits` **always explicitly pinned** to that resolved
        `source_id_`, pivot long→wide keyed on `plant_id` alone within that source, apply
        the canonical column-role rename including `plant_id` as metadata (D2, design.md's
        table). Set the returned frame's `resolved_source` to the pinned `SourceInfo` (D3).
      - Reject more than one `scan_id` per `plant_id` in the resolved source with
        `MultipleScansPerPlantError`, not a silent `(scan_id, plant_id)` key (D6 — the
        original task description here was wrong about the key; fixed after PR review).
      - Validate `source_id`/`run_id` mutual exclusivity before any RPC call (D2).
      - Validate `sample_id` uniqueness post-pivot; raise `AmbiguousSampleIdentityError`
        on collision (D5).
      - DB-only resolution: non-numeric `name` → `ExperimentNotFoundError` (D1).
      - Remove `raw_source_path`/`RawSourced` implementation.
      - Add `list_sources`/`resolve_source` (`SourceSelectable`) and `source_id`/`run_id`
        kwargs on `load_experiment`.
      - Rewrite `list_experiments()`: `cyl_experiments` roster, deriving **both** `rows`
        and `trait_columns`/`total_columns` from one per-experiment bulk fetch (not a
        separate `count=exact` query — that would need an unverified PostgREST
        join-filter shape against `cyl_plants`/`cyl_waves`; deriving `rows` from the same
        bulk fetch instead is one round trip, not two, and needs no guessed query), with
        `filename = str(experiment_id)`; exclude an experiment from the list on a
        per-experiment fetch failure rather than failing the whole call (D4).
      - Update the module docstring to describe the DB-direct raw tier (coordinate with
        task 0.1).
- [x] 4.4 `bloommcp/src/bloom_mcp/contract/provenance.py`: add `source_id`/`source_name`
      fields to `Provenance` (defaulting to `None`); `to_version_entry()` passes them
      through. Do **not** add kwargs to `Provenance.stamp()` — nothing calls it with
      source context (D3).
- [x] 4.5 `bloommcp/src/bloom_mcp/manifest/schema.py`: bump `CURRENT_SCHEMA_VERSION` to 4;
      add the v4-additive `source_id`/`source_name` block to `VersionEntry`; update the
      module/class docstrings that currently say "schema version 3" (D3).
- [x] 4.6 `bloommcp/src/bloom_mcp/result_store/ports.py`: add `source:
      Optional[SourceInfo] = None` to the `ResultStore.create_run` Protocol; add
      `source_id`/`source_name` to `StoredRun`, passed through in
      `StoredRun.from_version_entry` (D3).
- [x] 4.7 `bloommcp/src/bloom_mcp/result_store/supabase_store.py` and `fake_store.py`:
      `create_run` accepts `source`, merges it into `provenance` via `model_copy` before
      constructing the per-run state dataclass (D3).
- [x] 4.8 `bloommcp/src/bloom_mcp/tools/_ports.py`: no `source_for(filename)` helper.
      **Fixed after a second PR review round**: an earlier revision added one, plus a
      matching one-line addition to `start_run`, "for consistency" with the 7 producer
      tools. Review found that `source_for` re-resolves independently (the same
      unpinned-race pattern fixed at 4.9 below) with zero test coverage and zero real
      callers — a footgun for whoever eventually adopts `start_run`, not a convenience.
      Removed outright; `start_run` drops its `source=` kwarg entirely (D2/D3).
- [x] 4.9 Add `source=frame.resolved_source` to the existing `store.create_run(...)` call
      in each of the 7 producer tools: `qc_clean.py`, `qc_inspect.py`,
      `remove_outliers.py`, `pca_analysis.py`, `clustering.py`, `descriptive_stats.py`,
      `umap_analysis.py` (D3). **Fixed after PR review**: this task originally said
      `source=_ports.source_for(params.experiment)` — an independent, unpinned
      re-resolution at commit time, disconnected from what the tool's own
      `load_experiment(...)` call actually read. For the 5 tools reading
      `require_clean=True` (a cleaned CSV from Storage, never the raw DB tier), that
      recorded a source the run never consulted. `frame.resolved_source` (the `frame`
      variable each tool already holds from its own load) is the correct, race-free value.
- [x] 4.10 `bloommcp/src/bloom_mcp/data_access/__init__.py`: export `SourceInfo`,
      `SourceSelectable`, `AmbiguousSourceSelectionError`, `AmbiguousSampleIdentityError`,
      `MultipleScansPerPlantError`.

## 5. Validate

- [x] 5.1 Full `bloommcp` test suite green, including the two deletions actually removed
      and the two schema-version tests actually fixed (not skipped/xfailed).
- [x] 5.2 `ruff`/`black` clean on all touched files (pinned versions per repo convention).
- [x] 5.3 `openspec validate refactor-supabase-reader-db-tier2 --strict` passes.
- [x] 5.4 Manual sanity: `list_available_experiments` and `list_existing_analyses` against
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

## 7. Second review round (5-agent) fixes

A follow-up 5-agent review (code quality, testing, scientific rigor, security, behavioural
correctness) surfaced three blocking issues and several important ones, addressed here.

- [x] 7.1 **Blocking — live smoke tests broken by the DB-only rewrite.**
      `live_persistence_smoke.py`/`live_plot_tool_smoke.py` and the 7 granular
      analysis-tool smoke tests (`tests/smoke/test_{qc_clean,qc_inspect,remove_outliers,
      pca_analysis,clustering,descriptive_stats,umap_analysis}_smoke.py`, via
      `conftest.py`'s `seeded_experiment` fixture) hard-coded non-numeric filenames
      (`"turface_raw.csv"`) as the experiment identifier passed to a `SupabaseReader`-backed
      tool call — `_parse_experiment_id` rejects those outright. **Investigation found the
      5 plot-tool smoke tests are NOT affected**: they call `experiment_utils.
      load_experiment_data` directly, a separate, untouched local-`BLOOM_TRAITS_DIR` raw
      tier this change does not touch — only the 7 tools routing through
      `SupabaseReader.load_experiment` needed a fix. Resolved with the user's chosen
      "minimal + document" scope, not a DB seeder (out of scope — no live dev-stack access
      to verify one, and building a correct CSV→envelope seeder for a 129-plant/880-trait
      fixture through `insert_cyl_result_envelope` with write-capable credentials is a
      change of its own): both scripts and `conftest.py` now read a numeric experiment id
      from env vars (`BLOOM_SMOKE_EXPERIMENT_ID`, `BLOOM_SMOKE_EXPERIMENT_ID_TURFACE_19`,
      `BLOOM_SMOKE_EXPERIMENT_ID_CYLINDER`) and fail/skip with a clear, actionable message
      when unset, rather than a confusing `ExperimentNotFoundError`. A new `db_experiment_id`
      fixture (distinct from the unchanged `seeded_experiment`) serves the 7 analysis-tool
      tests. `make bloommcp-smoke`'s Makefile target passes `BLOOM_SMOKE_EXPERIMENT_ID`
      through. **Getting CI fully green still requires seeding a real numeric experiment in
      the target Postgres and setting these env vars — not done here, no tracking issue
      filed yet.**
- [x] 7.2 **Blocking — `resolve_source(run_id=...)` could silently pick the wrong source.**
      `pipeline_run_id` carries no DB uniqueness constraint (only `idempotency_key` is
      enforced); a `run_id` pin matching more than one source previously resolved to
      whichever `next(...)` happened to match first, with no error — undermining "one
      source per frame is structural, not asserted." Fixed: a new `AmbiguousRunIdError`
      is raised when a `run_id` pin matches more than one source.
- [x] 7.3 **Blocking — silent data loss on duplicate `(plant_id, trait_name)` rows.**
      `pivot_table(..., aggfunc="first")` silently kept an arbitrary row and dropped the
      rest on a duplicate — the same class of risk this change already guards against for
      `sample_id`/multi-scan collisions, two functions away. Fixed: a new
      `DuplicateTraitReadingError` is raised on any duplicate `(plant_id, trait_name)` pair
      before the pivot, naming the trait and `qr_code` (not the internal `plant_id`).
- [x] 7.4 **Important — error-handling asymmetry.** `list_sources`/`load_experiment`'s RPC
      calls and `_experiment_exists`'s table read let a raw Supabase/network exception
      escape the `ExperimentReadError` contract. Fixed: a new `_safe_rpc` helper wraps
      `call_rpc`, and `_experiment_exists` wraps its table read, both translating any
      failure into a caller-safe `ExperimentReadError`.
- [x] 7.5 **Important — `list_experiments()` crashed the whole call on one malformed row.**
      `row["id"]` (before the try) and the `plant_ids`/`trait_names` derivation (after the
      try) could each raise `KeyError` uncaught, contradicting the documented per-item
      fail-open intent. Fixed: the entire per-row body is now one try/except.
- [x] 7.6 **Important — a `source_id`/`run_id` pin was silently ignored when a cleaned
      version resolved first.** `load_experiment(name, source_id=..., version="latest")`
      would return the cleaned frame with no indication the pin was never honored. Fixed:
      supplying either pin together with a non-`"raw"` `version` now raises
      `AmbiguousSourceSelectionError` up front.
- [x] 7.7 **Important — dead-code re-resolution footgun in `tools/_ports.py`.** See §7 note
      on `_ports.source_for` above (task 4.8) — removed outright rather than kept for a
      future caller.
- [x] 7.8 **Important — stale `RawSourced`/module docstrings in `ports.py`.**
      `RawSourced`'s docstring still listed `SupabaseReader` as an implementer (contradicted
      by this change's own `test_supabase_reader_no_longer_satisfies_raw_sourced`), and the
      module docstring's "a future DB-direct adapter" framing was stale now that
      `SupabaseReader` is that adapter. Both corrected.
- [x] 7.9 **Important — `scan_id` dropped from the returned frame's metadata**, a
      traceability regression versus plant/wave/experiment/source. Added to
      `_METADATA_COLS` and the pivot's retained columns.
- [x] 7.10 **Suggestion — id-leakage inconsistency.** `MultipleScansPerPlantError` named the
      internal `plant_id` directly while `AmbiguousSampleIdentityError` deliberately named
      only the `qr_code`. Made consistent: both now name the `qr_code`.
- [x] 7.11 **Suggestion — misleading message for `version="raw"` + `require_clean=True`.**
      Previously reused "no cleaned dataset found; run the QC workflow first," which
      misdiagnoses a self-contradictory parameter combination as a missing-data condition.
      Now distinguishes the two cases.
- [x] 7.12 New test coverage: `run_id` matching multiple sources (`AmbiguousRunIdError`),
      duplicate `(plant_id, trait_name)` rows (`DuplicateTraitReadingError`), a genuinely
      mixed-source fixture proving an unpinned read never mixes sources, a negative
      `SourceSelectable` capability check for `FakeReader`, a NaN/null `trait_value` case,
      `list_experiments()` surviving a malformed row, and the new
      pin-requires-raw-version / raw+require_clean-contradiction error paths.
