# cyl-trait-read Specification

## Purpose
TBD - created by archiving change add-cyl-trait-read-source-aware. Update Purpose after archive.
## Requirements
### Requirement: Canonical source-aware trait view

Bloom SHALL provide a `cyl_scan_traits_source` view that is the single source of truth for
source-aware cyl scan-trait reads. It SHALL expose one row per `cyl_scan_traits` row with the columns
`scan_id`, `trait_id`, `trait_name` (the resolved `cyl_traits.name`), `value`, `source_id`,
`source_name` (the producing `cyl_trait_sources.name`), `pipeline_run_id` (the batch key
`cyl_trait_sources.metadata->>'pipeline_run_id'`, nullable), and a boolean `is_latest`. `is_latest`
SHALL be true exactly when the row's `source_id` equals `max(source_id) OVER (PARTITION BY scan_id)`,
compared with `IS NOT DISTINCT FROM` so that a scan whose rows all have a `NULL` `source_id` (legacy
data) is treated as latest. "Latest" is defined as `max(source_id)` per scan because
`cyl_trait_sources` has no timestamp column and identity ids increase monotonically as reprocessing
mints new sources. The view SHALL use `security_invoker`, SHALL be granted `SELECT` to the read roles,
and SHALL be the only place the latest-selection rule is defined; every other read object is built on
it. Exposing `trait_name`/`source_id`/`is_latest` makes the view directly usable for scan-grain reads.

#### Scenario: View exposes the source dimension for each trait row

- **WHEN** a scan has trait rows written by a source
- **THEN** `cyl_scan_traits_source` returns those rows with `source_id`, `source_name`, and
  `pipeline_run_id` populated from that source, and `value`/`scan_id`/`trait_id` unchanged

#### Scenario: is_latest marks the max-source rows per scan

- **WHEN** a scan has trait rows from two sources (an original and a higher-id reprocess)
- **THEN** only the rows from the higher `source_id` have `is_latest = true`, and the original
  source's rows have `is_latest = false`

#### Scenario: Legacy NULL-source rows are treated as latest

- **WHEN** a scan has only trait rows whose `source_id` is `NULL`
- **THEN** those rows have `is_latest = true` (so legacy scans remain readable by default)

#### Scenario: pipeline_run_id is surfaced from source metadata

- **WHEN** a source row's `metadata` contains a `pipeline_run_id`
- **THEN** every `cyl_scan_traits_source` row for that source exposes that value in `pipeline_run_id`,
  and rows whose source has no `pipeline_run_id` expose `NULL`

### Requirement: Latest-source-by-default scan trait reads

Bloom SHALL provide a `cyl_scan_traits_latest` view equal to the `is_latest` rows of
`cyl_scan_traits_source`, and the default read path SHALL return, per scan, only the latest source's
traits. A scan with multiple sources SHALL NOT yield duplicate or cross-source-mixed rows: the latest
source is chosen at scan grain and exactly that source's traits are returned (traits the latest run did
not produce are absent rather than backfilled from an older source). For **source-bearing** data the
latest view yields at most one row per `(scan, trait)`, because `cyl_scan_traits` is
`UNIQUE (scan_id, source_id, trait_id)` and a single latest source is chosen per scan. Pre-existing
**legacy `NULL`-source** rows are surfaced as-is (the legacy schema's `UNIQUE` is vacuous for `NULL`
`source_id`, so it could permit two `NULL`-source rows for one `(scan, trait)`; the sole-writer RPC can
no longer create such rows). A latest-source trait whose `value` is `NULL` (the write-back RPC stores
`NULL` for non-finite values) SHALL still be returned as a `NULL`-valued row — the trait was measured;
non-finite is not absent — matching the legacy function's no-value-filter behavior.

#### Scenario: Default read returns the latest source's values only

- **WHEN** a scan has two sources carrying different values for the same trait and the default path is
  read (no source argument)
- **THEN** only the latest source's value is returned for that scan, with no duplicate rows for the
  trait

#### Scenario: No cross-source mixing when the latest run dropped a trait

- **WHEN** an older source wrote traits A and B for a scan and the latest source wrote only trait A
- **THEN** the default read returns A from the latest source and does **not** return B (no backfill
  from the older source)

#### Scenario: Latest view has no duplicate rows per scan and trait

- **WHEN** `cyl_scan_traits_latest` is queried for a source-bearing scan with multiple sources
- **THEN** it returns at most one row per `(scan_id, trait_id)`

