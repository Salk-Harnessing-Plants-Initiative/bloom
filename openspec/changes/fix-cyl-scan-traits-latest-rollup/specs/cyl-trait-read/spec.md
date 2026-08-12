## MODIFIED Requirements

### Requirement: Canonical source-aware trait view

Bloom SHALL provide a `cyl_scan_traits_source` view that is the single source of truth for
source-aware cyl scan-trait reads. It SHALL expose one row per `cyl_scan_traits` row with the columns
`scan_id`, `trait_id`, `trait_name` (the resolved `cyl_traits.name`), `value`, `source_id`,
`source_name` (the producing `cyl_trait_sources.name`), `pipeline_run_id` (the batch key
`cyl_trait_sources.metadata->>'pipeline_run_id'`, nullable), and a boolean `is_latest`. `is_latest`
SHALL be true exactly when the row's `source_id` equals `max(source_id) OVER (PARTITION BY scan_id)`,
compared with `IS NOT DISTINCT FROM` so that a scan whose rows all have a `NULL` `source_id` (legacy
data) is treated as latest. "Latest" is defined as `max(source_id)` per scan because `cyl_trait_sources`
has no timestamp column and identity ids increase monotonically as reprocessing mints new sources. The
partition is per `scan_id`, not per `(scan_id, trait_id)` — a newer source that only re-delivers a
subset of a scan's traits does not "backfill" its missing traits from an older source (see the
"Latest-source-by-default scan trait reads" requirement's own scenario for this; it is intentional, not
an accident of the view's original implementation).

`is_latest` SHALL be backed by a stored, indexed boolean column on `cyl_scan_traits`, maintained by a
trigger on every write to that table (see the `cyl-trait-writeback` capability's write-side maintenance
requirement) — not recomputed per query. The view SHALL expose that column's value directly; this is an
implementation change from the view's prior live `WindowAgg` computation, and does not change the
selection rule or its result for any given row. The view SHALL use `security_invoker`, SHALL be granted
`SELECT` to the read roles, and SHALL be the only read-facing place the latest-selection rule is exposed;
every other read object is built on it. Exposing `trait_name`/`source_id`/`is_latest` makes the view
directly usable for scan-grain reads.

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

#### Scenario: The view's is_latest matches the stored column, not a fresh recomputation

- **WHEN** `cyl_scan_traits.is_latest` has been correctly maintained (by the trigger, or by the one-time
  backfill, for a row not touched since) for a scan's rows
- **THEN** `cyl_scan_traits_source.is_latest` for those rows equals the stored column's value, and no
  per-query window-function computation is performed to produce it

### Requirement: Aggregate experiment summary counts

Bloom SHALL provide `get_experiment_summary_counts(experiment_id_ BIGINT DEFAULT NULL, source_id_ BIGINT
DEFAULT NULL, run_id_ TEXT DEFAULT NULL)` returning `(experiment_id, n_plants, n_traits)` rows. With
`source_id_` and `run_id_` both `NULL` (the "current latest" case, whether or not `experiment_id_` is
pinned), the function SHALL read the result directly from the experiment summary rollup table (the
`cyl-experiment-summary-rollup` capability) rather than computing a live join — this is the read path
`list_experiments()` and any future no-override, single-experiment caller uses. With `source_id_` or
`run_id_` set, the function SHALL compute the counts via a live join over the same join chain and
selection disjunction `get_experiment_traits` uses against `cyl_scan_traits_source`
(`cyl_experiments → cyl_waves → cyl_plants → accessions → cyl_scans → cyl_scan_traits_source`), using a
`GROUP BY` subquery over `SELECT DISTINCT` pairs to compute the distinct plant/trait counts rather than
`COUNT(DISTINCT ...)`, so its pinned-source/pinned-run counts agree with what a caller deriving the same
counts from `get_experiment_traits`'s own rows would compute. With `experiment_id_ NULL`, the function
SHALL return one row per experiment that has at least one matching trait row under the given
source/run selection — an experiment with none SHALL be absent from the result set, not present with
zero-valued counts. Supplying both `source_id_` and `run_id_` SHALL raise an error, matching
`get_experiment_traits`'s existing mutual-exclusion guard. Results SHALL be scoped to `experiment_id_`
when given; with `experiment_id_ NULL`, results SHALL cover every experiment in one call.

