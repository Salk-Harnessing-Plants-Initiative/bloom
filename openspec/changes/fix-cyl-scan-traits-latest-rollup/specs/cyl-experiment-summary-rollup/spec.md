## ADDED Requirements

### Requirement: cyl_experiment_trait_counts caches n_traits per experiment

Bloom SHALL maintain a `cyl_experiment_trait_counts` table (`experiment_id BIGINT PRIMARY KEY REFERENCES
cyl_experiments(id) ON DELETE CASCADE`, `n_traits INT NOT NULL`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT
now()`) holding, for every experiment that has at least one matching latest-source trait, the count of
distinct trait ids among plants with a non-null accession — the same value
`get_experiment_traits(experiment_id_)`'s distinct `trait_name` count would compute for that experiment.
An experiment with no matching data SHALL have no row in this table (absent, not zero-valued), matching
`get_experiment_summary_counts`'s existing "absent if zero" contract. This table SHALL NOT be maintained
by a per-write trigger on `cyl_scan_traits` — a single write-back call inserts on the order of hundreds of
trait rows in a loop, and a per-row trigger recomputing a whole experiment's aggregate on every one of
those rows would fire that many full-experiment recomputes for one call. Instead, a
`refresh_cyl_experiment_trait_counts()` function SHALL recompute every experiment's count in one pass
(delete-then-reinsert, so an experiment that drops to zero matching traits is removed from the table) and
SHALL be invoked by an external trigger independent of write volume — an automatic fixed schedule for
production, and on-demand dispatch (no automatic schedule) for staging, since staging's write volume
does not currently warrant one. Concurrent invocations SHALL be
serialized (e.g. via a fixed-key advisory lock, since the refresh always rebuilds the whole table rather
than one identifiable row) so that two overlapping calls cannot race the delete-then-reinsert into a
primary-key conflict. Row-level security SHALL be enabled on
this table with the same policy set as `cyl_scan_traits` itself (`bloom_admin` full access,
`bloom_agent`/`bloom_user`/`authenticated` read-only, all permissive) — an unauthenticated (`anon`) caller
SHALL NOT be able to read or write this table, regardless of any table-level grant Supabase applies by
default to new tables. `refresh_cyl_experiment_trait_counts()` itself SHALL NOT be callable by `anon`,
`authenticated`, or any of the four read roles — only by whatever identity runs the schedule.

#### Scenario: A refresh populates counts matching a live computation

- **WHEN** `refresh_cyl_experiment_trait_counts()` is called against a database with existing latest-source
  trait data
- **THEN** every experiment with matching data gets a `cyl_experiment_trait_counts` row whose `n_traits`
  equals a live per-experiment distinct-trait-id computation for that experiment

#### Scenario: An experiment with no matching data has no row after a refresh

- **WHEN** `refresh_cyl_experiment_trait_counts()` is called and an experiment has no scans with any
  latest-source trait data
- **THEN** that experiment has no row in `cyl_experiment_trait_counts` after the refresh (not a
  zero-valued row)

#### Scenario: An experiment that loses all its trait data is removed on the next refresh

- **WHEN** an experiment previously had a `cyl_experiment_trait_counts` row and all of its scans'
  latest-source trait data is subsequently deleted
- **THEN** the next `refresh_cyl_experiment_trait_counts()` call removes that experiment's row rather than
  leaving it at a stale nonzero value

#### Scenario: A refresh reflects reruns, not the previously-latest source

- **WHEN** a rerun changes which source is latest for some of an experiment's scans between two refreshes
- **THEN** the next refresh's `n_traits` for that experiment reflects the new latest source's traits, not
  the prior one's

#### Scenario: Cross-experiment isolation

- **WHEN** `refresh_cyl_experiment_trait_counts()` is called against data spanning multiple experiments
- **THEN** each experiment's row reflects only that experiment's own scans, plants, and traits

#### Scenario: A plant with no accession is excluded, matching get_experiment_traits

- **WHEN** an experiment has a plant whose `accession_id` is `NULL`, alongside other plants that do have
  an accession
- **THEN** a refresh excludes that plant's scans and traits from `n_traits` for the experiment, matching
  `get_experiment_traits`'s own exclusion of the same plant

#### Scenario: The cache is never invoked directly from a raw trait write, regardless of trigger type

- **WHEN** trait data is written to `cyl_scan_traits`
- **THEN** no trigger on `cyl_scan_traits` invokes `refresh_cyl_experiment_trait_counts()` as a direct
  consequence of that write; the function is invoked only by an external schedule or an on-demand
  dispatch, never by the write path itself

#### Scenario: Production refreshes on an automatic schedule; staging remains dispatch-only

- **WHEN** the refresh mechanism's trigger source is a production schedule event
- **THEN** `refresh_cyl_experiment_trait_counts()` is invoked automatically against production on that
  schedule, with no equivalent automatic trigger configured for staging — staging's cache is refreshed
  only when explicitly dispatched

#### Scenario: Concurrent refresh calls do not raise a duplicate-key error

- **WHEN** two calls to `refresh_cyl_experiment_trait_counts()` overlap (e.g. a manual invocation landing
  while a scheduled one is still in flight), with both transactions in flight before either commits
- **THEN** neither call raises an error, and after both complete `cyl_experiment_trait_counts` holds a
  single, consistent, correct row per experiment with matching data — not a primary-key violation from
  the second call's `INSERT` colliding with the first's already-committed rows

#### Scenario: An unauthenticated caller cannot read cyl_experiment_trait_counts or invoke its refresh

- **WHEN** an `anon` (unauthenticated) caller selects from `cyl_experiment_trait_counts`, attempts to
  write to it directly, or attempts to call `refresh_cyl_experiment_trait_counts()`
- **THEN** the `SELECT` returns zero rows regardless of how much real data exists, any direct write is
  rejected by row-level security, and the function call is rejected for lacking `EXECUTE` privilege — even
  though Supabase's default privileges would otherwise grant `anon` both a raw table-level write grant and
  `EXECUTE` on the newly-created function

#### Scenario: An unauthenticated caller cannot TRUNCATE cyl_experiment_trait_counts

- **WHEN** an `anon` (unauthenticated) caller attempts `TRUNCATE public.cyl_experiment_trait_counts`
- **THEN** the statement is rejected for lacking `TRUNCATE` privilege — row-level security does not govern
  `TRUNCATE` at all (a Postgres limitation, not a policy gap), so this privilege must be revoked explicitly
  the same way as `cyl_scan_latest_source`'s equivalent scenario