#### Scenario: A non-finite latest value is surfaced as NULL, not dropped

- **WHEN** the latest source for a scan stored a `NULL` `value` for a trait (a non-finite measurement)
- **THEN** the default read returns that trait as a row with `trait_value = NULL` (it is not omitted)

### Requirement: Source-pinned and run-grouped get_scan_traits

Bloom SHALL replace `get_scan_traits` with a single function
`get_scan_traits(experiment_id_ BIGINT, trait_name_ TEXT, source_id_ BIGINT DEFAULT NULL,
run_id_ TEXT DEFAULT NULL)` returning the same result columns as before
(`scan_id, date_scanned, plant_age_days, wave_number, plant_id, germ_day, plant_qr_code,
accession_name, trait_name, trait_value`). The legacy two-argument function SHALL be dropped so exactly
one candidate exists (no PostgREST overload ambiguity), and a caller passing only `experiment_id_` and
`trait_name_` SHALL behave as before via the defaults. With both optional arguments `NULL` the function
SHALL return the latest source per scan; with `source_id_` set it SHALL return only that source's rows
(scan-grain pin); with `run_id_` set it SHALL return each scan's values from the pipeline run whose
`pipeline_run_id` equals `run_id_` (experiment-grain "as of run X"). The `run_id_` path selects rows by
the run, **not** by the global latest — so a scan that a newer run has since superseded still
contributes its `run_id_` values (no implicit latest filter), which is the point of "as of run X". To
keep the result unambiguous, when a run delivered the same `(scan, trait)` more than once (distinct
`idempotency_key`/`source_id` but the same `pipeline_run_id`), the `run_id_` path SHALL return only the
**latest delivery within that run** (`max(source_id)` among that run's rows for the `(scan, trait)`),
yielding at most one row per `(scan, trait)`. Supplying both `source_id_` and `run_id_` SHALL raise an
error rather than return an ambiguous result.

#### Scenario: Backward-compatible two-argument call returns latest

- **WHEN** an existing caller invokes `get_scan_traits(experiment_id_, trait_name_)` with no source
  argument for an experiment whose scans have multiple sources
- **THEN** the call succeeds and returns the latest source per scan, one row per scan for the trait

#### Scenario: Pinning an older source returns the older values

- **WHEN** `get_scan_traits` is called with `source_id_` set to a scan's older source
- **THEN** it returns that source's (older) trait values for that scan

#### Scenario: Run id groups an experiment by pipeline run

- **WHEN** `get_scan_traits` is called with `run_id_` set to a `pipeline_run_id` shared by several
  scans' sources in the same experiment
- **THEN** it returns those scans' values from the matching run (one row per such scan)

#### Scenario: Run id returns a scan's run values even after a newer run superseded it

- **WHEN** scan A was processed by `run-1` then reprocessed by a newer `run-2`, scan B only by `run-1`,
  and `get_scan_traits` is called with `run_id_ = 'run-1'`
- **THEN** it returns scan A's `run-1` values (not A's newer `run-2` values) together with scan B's
  `run-1` values — selection is by run, without an implicit global-latest filter

#### Scenario: Duplicate deliveries within one run do not duplicate rows

- **WHEN** one pipeline run delivered the same `(scan, trait)` twice (two sources with distinct
  `source_id` but the same `pipeline_run_id`) and `get_scan_traits` is called with that `run_id_`
- **THEN** it returns a single row for that `(scan, trait)` — the latest delivery within the run
  (`max(source_id)`)

#### Scenario: A run id matching no source returns no rows

