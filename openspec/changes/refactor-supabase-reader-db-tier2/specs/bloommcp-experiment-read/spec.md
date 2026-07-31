## MODIFIED Requirements

### Requirement: SupabaseReader Adapter

The system SHALL provide a `SupabaseReader` adapter implementing `ExperimentReader` that
preserves the deployed cleaned-output read behaviour — versioned-cleaned outputs from
Supabase Storage under `bloommcp_output/` as `bloom_agent` — and resolves its **raw** tier
by querying Bloom's Postgres tables directly (`get_experiment_traits`,
`list_experiment_trait_sources`) rather than reading a local `BLOOM_TRAITS_DIR` CSV. The raw
tier is DB-only: `name` is parsed as `str(experiment_id)`; a non-numeric or unresolvable
`name` with no cleaned output is a structured not-found condition, not a local-disk
fallback. Every raw read — pinned or not — resolves and pins exactly one concrete DB source
before fetching, so no returned frame ever mixes rows from more than one source, and
records that resolved source on the returned `ExperimentFrame` (`resolved_source`) so a
caller can stamp accurate provenance without an independent, racy re-resolution. A
long→wide pivot that produces colliding `sample_id` values across distinct plants is a
structured error, not a silently ambiguous frame, and so is more than one scan for the same
plant within a resolved source. `list_experiments()` enumerates
experiments from `cyl_experiments` rather than scanning a local directory or bucket, with
each entry's `filename` equal to the same `str(experiment_id)` shape `load_experiment`
accepts. `SupabaseReader` no longer implements `RawSourced` (there is no on-disk path for a
DB-backed raw read to content-address); it implements `SourceSelectable` instead.

#### Scenario: Resolves the latest versioned cleaned output from Supabase

- **WHEN** `SupabaseReader.load_experiment(name)` is called and a versioned `qc_<stem>`
  manifest with a `latest` cleaned output exists
- **THEN** it downloads and returns that cleaned CSV from Supabase Storage, with a source
  label identifying the version

#### Scenario: Resolves the raw tier from the database, wide-pivoted

- **WHEN** `SupabaseReader.load_experiment(name)` is called for `name` shaped as
  `str(experiment_id)` with no cleaned output
- **THEN** it resolves a concrete source (see "One source per frame, never mixed"), fetches
  long-format trait rows via `get_experiment_traits` pinned to that source, pivots them
  wide (one column per distinct trait name), renames columns to canonical roles
  (`genotype` from `accessions.name`, `sample_id` from `cyl_plants.qr_code`), retains
  `cyl_plants.id` as a metadata column, and returns the resulting frame with source label
  `"raw"` — no local disk or Storage bucket is read for this tier

#### Scenario: Non-numeric raw-tier name is not-found, not a local fallback

- **WHEN** `SupabaseReader.load_experiment(name)` is called for a `name` that does not
  parse as `str(experiment_id)` and has no cleaned output
- **THEN** it raises `ExperimentNotFoundError` — it SHALL NOT read a local `BLOOM_TRAITS_DIR`
  CSV as a fallback

#### Scenario: An empty raw read is valid, not not-found

- **WHEN** `SupabaseReader.load_experiment(name)` resolves `name` to a real experiment that
  has zero trait rows recorded
- **THEN** it returns a frame with zero trait columns rather than raising
  `ExperimentNotFoundError` — the experiment exists; it simply has no measurements yet

#### Scenario: One source per frame, never mixed

- **WHEN** `SupabaseReader.load_experiment(name)` resolves a raw read, pinned or not
- **THEN** it first resolves exactly one concrete `source_id` (the explicit pin, or
  whatever `resolve_source` treats as latest for the whole experiment) and passes that
  single id as an explicit pin to `get_experiment_traits` — it SHALL NOT call
  `get_experiment_traits` unpinned and rely on the RPC's own per-scan `is_latest`
  disjunction to avoid mixing sources across scans

#### Scenario: The resolved frame records which source it actually consulted

- **WHEN** `SupabaseReader.load_experiment(name)` resolves a raw-tier read
- **THEN** the returned `ExperimentFrame.resolved_source` equals the `SourceInfo` that was
  actually pinned for that read — not re-resolved independently by any caller — and stays
  fixed to that value even if a newer source becomes available before the caller acts on
  the frame

#### Scenario: A cleaned-tier read carries no source identity

- **WHEN** `SupabaseReader.load_experiment(name)` resolves a cleaned-tier read (a Storage
  read, not the raw DB tier)
- **THEN** the returned `ExperimentFrame.resolved_source` is `None` — that read never
  consulted a DB source, so recording one would misattribute lineage

#### Scenario: Multiple scans for one plant is a structured error

- **WHEN** the resolved source's rows include more than one `scan_id` for the same
  `plant_id`
- **THEN** `SupabaseReader.load_experiment` raises `MultipleScansPerPlantError` rather than
  silently keying the pivot by `(scan_id, plant_id)` or picking one scan arbitrarily

