## MODIFIED Requirements

### Requirement: Canonical source-aware trait view

Bloom SHALL provide a `cyl_scan_traits_source` view that is the single source of truth for
source-aware cyl scan-trait reads. It SHALL expose one row per `cyl_scan_traits` row with the columns
`scan_id`, `trait_id`, `trait_name` (the resolved `cyl_traits.name`), `value`, `source_id`,
`source_name` (the producing `cyl_trait_sources.name`), `pipeline_run_id` (the batch key
`cyl_trait_sources.metadata->>'pipeline_run_id'`, nullable), and a boolean `is_latest`. `is_latest`
SHALL be true exactly when the row's `source_id` equals `max(source_id)` **per scan**, compared with
`IS NOT DISTINCT FROM` so that a scan whose rows all have a `NULL` `source_id` (legacy data) is treated
as latest. "Latest" is defined as `max(source_id)` per scan because `cyl_trait_sources` has no timestamp
column and identity ids increase monotonically as reprocessing mints new sources. This value SHALL be
computed by joining to a `cyl_scan_latest_source` table (one row per scan, holding that scan's current
`max_source_id`) rather than by a live per-query window aggregate over all of `cyl_scan_traits` — the
selection rule itself (per-scan partition grain, `IS NOT DISTINCT FROM` NULL handling) is unchanged; only
where the value is computed from changes, so every row of `cyl_scan_traits` continues to have exactly one
`is_latest = true` row per scan and the view's output for any given data is unchanged. `cyl_scan_traits`'s
own write path is responsible for keeping `cyl_scan_latest_source` correct on every write (see the
`cyl-trait-writeback` capability); this view never computes or stores that value itself. The view SHALL
use `security_invoker`, SHALL be granted `SELECT` to the read roles, and SHALL be the only place the
latest-selection _rule_ is defined; every other read object is built on it. Exposing
`trait_name`/`source_id`/`is_latest` makes the view directly usable for scan-grain reads.

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

#### Scenario: is_latest computed via the stored table matches the prior live computation

- **WHEN** the view is queried after `cyl_scan_latest_source` has been backfilled for all pre-existing
  scans
- **THEN** every row's `is_latest` value equals what a live `max(source_id) OVER (PARTITION BY scan_id)`
  computation over the same data would produce

### Requirement: Aggregate experiment summary counts

Bloom SHALL provide `get_experiment_summary_counts(experiment_id_ BIGINT DEFAULT NULL, source_id_ BIGINT
DEFAULT NULL, run_id_ TEXT DEFAULT NULL)` returning `(experiment_id, n_plants, n_traits,
n_traits_updated_at)` rows. With `source_id_` and `run_id_` both `NULL` (the "current latest" case, pinned
to one experiment or covering every experiment), `n_plants` SHALL be computed live as the count of
distinct plants whose accession is non-null and at least one of whose scans has any `cyl_scan_traits`
row, and `n_traits` SHALL be read from a maintained cache of distinct latest-source trait counts per
experiment, refreshed on an interval rather than recomputed on every call — so `n_traits` MAY lag
newly-written trait data by up to one refresh interval once that refresh schedule is actually running,
while `n_plants` is always current. `n_traits_updated_at` SHALL report that cache row's own last-refreshed
timestamp for the unpinned case (or `NULL` if the cache has never been populated for that experiment), so
a caller can determine how stale `n_traits` actually is rather than assuming a fixed bound. With either
`source_id_` or `run_id_` set, both counts SHALL be computed live against that pinned source/run
selection, matching `get_experiment_traits(experiment_id_, source_id_, run_id_)`'s own selection
semantics, with no caching, and `n_traits_updated_at` SHALL be `NULL` (a pinned call is always live, with
no cache staleness to report). With `experiment_id_ NULL`, the function SHALL return one row per
experiment that has at least one matching trait row under the given source/run selection — an experiment
with none SHALL be absent from the result set, not present with zero-valued counts. Supplying both
`source_id_` and `run_id_` SHALL raise an error, matching `get_experiment_traits`'s existing
mutual-exclusion guard. Results SHALL be scoped to `experiment_id_` when given; with `experiment_id_
NULL`, results SHALL cover every experiment in one call.

#### Scenario: Unpinned n_plants matches get_experiment_traits' latest semantics, live

- **WHEN** `get_experiment_summary_counts(experiment_id_)` and `get_experiment_traits(experiment_id_)`
  are both called (no source/run arguments) for the same experiment
- **THEN** `n_plants` equals the number of distinct `plant_id` values `get_experiment_traits` returns for
  that experiment, computed from the current data with no caching delay

#### Scenario: Unpinned n_traits reflects the last completed refresh

- **WHEN** new trait data is written for an experiment and `get_experiment_summary_counts(experiment_id_)`
  is called before the next scheduled refresh has run
- **THEN** `n_traits` reflects the count as of the last completed refresh, not the just-written data —
  and once a refresh has run since that write, `n_traits` matches
  `get_experiment_traits(experiment_id_)`'s distinct `trait_name` count exactly

