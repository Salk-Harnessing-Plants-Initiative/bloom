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
  now calls Tier 1's `get_experiment_traits(experiment_id_=int(name))`, pivots the
  long-format result wide, and renames columns to canonical roles
  (`genotype`←`accessions.name`, `sample_id`←`cyl_plants.qr_code`, metadata columns for
  `wave`/`plant_age_days`/`date_scanned`) instead of reading a local CSV.
- **Two-ID-shape dispatch simplified to DB-only** *(Decision D1 — see `design.md`)*: issue
  #413 (the upload path the original dispatch would have fallen through to) is closed with
  no successor filed, so a non-numeric `name` is now a structured `ExperimentNotFoundError`
  rather than a silent local-disk read — this is what actually retires the `BLOOM_TRAITS_DIR`
  bypass `bloom#476` is blocked on.
- **New `SourceSelectable` capability protocol** *(Decision D2)*, `isinstance`-gated like
  the existing `RawSourced`, giving `SupabaseReader` a `list_sources(name)` discovery method
  and `load_experiment(name, source_id=..., run_id=...)` optional pin kwargs — the
  source/run-selection gap the roadmap calls out as previously silently defaulted to
  "latest."
- **`list_experiments()`** rewritten to enumerate experiments from `cyl_experiments` via a
  direct PostgREST table read (no new RPC/migration) instead of scanning the local
  directory/bucket *(Decision D4 — see `design.md` for the `rows`/`trait_columns` count
  tradeoff)*.
- **Provenance/manifest schema v3→v4, additive** *(Decision D3, mirrors the shipped
  v2→v3 `+seed/agent/output_sha256` bump)*: `Provenance`/`VersionEntry` gain optional
  `source_id`/`source_name` fields so a DB-backed read still has a recorded identity even
  though it no longer satisfies `RawSourced` (no on-disk path to content-address).
  `ExperimentBlock` is unchanged — `supabase_store.py` already treats a path-less source as
  `source_path=""`/`input_sha256=""`, so no schema change is needed there.
- **A fake DB row-fetcher**, injected into `SupabaseReader`, mirrors the existing
  `FakeReader` precedent so this tier's tests run with no live DB.
- **Two existing tests deleted, not updated**: `tests/data_access/test_local_reader.py`'s
  `test_same_raw_bytes_yield_same_roles_as_supabase` (its premise — `SupabaseReader` and
  `LocalReader` read identical on-disk bytes — no longer holds once the raw tier is
  DB-backed) and `tests/data_access/test_supabase_reader.py`'s
  `test_raw_source_path_rejects_path_traversal` (guards a local-disk traversal case that no
  longer applies once the raw tier drops `RawSourced`).

**Explicitly out of scope, per the roadmap's Tier 3 row**: LLM-facing tool-schema text
(still says "CSV filename"), retiring `BLOOM_TRAITS_DIR` from `_REQUIRED_DIRS`/boot
validation, and `docker-compose.prod.yml`'s `SLEAP_OUT_CSV` mount — those are Tier 3, not
yet filed. `LocalReader`/`BLOOM_STORAGE_BACKEND=local` is untouched.

## Impact

- **Affected specs:**
  - `bloommcp-experiment-read` — MODIFIED `SupabaseReader Adapter`; ADDED
    `SourceSelectable Capability`.
  - `bloommcp-tool-contract` — RENAMED+MODIFIED `Additive Manifest Schema v3` →
    `Additive Manifest Schema v4`; MODIFIED `Provenance Maps Into The Manifest
    VersionEntry`.
- **Affected code:**
  - `bloommcp/src/bloom_mcp/data_access/supabase_reader.py` (raw tier + `list_experiments`
    rewrite, drops `RawSourced`, adds `SourceSelectable`)
  - `bloommcp/src/bloom_mcp/data_access/ports.py` (new `SourceSelectable` protocol +
    `SourceInfo` value type)
  - `bloommcp/src/bloom_mcp/contract/provenance.py` (`Provenance.stamp()` /
    `to_version_entry()` gain `source_id`/`source_name`)
  - `bloommcp/src/bloom_mcp/manifest/schema.py` (`CURRENT_SCHEMA_VERSION` 3→4;
    `VersionEntry` v4-additive block)
  - `bloommcp/src/bloom_mcp/tools/_ports.py` (`start_run` records the resolved source
    alongside the existing `raw_source_for`)
  - `bloommcp/src/bloom_mcp/supabase_client.py` (new RPC/table-read helper — no existing
    `.rpc(...)` caller exists to reuse)
- **Affected tests:** `tests/data_access/test_supabase_reader.py` (rewritten raw-tier
  tests, new fake DB fixture, one deletion), `tests/data_access/test_local_reader.py` (one
  deletion), `tests/contract/test_provenance_roundtrip.py` /
  `test_provenance_to_version_entry.py` (extended for `source_id`/`source_name`),
  `tests/contract/test_schema_v3.py`-equivalent v4 coverage, a new `test_v3_backcompat.py`
  proving pre-v4 manifests still load.
- **Coordination note — `retire-bloommcp-traits-dir-bypass` (bloom#476's in-progress
  change, 1/12 tasks):** that change makes doc/message-text-only edits to
  `supabase_reader.py`'s module docstring and `_LOCAL_RAW_DEPRECATION`, and a matching
  `SupabaseReader Adapter` wording delta on the same spec requirement this change also
  modifies. Neither the module docstring, the deprecation constant, nor the text #476's
  change edits exist in this change's rewritten `supabase_reader.py` (there is no more
  local raw-input fallback to deprecate). Whichever change merges first, the other's
  doc-only hunk on this file should be dropped rather than reapplied — recommend landing
  `retire-bloommcp-traits-dir-bypass` first (it's smaller and already in progress) and
  rebasing this change's rewrite on top.

**Non-goals:** Tier 3 (LLM-facing surface + `BLOOM_TRAITS_DIR` boot/compose cleanup) — filed
as its own issue when reached, per the roadmap's just-in-time policy. No RLS or write-grant
change; auth model stays the shared `bloom_agent` role.