- **WHEN** `get_scan_traits` is called with `run_id_` set to a value that no source's `pipeline_run_id`
  carries (e.g. the producer omitted `pipeline_run_id`, so the source's `pipeline_run_id` is `NULL`)
- **THEN** the call returns zero rows and does not raise

#### Scenario: Supplying both source*id* and run*id* is rejected

- **WHEN** `get_scan_traits` is called with both `source_id_` and `run_id_` non-null
- **THEN** the call raises an error and returns no rows

#### Scenario: Result columns and ordering are preserved

- **WHEN** `get_scan_traits` returns rows for an experiment whose accessions are not in insertion order
- **THEN** the result columns are exactly `scan_id, date_scanned, plant_age_days, wave_number,
plant_id, germ_day, plant_qr_code, accession_name, trait_name, trait_value` (same names, types, and
  order as before) and rows are ordered by `accession_name, plant_id`

#### Scenario: An experiment with no matching traits returns cleanly

- **WHEN** `get_scan_traits` is called for an experiment/trait combination that has no trait rows
- **THEN** the call returns zero rows without error

### Requirement: Source-disambiguated trait-name listing

The `cyl_scan_trait_names` view SHALL list the non-null `DISTINCT` trait names present in the
latest-source data (the `trait_name` column of `cyl_scan_traits_latest`, filtered `WHERE trait_name IS
NOT NULL` so an unresolved legacy row with a `NULL` `trait_id` cannot surface a `NULL` name), so the
trait-name listing reflects traits actually measured in current (latest-source) data rather than every
registered name. Because the view is dropped and recreated, the migration SHALL re-issue its
`GRANT SELECT` to the read roles (the prior object-level read access does not survive `DROP VIEW`).

#### Scenario: Lists distinct names present in latest data

- **WHEN** the latest sources across scans have measured a set of trait names
- **THEN** `cyl_scan_trait_names` returns each such name exactly once

#### Scenario: A name only in a superseded source is excluded

- **WHEN** a trait name was measured only by an older source that a later source has superseded (the
  latest source did not measure it)
- **THEN** that name is absent from `cyl_scan_trait_names`

### Requirement: Read path stays open to read roles with no RLS change

The new views SHALL grant `SELECT` to the existing read roles (`bloom_agent`, `bloom_user`,
`bloom_admin`, `authenticated`) using `security_invoker`, and `get_scan_traits` SHALL remain callable by
the existing read roles. This change SHALL NOT add, drop, or alter any row-level-security policy or any
write grant on `cyl_scan_traits`, `cyl_trait_sources`, or `cyl_traits`; it is read-only and does not
widen write access.

#### Scenario: Read roles can use the new read surface

- **WHEN** a session assumes each of `bloom_agent`, `bloom_user`, `bloom_admin`, and `authenticated`
  and selects from `cyl_scan_traits_source`, `cyl_scan_traits_latest`, and `cyl_scan_trait_names`
  (including the joined `source_name` column), and calls `get_scan_traits`
- **THEN** each read and call is permitted

#### Scenario: No write capability is added

- **WHEN** the change is applied
- **THEN** no new policy or grant permits any role to write `cyl_scan_traits`, `cyl_trait_sources`, or
  `cyl_traits` that could not already do so before the change

### Requirement: Additive, non-destructive read-path migration

The read-path migration SHALL be **additive only** — it MUST NOT drop or rewrite any table, column, or
data — so a single forward `supabase db push` applies it. Replacing `get_scan_traits` and
`cyl_scan_trait_names` SHALL recreate them within the same migration transaction so live callers see no
missing-object window, and each created/recreated view SHALL re-issue its `GRANT SELECT` to the read
roles. The migration SHALL NOT `REVOKE` the function's default `PUBLIC` `EXECUTE` (the read RPC stays
callable as before). A companion manual rollback script SHALL be provided under `supabase/rollbacks/`
that drops the two new views (dependents before the substrate), `DROP`s the four-argument
`get_scan_traits` **before** restoring the prior two-argument `LANGUAGE SQL` definition (so no overload
ambiguity is reintroduced), and restores `cyl_scan_trait_names = SELECT name FROM cyl_traits`. All five
tracked Supabase `database.types.ts` copies SHALL be regenerated to include the new views and the
updated four-argument function signature, with the two new arguments optional.

#### Scenario: Forward migration adds the read surface without touching data

- **WHEN** the migration is applied to a database that already has `cyl_scan_traits`,
  `cyl_trait_sources`, and `cyl_traits`
- **THEN** `cyl_scan_traits_source` and `cyl_scan_traits_latest` are created, `get_scan_traits` and
  `cyl_scan_trait_names` are redefined, and no pre-existing table or data is altered

#### Scenario: Recreated trait-names view stays readable by read roles

- **WHEN** a session assumes `bloom_agent` (and likewise `bloom_user`) after the migration and selects
  from the recreated `cyl_scan_trait_names`
- **THEN** the read is permitted (the migration re-issued the view's `GRANT SELECT`)

#### Scenario: Rollback restores the prior read surface without overload ambiguity

- **WHEN** the companion rollback script is applied to a database where the migration had been applied
- **THEN** the two new views no longer exist, exactly one `get_scan_traits` function remains and it has
  the two-argument signature, and `cyl_scan_trait_names` is back to `SELECT name FROM cyl_traits`

