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
SHALL be invoked on a fixed schedule, independent of write volume.

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

#### Scenario: The cache is invoked on a schedule, not on every write

- **WHEN** trait data is written to `cyl_scan_traits`
- **THEN** no trigger on `cyl_scan_traits` invokes `refresh_cyl_experiment_trait_counts()` as a direct
  consequence of that write; the function is invoked only by an external schedule
