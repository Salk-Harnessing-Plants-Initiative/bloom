## ADDED Requirements

### Requirement: `cyl_pipeline_run_experiments` read view maps runs to the experiments their requested scans belong to
The database SHALL provide `public.cyl_pipeline_run_experiments(run_id, experiment_id, created_at)`, a `security_invoker` view. It lists the distinct `(run_id, experiment_id)` pairs reachable from `cyl_pipeline_run_scans` via `cyl_scans`, `cyl_plants`, `cyl_waves` and `cyl_experiments`. `created_at` is the run's `cyl_pipeline_runs.created_at`.

Its migration SHALL:
- set `lock_timeout` for its transaction;
- create `cyl_pipeline_run_scans_scan_id_idx` on `cyl_pipeline_run_scans(scan_id)`;
- `REVOKE ALL` on the view from `PUBLIC`, `anon`, `authenticated`, `service_role`, `bloom_user`, `bloom_writer`, `bloom_agent`, `bloom_admin` and `bloom_workflows`;
- then `GRANT SELECT` on the view to `bloom_user`, `bloom_agent` and `bloom_admin`;
- send `NOTIFY pgrst, 'reload schema'` after committing.

It SHALL ship with a companion rollback script that, under its own `lock_timeout`, drops the view and then the index. No table, column, RLS policy or publication is changed.

#### Scenario: A multi-scan request within one experiment yields one row
- **WHEN** a `scan_ids` run requests scans 10 and 11, both belonging to experiment 5
- **THEN** the view contains `(run_id, 5, <run created_at>)` exactly once

#### Scenario: A run spanning two experiments yields one row per experiment
- **WHEN** a run requests one scan from experiment 5 and one from experiment 6
- **THEN** the view contains exactly `(run_id, 5, …)` and `(run_id, 6, …)`

#### Scenario: Scans without a plant or a wave contribute no row
- **WHEN** a run's only requested scan has a null `plant_id`, or its plant has a null `wave_id`
- **THEN** the view contains no row for that run

#### Scenario: bloom_user reads the view through its own base-table policies
- **WHEN** rows are seeded as a superuser and read back after `SET LOCAL ROLE bloom_user`
- **THEN** the seeded `(run_id, experiment_id)` rows are returned

#### Scenario: Soft-deleted experiments are hidden from bloom_user
- **WHEN** the experiment of a run's only scan has `deleted_at` set
- **THEN** that run has no view row for `bloom_user`, but does for `bloom_admin`

#### Scenario: security_invoker enforces base-table access
- **WHEN** a role that holds `USAGE` on schema `public` and `SELECT` on the view, but no `SELECT` on `cyl_pipeline_run_scans`, queries the view
- **THEN** the query fails with insufficient privilege naming a base table

#### Scenario: Only the read roles hold any privilege on the view
- **WHEN** `has_table_privilege` is checked for `public`, `anon`, `authenticated`, `service_role`, `bloom_user`, `bloom_writer`, `bloom_agent`, `bloom_admin` and `bloom_workflows`
- **THEN** `SELECT` is true exactly for `bloom_user`, `bloom_agent`, `bloom_admin` and (inherited from `bloom_user`) `bloom_writer`
- **AND** `INSERT`, `UPDATE`, `DELETE` and `TRUNCATE` are false for all of them

#### Scenario: Rollback removes the objects and the migration re-applies cleanly
- **WHEN** the rollback is applied after the migration, and then the migration is applied again
- **THEN** after the rollback neither `cyl_pipeline_run_experiments` nor `cyl_pipeline_run_scans_scan_id_idx` exists
- **AND** after the re-apply both exist again