#### Scenario: Unpinned counts match get_experiment_traits' latest semantics

- **WHEN** `get_experiment_summary_counts(experiment_id_)` and `get_experiment_traits(experiment_id_)`
  are both called (no source/run arguments) for the same experiment
- **THEN** `n_plants` equals the number of distinct `plant_id` values and `n_traits` equals the number of
  distinct `trait_name` values `get_experiment_traits` returns for that experiment

#### Scenario: No-override calls read the rollup, not a live join

- **WHEN** `get_experiment_summary_counts` is called with `source_id_` and `run_id_` both `NULL`
  (pinned to one experiment, or unpinned across all experiments)
- **THEN** the returned counts come from the experiment summary rollup table's current contents for the
  relevant experiment(s), and no live join over `cyl_experiments`/`cyl_waves`/`cyl_plants`/`cyl_scans`/
  `cyl_scan_traits_source` is evaluated to produce them

#### Scenario: Pinning a source matches get_experiment_traits byte-for-byte

- **WHEN** `get_experiment_summary_counts(experiment_id_, source_id_=X)` and
  `get_experiment_traits(experiment_id_, source_id_=X)` are both called for the same source
- **THEN** the counts agree with the distinct plant/trait counts of `get_experiment_traits`'s rows for
  source `X`

#### Scenario: Run grouping matches get_experiment_traits byte-for-byte

- **WHEN** `get_experiment_summary_counts(experiment_id_, run_id_=R)` and
  `get_experiment_traits(experiment_id_, run_id_=R)` are both called for the same run
- **THEN** the counts agree with the distinct plant/trait counts of `get_experiment_traits`'s rows for
  run `R`, including for an experiment whose run `R` values were later superseded by a newer run

#### Scenario: Supplying both source_id_ and run_id_ is rejected

- **WHEN** `get_experiment_summary_counts` is called with both `source_id_` and `run_id_` non-null
- **THEN** the call raises an error and returns no rows

#### Scenario: An experiment with no matching trait rows is absent, not zero-valued

- **WHEN** `get_experiment_summary_counts` is called (pinned or unpinned) for an experiment with no
  `cyl_scan_traits` rows reachable under the given source/run selection
- **THEN** the result set contains no row for that experiment

#### Scenario: Bulk unpinned call covers every experiment in one round trip

- **WHEN** `get_experiment_summary_counts()` is called with `experiment_id_ NULL`
- **THEN** it returns a row for every experiment that has matching trait data, across the whole
  `cyl_experiments` table, in a single call — no per-experiment round trip and no per-call join or
  window-function evaluation is required

#### Scenario: A NULL-valued trait still counts toward n_traits

- **WHEN** the latest source for a scan stored a `NULL` value for a trait
- **THEN** `get_experiment_summary_counts` still counts that trait name toward `n_traits` for the
  experiment (a null value does not make the trait invisible to the count)

#### Scenario: Cross-experiment isolation

- **WHEN** `get_experiment_summary_counts` is called for experiment A (pinned or unpinned)
- **THEN** the returned counts never include plants or traits belonging to any other experiment's scans

#### Scenario: A plant with no accession is excluded, matching get_experiment_traits

- **WHEN** an experiment has a plant whose `accession_id` is `NULL`, alongside other plants that do have
  an accession
- **THEN** `get_experiment_summary_counts` excludes that plant's scans and traits from `n_plants`/
  `n_traits`, matching `get_experiment_traits`'s own exclusion of the same plant
