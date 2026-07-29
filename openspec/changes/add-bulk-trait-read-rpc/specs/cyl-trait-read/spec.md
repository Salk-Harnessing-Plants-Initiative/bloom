## ADDED Requirements

### Requirement: Bulk experiment-scoped trait reads

Bloom SHALL provide `get_experiment_traits(experiment_id_ BIGINT, source_id_ BIGINT DEFAULT NULL,
run_id_ TEXT DEFAULT NULL)` returning every trait row for the given experiment in a single call
(`scan_id, date_scanned, plant_age_days, wave_number, plant_id, germ_day, plant_qr_code, accession_name,
trait_name, source_id, trait_value`), built on `cyl_scan_traits_source` and reusing its `is_latest`
selection rule rather than re-deriving it. With both optional arguments `NULL` the function SHALL return
the latest source per scan for every trait; with `source_id_` set it SHALL return only that source's
rows; with `run_id_` set it SHALL return each scan's values from the pipeline run whose
`pipeline_run_id` equals `run_id_`. Supplying both `source_id_` and `run_id_` SHALL raise an error.
These semantics SHALL match `get_scan_traits`'s existing latest/`source_id`/`run_id` behavior
byte-for-byte on any `(scan, trait)` combination both functions can return. A scan whose latest source
did not measure a trait an older source measured SHALL NOT have that trait backfilled from the older
source (no cross-source mixing). A trait whose latest-source value is non-finite (stored `NULL`) SHALL
be returned as a `NULL`-valued row, not omitted. Results SHALL be scoped to `experiment_id_` only — no
row from another experiment SHALL be returned under any argument combination.

#### Scenario: One call returns all traits for an experiment

- **WHEN** `get_experiment_traits(experiment_id_)` is called for an experiment with multiple scans, each
  with multiple measured traits
- **THEN** the call returns every trait for every scan in that experiment in a single response, with no
  `trait_name_` argument required

#### Scenario: Default path matches get_scan_traits' latest semantics

- **WHEN** `get_experiment_traits(experiment_id_)` and `get_scan_traits(experiment_id_, trait_name_)` are
  both called (no source/run arguments) for the same experiment and an overlapping trait name
- **THEN** the two calls agree row-for-row on that trait's `(scan_id, trait_value)` pairs

#### Scenario: Pinning a source matches get_scan_traits byte-for-byte

- **WHEN** `get_experiment_traits(experiment_id_, source_id_=X)` and
  `get_scan_traits(experiment_id_, trait_name_, source_id_=X)` are both called for the same source
- **THEN** the two calls agree row-for-row on that trait's values for source `X`

#### Scenario: Run grouping matches get_scan_traits byte-for-byte

- **WHEN** `get_experiment_traits(experiment_id_, run_id_=R)` and
  `get_scan_traits(experiment_id_, trait_name_, run_id_=R)` are both called for the same run
- **THEN** the two calls agree row-for-row on that trait's values for run `R`, including for a scan
  whose run `R` values were later superseded by a newer run

#### Scenario: Supplying both source_id_ and run_id_ is rejected

- **WHEN** `get_experiment_traits` is called with both `source_id_` and `run_id_` non-null
- **THEN** the call raises an error and returns no rows

#### Scenario: No cross-source mixing

- **WHEN** an older source measured traits A and B for a scan and the latest source measured only A
- **THEN** `get_experiment_traits`'s default path returns A from the latest source and does not return B

#### Scenario: Non-finite values are surfaced as NULL

- **WHEN** the latest source for a scan stored a `NULL` value for a trait
- **THEN** `get_experiment_traits` returns that trait as a row with `trait_value = NULL`, not omitted

#### Scenario: Results never cross experiment boundaries

- **WHEN** `get_experiment_traits` is called for experiment A, with or without `source_id_`/`run_id_` set
  to a source/run that belongs to experiment A
- **THEN** no row from any other experiment's scans is returned

#### Scenario: An experiment with no trait rows returns cleanly

- **WHEN** `get_experiment_traits` is called for an experiment with no scan-trait rows
- **THEN** the call returns zero rows without error

