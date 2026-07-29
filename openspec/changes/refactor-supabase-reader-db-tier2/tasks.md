## 0. Pre-work

- [ ] 0.1 Confirm `retire-bloommcp-traits-dir-bypass` (bloom#476's change) merge status;
      if it lands first, rebase this change's `supabase_reader.py` rewrite on top and drop
      its now-superseded module-docstring/`_LOCAL_RAW_DEPRECATION` edits rather than
      reapplying them (see `proposal.md`'s Coordination note).
- [ ] 0.2 Confirm the live `cyl_experiments` table's column set against the Supabase schema
      (design.md's Open Question) before writing the `list_experiments()` query.
- [ ] 0.3 Read `bloommcp/src/bloom_mcp/data_access/fake_reader.py` in full to mirror its
      in-memory-double pattern for the new fake DB row-fetcher.

## 1. Test scaffolding (RED first)

- [ ] 1.1 Add a fake DB row-fetcher fixture (mirrors `FakeReader`'s precedent) injectable
      into `SupabaseReader`, seeded with long-format rows shaped like
      `get_experiment_traits`'s return columns.
- [ ] 1.2 Add a fixture wrapping `bloommcp/tests/fixtures/cylinder_*` (bloom#483) for the
      golden long→wide pivot test.

## 2. Failing tests (RED)

- [ ] 2.1 `load_experiment(str(experiment_id))` returns the expected wide frame with
      canonical roles (`genotype`, `sample_id`, metadata columns) — golden cylinder fixture
      (Scenario: Resolves the raw tier from the DB, wide-pivoted).
- [ ] 2.2 A non-numeric `name` with no cleaned output raises `ExperimentNotFoundError`, not
      a local-disk read (D1 — Scenario: Non-numeric raw-tier name is not-found, not a local
      fallback).
- [ ] 2.3 Multi-source fixture: two `source_id`s never mix in one returned frame (Scenario:
      One source per frame).
- [ ] 2.4 `SupabaseReader().list_sources(name)` returns the distinct `(source_id,
      source_name, pipeline_run_id)` tuples for a fixture experiment (Scenario:
      Source/run discovery).
- [ ] 2.5 `load_experiment(name, source_id=<pinned>)` returns only that source's rows;
      `load_experiment(name, run_id=<pinned>)` likewise (Scenario: Explicit source/run pin
      is honored).
- [ ] 2.6 `isinstance(SupabaseReader(), SourceSelectable)` is `True`;
      `isinstance(FakeReader(), SourceSelectable)` is `False` (Scenario: Capability is
      discoverable via isinstance).
- [ ] 2.7 `isinstance(SupabaseReader(), RawSourced)` is now `False` (the DB-backed raw tier
      has no on-disk path) (Scenario: DB-backed reader no longer satisfies RawSourced).
- [ ] 2.8 `list_experiments()` returns DB-sourced `ExperimentSummary` entries with
      non-placeholder `rows`/`trait_columns`/`total_columns` for a fixture experiment
      (Scenario: List experiments enumerates DB experiments).
- [ ] 2.9 `_ports.start_run` records `source_id`/`source_name` on the stamped `Provenance`
      when the injected reader is `SourceSelectable` (Scenario: Provenance records the
      resolved source).
- [ ] 2.10 `test_resolves_versioned_cleaned_then_raw` (existing) still passes unmodified —
      pin as a regression checkpoint.
- [ ] 2.11 Extend `test_provenance_roundtrip.py` / `test_provenance_to_version_entry.py`
      with populated and `None` `source_id`/`source_name` cases.
- [ ] 2.12 Add v4-schema coverage mirroring `test_schema_v3.py` (`CURRENT_SCHEMA_VERSION ==
      4`, a v4 `VersionEntry` round-trips through JSON exactly, a manifest declaring
      version 5 is rejected).
- [ ] 2.13 Add pre-v4 (v2 and v3) manifest backcompat coverage — old manifests still load
      under v4 code (Scenario: Old manifest still reads under v4 code).

## 3. Deletions

- [ ] 3.1 Delete `tests/data_access/test_local_reader.py::
      test_same_raw_bytes_yield_same_roles_as_supabase` outright (its premise no longer
      holds — do not attempt to "fix" it).
- [ ] 3.2 Delete `tests/data_access/test_supabase_reader.py::
      test_raw_source_path_rejects_path_traversal` outright (guards a case that no longer
      applies once `SupabaseReader` drops `RawSourced`).

## 4. Implementation (GREEN)

- [ ] 4.1 `bloommcp/src/bloom_mcp/supabase_client.py`: add an RPC-call helper
      (`get_postgrest_client().rpc(name, params).execute()` shape — no existing caller to
      reuse) for `get_experiment_traits`/`list_experiment_trait_sources`, and a
      table-read helper for `cyl_experiments` (D4).
- [ ] 4.2 `bloommcp/src/bloom_mcp/data_access/ports.py`: add `SourceInfo` dataclass and
      `SourceSelectable` Protocol (D2).
- [ ] 4.3 `bloommcp/src/bloom_mcp/data_access/supabase_reader.py`:
      - Rewrite the raw-tier fallback to call `get_experiment_traits`, pivot long→wide,
        apply the canonical column-role rename (design.md's table).
      - DB-only resolution: non-numeric `name` → `ExperimentNotFoundError` (D1).
      - Remove `raw_source_path`/`RawSourced` implementation.
      - Add `list_sources`/`resolve_source` (`SourceSelectable`) and `source_id`/`run_id`
        kwargs on `load_experiment`.
      - Rewrite `list_experiments()` to query `cyl_experiments` + per-experiment count
        aggregates (D4).
      - Update the module docstring to describe the DB-direct raw tier (drop the
        `bloommcp_input/` migration language it currently carries, unless already handled
        by `retire-bloommcp-traits-dir-bypass` per task 0.1).
- [ ] 4.4 `bloommcp/src/bloom_mcp/contract/provenance.py`: add `source_id`/`source_name` to
      `Provenance`, `Provenance.stamp()`, and `to_version_entry()` (D3).
- [ ] 4.5 `bloommcp/src/bloom_mcp/manifest/schema.py`: bump `CURRENT_SCHEMA_VERSION` to 4;
      add the v4-additive `source_id`/`source_name` block to `VersionEntry` (D3).
- [ ] 4.6 `bloommcp/src/bloom_mcp/tools/_ports.py`: add `source_for(filename)` mirroring
      `raw_source_for`; thread its result into `start_run`'s `Provenance.stamp()` call
      (D2/D3).
- [ ] 4.7 `bloommcp/src/bloom_mcp/data_access/__init__.py`: export `SourceInfo`,
      `SourceSelectable`.

## 5. Validate

- [ ] 5.1 Full `bloommcp` test suite green, including the two deletions actually removed
      (not skipped/xfailed).
- [ ] 5.2 `ruff`/`black` clean on all touched files (pinned versions per repo convention).
- [ ] 5.3 `openspec validate refactor-supabase-reader-db-tier2 --strict` passes.
- [ ] 5.4 Manual sanity: `list_available_experiments` and `list_existing_analyses` against
      the fake reader still produce sensible output (no `KeyError`/placeholder zeros
      surfacing to the LLM-facing text).

## 6. Docs + follow-up

- [ ] 6.1 Update `bloommcp/docs/data-access-roadmap.md`'s Tier 2 row status to ✅ and link
      this change / its PR.
- [ ] 6.2 Cross-reference bloom#476 (comment that its blocking dependency has landed) —
      do not close #476 from this change's PR (per the #476 auto-close lesson already
      learned once; let Elizabeth/whoever owns #476 close it explicitly after verifying).
- [ ] 6.3 File Tier 3 as its own tracking issue now that Tier 2 is reached (per the
      roadmap's just-in-time issue policy) if not already filed.
