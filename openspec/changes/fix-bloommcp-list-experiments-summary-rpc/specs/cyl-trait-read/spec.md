## ADDED Requirements

### Requirement: Aggregate experiment summary counts

Bloom SHALL provide `get_experiment_summary_counts(experiment_id_ BIGINT DEFAULT NULL, source_id_ BIGINT
DEFAULT NULL, run_id_ TEXT DEFAULT NULL)` returning `(experiment_id, n_plants, n_traits)` rows, computed
server-side via `COUNT(DISTINCT plant_id)`/`COUNT(DISTINCT trait_name)` over the same join chain and
latest/`source_id`/`run_id` selection disjunction `get_experiment_traits` uses against
`cyl_scan_traits_source`, so its unpinned counts agree with what a caller deriving the same counts from
`get_experiment_traits`'s own rows would compute, for any experiment both functions can read. With
`experiment_id_ NULL`, the function SHALL return one row per experiment that has at least one matching
trait row under the given source/run selection — an experiment with none SHALL be absent from the result
set, not present with zero-valued counts. Supplying both `source_id_` and `run_id_` SHALL raise an error,
matching `get_experiment_traits`'s existing mutual-exclusion guard. Results SHALL be scoped to
`experiment_id_` when given; with `experiment_id_ NULL`, results SHALL cover every experiment in one
call.

#### Scenario: Unpinned counts match get_experiment_traits' latest semantics

- **WHEN** `get_experiment_summary_counts(experiment_id_)` and `get_experiment_traits(experiment_id_)`
  are both called (no source/run arguments) for the same experiment
- **THEN** `n_plants` equals the number of distinct `plant_id` values and `n_traits` equals the number of
  distinct `trait_name` values `get_experiment_traits` returns for that experiment

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
  `cyl_experiments` table, in a single call — no per-experiment round trip is required

#### Scenario: Non-finite trait values still count toward n_traits

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

### Requirement: Bulk read grants match the existing per-trait read surface

`get_experiment_summary_counts` SHALL have its `EXECUTE` privilege explicitly `REVOKE`d from `PUBLIC`
and `GRANT`ed to exactly the four existing read roles (`bloom_agent`, `bloom_user`, `bloom_admin`,
`authenticated`), matching `get_experiment_traits`'s round-1-review-tightened posture rather than
`get_scan_traits`'s older implicit-`PUBLIC` default. This change SHALL NOT add, drop, or alter any
row-level-security policy or any write grant on `cyl_experiments`, `cyl_waves`, `cyl_plants`,
`accessions`, `cyl_scans`, `cyl_scan_traits`, or `cyl_trait_sources`.

#### Scenario: The four read roles can call the new function

- **WHEN** a session assumes each of `bloom_agent`, `bloom_user`, `bloom_admin`, and `authenticated` and
  calls `get_experiment_summary_counts`
- **THEN** each call is permitted, through the same join chain `get_experiment_traits` already reads

#### Scenario: No write capability is added

- **WHEN** the migration is applied
- **THEN** no new policy or grant permits any role to write any table in the join chain that could not
  already do so before the change

### Requirement: Additive, non-destructive migration with a companion rollback

The migration adding `get_experiment_summary_counts` SHALL be additive only — it MUST NOT drop or rewrite
any existing table, view, function, column, or data. `CREATE OR REPLACE FUNCTION` SHALL be used so the
migration body is safely re-runnable. A companion manual rollback script SHALL be provided under
`supabase/rollbacks/` that drops exactly the new function, by its full argument signature, leaving every
pre-existing read object (`get_experiment_traits`, `get_scan_traits`, `list_experiment_trait_sources`,
`cyl_scan_traits_source`, `cyl_scan_traits_latest`) unchanged.

#### Scenario: Forward migration adds the function without touching existing objects

- **WHEN** the migration is applied to a database that already has `get_experiment_traits`
- **THEN** `get_experiment_summary_counts` is created and no pre-existing table, view, or function is
  altered

#### Scenario: Migration is idempotent on re-apply

- **WHEN** the migration body is re-applied to a database where it was already applied
- **THEN** `get_experiment_summary_counts` still exists with the same signature, and
  `get_experiment_traits`/`get_scan_traits`/the existing views are unchanged

#### Scenario: Rollback removes exactly the new function

- **WHEN** the companion rollback script is applied to a database where the migration had been applied
- **THEN** `get_experiment_summary_counts` no longer exists, and every pre-existing read object
  (`get_experiment_traits`, `get_scan_traits`, `list_experiment_trait_sources`, `cyl_scan_traits_source`,
  `cyl_scan_traits_latest`) is unchanged; re-applying the forward migration restores the function