#### Scenario: Concurrent source_id and run_id pin is rejected before any DB call

- **WHEN** `SupabaseReader.load_experiment(name, source_id=<id>, run_id=<id>)` is called
  with both set
- **THEN** it raises `AmbiguousSourceSelectionError` without calling `get_experiment_traits`
  — the caller never sees a raw Postgres `RAISE EXCEPTION` message

#### Scenario: An explicit pin matching nothing is a hard error

- **WHEN** `SupabaseReader.load_experiment(name, source_id=<id>)` or `load_experiment(name,
  run_id=<id>)` is called for a pin that resolves no rows
- **THEN** it raises `ExperimentNotFoundError` rather than returning a silent empty frame —
  an explicit pin the caller expected to resolve failing is a caller-visible condition

#### Scenario: Colliding sample identity is a structured error, not a silent merge

- **WHEN** the long→wide pivot produces two or more rows sharing the same `sample_id`
  (`cyl_plants.qr_code`) value — possible because `qr_code` is unique only within a wave,
  not experiment-wide
- **THEN** `SupabaseReader.load_experiment` raises `AmbiguousSampleIdentityError` naming the
  colliding value, rather than returning a frame where two physically distinct plants share
  one `sample_id`

#### Scenario: DB-backed reader no longer satisfies RawSourced

- **WHEN** `isinstance(SupabaseReader(), RawSourced)` is checked
- **THEN** it is `False` — the raw tier has no on-disk path to content-address; callers that
  gate on `RawSourced` for input hashing (e.g. `tools._ports.raw_source_for`) correctly
  treat `SupabaseReader` as path-less and record no fabricated path

#### Scenario: List experiments enumerates database experiments

- **WHEN** `SupabaseReader.list_experiments()` is called
- **THEN** it returns `ExperimentSummary` entries sourced from `cyl_experiments`, each with
  `filename` equal to `str(experiment_id)` (so it round-trips unchanged through
  `load_experiment`) and non-placeholder `rows`/`trait_columns`/`total_columns` counts,
  rather than scanning a local directory or Storage bucket

#### Scenario: A per-experiment listing failure excludes that experiment, not the whole list

- **WHEN** `SupabaseReader.list_experiments()` fails to fetch trait data for one experiment
  while enumerating several
- **THEN** that experiment is excluded from the returned list (logged server-side) rather
  than the whole call raising or returning a misleading placeholder count for it

#### Scenario: Adapter tests do not touch the network

- **WHEN** the `SupabaseReader` test suite runs
- **THEN** it exercises the adapter against a monkeypatched `supabase_client` boundary (no
  `supabase.create_client` call, no live Postgres/PostgREST connection) and passes with no
  `SUPABASE_URL`/`BLOOM_AGENT_KEY` configured

## ADDED Requirements

### Requirement: SourceSelectable Capability

The system SHALL define an optional, `isinstance`-gated adapter capability
`SourceSelectable` in `data_access/ports.py`, mirroring the existing `RawSourced` capability
pattern, so a reader backed by a source/run-versioned substrate can advertise and honor
explicit source/run pinning without widening the core `ExperimentReader` Protocol.
`SourceSelectable` SHALL expose `list_sources(name) -> list[SourceInfo]` (enumerating
available `(source_id, source_name, pipeline_run_id)` tuples for an experiment) and
`resolve_source(name, *, source_id=None, run_id=None) -> Optional[SourceInfo]` (resolving
which source actually backs a read, honoring an explicit pin or defaulting to latest, and
returning `None` when the experiment has only legacy, pre-source-tracking data with no
`source_id` to report). Adapters without a source-versioned substrate (e.g. `FakeReader`,
`LocalReader`) SHALL NOT implement it.

#### Scenario: Capability is discoverable via isinstance

- **WHEN** `isinstance(SupabaseReader(), SourceSelectable)` and
  `isinstance(FakeReader(), SourceSelectable)` are checked
- **THEN** the former is `True` and the latter is `False`

#### Scenario: Source/run discovery

- **WHEN** `SupabaseReader().list_sources(name)` is called for a known experiment with
  multiple contributing sources
- **THEN** it returns the distinct `(source_id, source_name, pipeline_run_id)` tuples for
  that experiment

#### Scenario: Unpinned resolution yields a concrete source when one exists

- **WHEN** `SupabaseReader().resolve_source(name)` is called with no `source_id`/`run_id`
  for an experiment with at least one tracked (`source_id IS NOT NULL`) trait row
- **THEN** it returns the `SourceInfo` for whatever `get_experiment_traits` treats as
  latest for that experiment

#### Scenario: Legacy-only data resolves to no source, not an error

- **WHEN** `SupabaseReader().resolve_source(name)` is called for an experiment whose trait
  rows are all legacy (`source_id IS NULL`, so `list_experiment_trait_sources` returns an
  empty list for it)
- **THEN** it returns `None` rather than raising or fabricating a `SourceInfo` —
  `load_experiment(name)` still succeeds using that legacy data, recording no source
  identity
