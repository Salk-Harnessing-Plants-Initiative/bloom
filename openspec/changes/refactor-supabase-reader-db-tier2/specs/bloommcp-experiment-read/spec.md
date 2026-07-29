## MODIFIED Requirements

### Requirement: SupabaseReader Adapter

The system SHALL provide a `SupabaseReader` adapter implementing `ExperimentReader` that
preserves the deployed cleaned-output read behaviour — versioned-cleaned outputs from
Supabase Storage under `bloommcp_output/` as `bloom_agent` — and resolves its **raw** tier
by querying Bloom's Postgres tables directly (`get_experiment_traits`,
`list_experiment_trait_sources`) rather than reading a local `BLOOM_TRAITS_DIR` CSV. The raw
tier is DB-only: `name` is parsed as `str(experiment_id)`; a non-numeric or unresolvable
`name` with no cleaned output is a structured not-found condition, not a local-disk
fallback. `list_experiments()` enumerates experiments from `cyl_experiments` rather than
scanning a local directory or bucket. `SupabaseReader` no longer implements `RawSourced`
(there is no on-disk path for a DB-backed raw read to content-address); it implements
`SourceSelectable` instead.

#### Scenario: Resolves the latest versioned cleaned output from Supabase

- **WHEN** `SupabaseReader.load_experiment(name)` is called and a versioned `qc_<stem>`
  manifest with a `latest` cleaned output exists
- **THEN** it downloads and returns that cleaned CSV from Supabase Storage, with a source
  label identifying the version

#### Scenario: Resolves the raw tier from the database, wide-pivoted

- **WHEN** `SupabaseReader.load_experiment(name)` is called for `name` shaped as
  `str(experiment_id)` with no cleaned output
- **THEN** it fetches long-format trait rows via `get_experiment_traits`, pivots them wide
  (one column per distinct trait name), renames columns to canonical roles (`genotype` from
  `accessions.name`, `sample_id` from `cyl_plants.qr_code`), and returns the resulting frame
  with source label `"raw"` — no local disk or Storage bucket is read for this tier

#### Scenario: Non-numeric raw-tier name is not-found, not a local fallback

- **WHEN** `SupabaseReader.load_experiment(name)` is called for a `name` that does not
  parse as `str(experiment_id)` and has no cleaned output
- **THEN** it raises `ExperimentNotFoundError` — it SHALL NOT read a local `BLOOM_TRAITS_DIR`
  CSV as a fallback

#### Scenario: One source per frame, never mixed

- **WHEN** `SupabaseReader.load_experiment(name)` resolves a raw read for an experiment
  whose trait rows span more than one `source_id`
- **THEN** the returned frame's rows all belong to exactly one resolved `source_id` (the
  latest, or an explicitly pinned one) — rows from a different source are never merged into
  the same frame

#### Scenario: Explicit source/run pin is honored

- **WHEN** `SupabaseReader.load_experiment(name, source_id=<id>)` or `load_experiment(name,
  run_id=<id>)` is called
- **THEN** the returned frame contains only rows from that pinned source or run, not
  whatever would otherwise resolve as latest

#### Scenario: DB-backed reader no longer satisfies RawSourced

- **WHEN** `isinstance(SupabaseReader(), RawSourced)` is checked
- **THEN** it is `False` — the raw tier has no on-disk path to content-address; callers that
  gate on `RawSourced` for input hashing (e.g. `tools._ports.raw_source_for`) correctly
  treat `SupabaseReader` as path-less and record no fabricated path

#### Scenario: List experiments enumerates database experiments

- **WHEN** `SupabaseReader.list_experiments()` is called
- **THEN** it returns `ExperimentSummary` entries sourced from `cyl_experiments`, with
  non-placeholder `rows`/`trait_columns`/`total_columns` counts, rather than scanning a
  local directory or Storage bucket

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
which source actually backs a read, honoring an explicit pin or defaulting to latest).
Adapters without a source-versioned substrate (e.g. `FakeReader`, `LocalReader`) SHALL NOT
implement it.

#### Scenario: Capability is discoverable via isinstance

- **WHEN** `isinstance(SupabaseReader(), SourceSelectable)` and
  `isinstance(FakeReader(), SourceSelectable)` are checked
- **THEN** the former is `True` and the latter is `False`

#### Scenario: Source/run discovery

- **WHEN** `SupabaseReader().list_sources(name)` is called for a known experiment with
  multiple contributing sources
- **THEN** it returns the distinct `(source_id, source_name, pipeline_run_id)` tuples for
  that experiment

#### Scenario: Unpinned resolution still yields a concrete source

- **WHEN** `SupabaseReader().resolve_source(name)` is called with no `source_id`/`run_id`
- **THEN** it returns the `SourceInfo` for whatever `get_experiment_traits` treats as
  latest for that experiment, never `None`, for an experiment with at least one source