#### Scenario: Pinning a source matches get_experiment_traits byte-for-byte, live

- **WHEN** `get_experiment_summary_counts(experiment_id_, source_id_=X)` and
  `get_experiment_traits(experiment_id_, source_id_=X)` are both called for the same source
- **THEN** both counts agree with the distinct plant/trait counts of `get_experiment_traits`'s rows for
  source `X`, computed live with no caching

#### Scenario: Run grouping matches get_experiment_traits byte-for-byte, live

- **WHEN** `get_experiment_summary_counts(experiment_id_, run_id_=R)` and
  `get_experiment_traits(experiment_id_, run_id_=R)` are both called for the same run
- **THEN** both counts agree with the distinct plant/trait counts of `get_experiment_traits`'s rows for
  run `R`, including for an experiment whose run `R` values were later superseded by a newer run

#### Scenario: Supplying both source*id* and run*id* is rejected

- **WHEN** `get_experiment_summary_counts` is called with both `source_id_` and `run_id_` non-null
- **THEN** the call raises an error and returns no rows

#### Scenario: An experiment with no matching trait rows is absent, not zero-valued

- **WHEN** `get_experiment_summary_counts` is called (pinned or unpinned) for an experiment with no
  `cyl_scan_traits` rows reachable under the given source/run selection
- **THEN** the result set contains no row for that experiment

#### Scenario: Bulk unpinned call covers every experiment in one round trip

- **WHEN** `get_experiment_summary_counts()` is called with `experiment_id_ NULL`
- **THEN** it returns a row for every experiment that has matching trait data, across the whole
  `cyl_experiments` table, in a single call — no per-experiment round trip is required

#### Scenario: A NULL-valued trait still counts toward n_traits

- **WHEN** the latest source for a scan stored a `NULL` value for a trait
- **THEN** `get_experiment_summary_counts` still counts that trait name toward `n_traits` for the
  experiment (a null value does not make the trait invisible to the count), once a refresh has run for
  the unpinned case, or immediately for a pinned call

#### Scenario: Cross-experiment isolation

- **WHEN** `get_experiment_summary_counts` is called for experiment A (pinned or unpinned)
- **THEN** the returned counts never include plants or traits belonging to any other experiment's scans

#### Scenario: A plant with no accession is excluded, matching get_experiment_traits

- **WHEN** an experiment has a plant whose `accession_id` is `NULL`, alongside other plants that do have
  an accession
- **THEN** `get_experiment_summary_counts` excludes that plant's scans and traits from `n_plants`/
  `n_traits`, matching `get_experiment_traits`'s own exclusion of the same plant, whether computed live
  (`n_plants`, always; both counts when pinned) or via the refreshed cache (`n_traits`, unpinned)

#### Scenario: A plant with an accession but a scan with zero trait rows is excluded from n_plants

- **WHEN** an experiment has a plant with a valid, non-null accession, but that plant's scan has never
  had any `cyl_scan_traits` row delivered for it
- **THEN** the unpinned `n_plants` count excludes that plant, the same way a null-accession plant is
  excluded — a valid accession alone is not sufficient; the plant's scan must have matching trait data

#### Scenario: n_traits_updated_at reports the cache's own staleness, not a fixed assumption

- **WHEN** `get_experiment_summary_counts(experiment_id_)` is called unpinned, after at least one refresh
  has populated that experiment's cache row
- **THEN** `n_traits_updated_at` equals that cache row's own `updated_at` timestamp exactly, so a caller
  can determine precisely how stale `n_traits` is rather than assuming any fixed bound

#### Scenario: n_traits_updated_at is NULL when the cache has never been populated

- **WHEN** `get_experiment_summary_counts(experiment_id_)` is called unpinned for an experiment whose
  trait-count cache row has never been populated by any refresh
- **THEN** `n_traits_updated_at` is `NULL`, distinct from a real timestamp, so a caller cannot mistake an
  unrefreshed count for a recently-refreshed one

#### Scenario: n_traits_updated_at is NULL for a pinned call

- **WHEN** `get_experiment_summary_counts` is called with `source_id_` or `run_id_` set
- **THEN** `n_traits_updated_at` is `NULL` — a pinned call is always computed live, with no cache
  involved, so there is no staleness to report

#### Scenario: An unauthenticated caller cannot execute get_experiment_summary_counts or its live helper

- **WHEN** an `anon` (unauthenticated) caller attempts to call `get_experiment_summary_counts` or the
  internal `compute_cyl_experiment_summary_counts_live` helper it delegates to for pinned calls
- **THEN** both calls are rejected for lacking `EXECUTE` privilege — even though Supabase's default
  privileges would otherwise grant `anon` `EXECUTE` on both, and the helper is `SECURITY DEFINER`, so an
  `anon` caller invoking it directly would otherwise run with the definer's elevated privilege rather than
  being stopped by `anon`'s own lack of table-level access