### Requirement: Experiment trait-source listing

Bloom SHALL provide `list_experiment_trait_sources(experiment_id_ BIGINT)` returning the distinct
`(source_id, source_name, pipeline_run_id)` tuples of real (non-`NULL`) sources that contributed
scan-trait rows to the given experiment, so a caller can enumerate an experiment's available sources
before choosing whether to pin one via `get_experiment_traits`'s or `get_scan_traits`'s `source_id_`/
`run_id_` arguments. Legacy rows with a `NULL` `source_id` SHALL NOT be listed as a source (there is no
source identity to pin for them). Results SHALL be scoped to `experiment_id_` only.

#### Scenario: Lists an experiment's sources

- **WHEN** `list_experiment_trait_sources(experiment_id_)` is called for an experiment whose scans carry
  trait rows from multiple distinct sources
- **THEN** the call returns each such source exactly once, with its `source_id`, `source_name`, and
  `pipeline_run_id` (nullable)

#### Scenario: Legacy NULL-source rows are not listed as a source

- **WHEN** an experiment has a scan whose trait rows all have a `NULL` `source_id`
- **THEN** `list_experiment_trait_sources` does not return a row for that placeholder

#### Scenario: An experiment with only legacy data returns cleanly

- **WHEN** `list_experiment_trait_sources` is called for an experiment whose scans all have `NULL`-source
  trait rows
- **THEN** the call returns zero rows without error

#### Scenario: Results never cross experiment boundaries

- **WHEN** `list_experiment_trait_sources` is called for experiment A
- **THEN** no source belonging only to another experiment's scans is returned

### Requirement: Bulk read grants match the existing per-trait read surface

`get_experiment_traits` and `list_experiment_trait_sources` SHALL be `SECURITY INVOKER`, matching
`get_scan_traits`'s posture, and SHALL be callable by the existing read roles (`bloom_agent`,
`bloom_user`, `bloom_admin`, `authenticated`) through the full join chain
(`cyl_scans`, `cyl_waves`, `cyl_plants`, `accessions`, `species`, `cyl_experiments`) without any new
grant or RLS policy — `SECURITY INVOKER` functions do not inherit a definer's privileges, so this is a
verified spot-check of existing grants, not a widening of access. This change SHALL NOT add, drop, or
alter any row-level-security policy or write grant on any table.

#### Scenario: Read roles can call the bulk read surface end-to-end

- **WHEN** a session assumes `bloom_agent` (and likewise `bloom_user`) and calls `get_experiment_traits`
  and `list_experiment_trait_sources` for an experiment reachable only through the full join chain
- **THEN** both calls succeed without any new grant

#### Scenario: No write capability is added

- **WHEN** this change is applied
- **THEN** no new policy or grant permits any role to write any table that could not already do so
  before the change

### Requirement: Additive, non-destructive bulk-read migration

The migration adding `get_experiment_traits` and `list_experiment_trait_sources` SHALL be additive
only — it MUST NOT drop, replace, or alter any existing table, view, or function (including
`get_scan_traits`, `cyl_scan_traits_source`, and `cyl_scan_traits_latest`, which it reads but does not
modify). A companion manual rollback script SHALL be provided under `supabase/rollbacks/` that drops
both new functions by full argument signature. All five tracked Supabase `database.types.ts` copies
SHALL be regenerated to include both new functions.

#### Scenario: Forward migration adds the bulk-read surface without touching existing objects

- **WHEN** the migration is applied to a database that already has the source-aware read surface
  (`cyl_scan_traits_source`, `cyl_scan_traits_latest`, `get_scan_traits`)
- **THEN** `get_experiment_traits` and `list_experiment_trait_sources` are created and every pre-existing
  view, function, table, and grant is unchanged

#### Scenario: Rollback removes exactly the two new functions

- **WHEN** the companion rollback script is applied to a database where the migration had been applied
- **THEN** `get_experiment_traits` and `list_experiment_trait_sources` no longer exist and every
  pre-existing read object (`get_scan_traits`, `cyl_scan_traits_source`, `cyl_scan_traits_latest`,
  `cyl_scan_trait_names`) is unchanged
