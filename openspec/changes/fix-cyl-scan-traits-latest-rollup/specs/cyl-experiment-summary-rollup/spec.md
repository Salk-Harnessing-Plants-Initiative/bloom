## ADDED Requirements

### Requirement: Per-experiment summary rollup table

Bloom SHALL provide a `cyl_experiment_summary_counts` table keyed by `experiment_id` (foreign key to
`cyl_experiments`, `ON DELETE CASCADE`), storing `n_plants` and `n_traits` (both `NOT NULL int`) — the
same counts `get_experiment_summary_counts` would compute for that experiment's "current latest" state
(no `source_id_`/`run_id_` override). An experiment with no matching `cyl_scan_traits` data reachable
under the latest selection SHALL have no row in this table, matching `get_experiment_summary_counts`'s
existing "absent if zero, not zero-valued" contract — so no downstream consumer's zero-default handling
needs to change.

#### Scenario: An experiment with data has a matching rollup row

- **WHEN** an experiment has at least one plant with at least one scan carrying latest-selected trait
  data
- **THEN** `cyl_experiment_summary_counts` has exactly one row for that experiment, with `n_plants`/
  `n_traits` matching a live `get_experiment_summary_counts(experiment_id_)` computation

#### Scenario: An experiment with no matching data has no rollup row

- **WHEN** an experiment has no plant/scan/trait data reachable under the latest selection (including an
  experiment that starts with none, and one whose only data was later deleted)
- **THEN** `cyl_experiment_summary_counts` has no row for that experiment

### Requirement: Rollup is refreshed per experiment when that experiment's underlying data changes

The rollup row for an experiment SHALL be refreshed (recomputed and upserted, or removed if the
experiment now has no matching data) whenever a write to `cyl_scan_traits` changes what counts as
"latest" for any scan belonging to that experiment — scoped to the one affected experiment, not a
whole-table recomputation, piggybacking on the same trigger that maintains `is_latest` (the
`cyl-trait-writeback` capability's write-side maintenance requirement). This refresh SHALL only be
correct and affordable because `is_latest` is a stored, indexed column by the time this refresh runs —
recomputing an experiment's counts from a live join is only cheap once the underlying `is_latest`
lookup is cheap.

#### Scenario: A new ingest refreshes only the affected experiment's rollup row

- **WHEN** `insert_cyl_result_envelope` writes trait rows for a scan belonging to experiment A
- **THEN** experiment A's rollup row is refreshed to reflect the new data, and no other experiment's
  rollup row is touched

#### Scenario: A rerun that changes the latest source updates the rollup to the new counts

- **WHEN** a rerun changes which source is latest for one of an experiment's scans, changing the
  experiment's `n_plants`/`n_traits`
- **THEN** the experiment's rollup row is updated to the new counts, not left at the pre-rerun values

#### Scenario: An experiment that loses all its matching data has its rollup row removed

- **WHEN** all of an experiment's `cyl_scan_traits` rows that contributed to its rollup row are deleted
- **THEN** the experiment's rollup row is removed from `cyl_experiment_summary_counts`, not left present
  with stale or zero-valued counts

#### Scenario: The refresh mechanism computes its own aggregation, not a call back into the rollup-backed read path

- **WHEN** the refresh mechanism recomputes an experiment's counts (whether from the write-triggered path
  or the one-time backfill)
- **THEN** it computes those counts via a live join over the current data, not by calling
  `get_experiment_summary_counts` itself with no source/run override — that read path answers from
  `cyl_experiment_summary_counts`'s current contents, which is exactly what this refresh is in the
  process of writing, so calling it here would read a value this same operation hasn't written yet

### Requirement: One-time rollup backfill, gated on the is_latest backfill being complete and verified

A one-time backfill SHALL populate `cyl_experiment_summary_counts` for every experiment that has matching
data, computed via the same aggregation the refresh mechanism uses. This backfill SHALL NOT run until
the `cyl-trait-writeback` capability's `is_latest` backfill has completed and been verified — running it
earlier would compute rollup counts from a still-incompletely-populated `is_latest` column, silently
under-counting experiments whose data hadn't yet been backfilled. Like the `is_latest` backfill, this
backfill SHALL be batched (by `experiment_id`) so no single transaction holds a lock for its full
duration.

#### Scenario: Rollup backfill matches a live per-experiment computation

- **WHEN** the rollup backfill runs against a database where `is_latest` has already been fully
  backfilled and verified
- **THEN** every resulting rollup row matches what a live `get_experiment_summary_counts(experiment_id_)`
  call would compute for that experiment

#### Scenario: Rollup backfill is not run before is_latest's own backfill is verified

- **WHEN** the deployment runbook sequences these migrations
- **THEN** the rollup backfill step is ordered strictly after the `is_latest` backfill's completeness
  verification step, not merely after its migration has been applied
