## MODIFIED Requirements

### Requirement: ExperimentReader Port

The system SHALL define a backend-agnostic `ExperimentReader` port exposing `load_experiment(name, version, require_clean)` and `list_experiments()`, where `load_experiment` returns an `ExperimentFrame` carrying the experiment frame, **adapter-declared** column roles (trait vs metadata columns), and a source label. Column roles SHALL be declared by the adapter, not re-inferred by callers, so a future adapter sourcing tidy/long rows can satisfy the contract without reproducing dtype-based detection. Consumers SHALL depend only on this port — never on `supabase`, `experiment_utils`, or `storage/` primitives directly — for reading experiment data.

#### Scenario: Load an experiment returns a frame with declared roles

- **WHEN** a consumer calls `load_experiment(name)` (default `version="latest"`) for a known experiment
- **THEN** it returns an `ExperimentFrame` whose frame holds the experiment data, whose trait and metadata column roles are populated, and whose source label identifies what was resolved (e.g. `raw`, `legacy_cleaned`, or a versioned cleaned output)

#### Scenario: Version selection resolves in the deployed order

- **WHEN** `load_experiment(name, version="latest")` is called and a versioned `qc_<stem>` manifest with a `latest` cleaned output exists
- **THEN** the reader resolves outputs in the deployed order — versioned-manifest `latest` cleaned CSV, then the legacy un-versioned `qc_<stem>/<stem>_cleaned.csv`, then the raw input — returning the first that resolves

#### Scenario: Explicit version miss is a hard error

- **WHEN** `load_experiment(name, version="v9")` is called for a version that does not exist
- **THEN** the reader signals a not-found condition for that explicit version rather than silently falling back to another tier

#### Scenario: Clean-required load

- **WHEN** `load_experiment(name, require_clean=True)` is called and no cleaned output exists
- **THEN** the reader signals that a cleaned version is required and absent, rather than returning the raw frame

#### Scenario: Unknown experiment is reported through the contract

- **WHEN** `load_experiment(name)` is called for a name the reader cannot resolve in any tier
- **THEN** the reader surfaces a structured not-found condition with no raw Supabase or filesystem traceback, bucket name, or connection string leaked to the caller

#### Scenario: List experiments enumerates available inputs

- **WHEN** a consumer calls `list_experiments()`
- **THEN** it returns the available experiments (each identified by name) and returns an empty list — not an error — when none are available

#### Scenario: Single-experiment read consumers go through the port

- **WHEN** `qc_clean.py` and `qc_inspect.py` (`bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/`) are inspected
- **THEN** neither imports `supabase` or a storage-writer class directly, nor builds a raw-input path from a hard-coded `TRAITS_DIR` constant; each obtains data through an injected `ExperimentReader`, and any provenance source-path lookup routes through `_ports.raw_source_for` (mirroring `_ports.start_run`) rather than reading the `TRAITS_DIR` module global directly

#### Scenario: qc_inspect's provenance source path honors the active reader's input root

- **WHEN** `qc_inspect` persists a report run
- **THEN** the recorded `source_csv` is resolved via `_ports.raw_source_for(experiment)` — the active reader's declared raw-input path — not a hard-coded `TRAITS_DIR / experiment` build, so a non-default input root (e.g. a custom `BLOOM_EXPERIMENT_LOCAL_ROOT`) is honored the same way `_ports.start_run`'s callers already are

#### Scenario: No consumer imports the storage writer or Supabase directly

- **WHEN** the discovery and analysis tools (`list_available_experiments.py`, `list_existing_analyses.py`, and the `sections/sleap_roots/analysis/*` tools) are inspected
- **THEN** none imports `supabase`, `AnalysisWriter`, or `AnalysisDir` directly; the `correlation_tools`/`tools/workflows/*` modules this scenario previously carved out for the deprecated `BLOOM_TRAITS_DIR` path no longer exist (retired by `devendor-bloommcp-analysis`) — the only remaining local-disk raw-input read is `SupabaseReader`'s own raw-tier fallback (see the `SupabaseReader Adapter` requirement), which is intentional infrastructure inside the port's Supabase adapter, not a consumer bypassing the port

### Requirement: SupabaseReader Adapter

The system SHALL provide a `SupabaseReader` adapter implementing `ExperimentReader` that preserves the deployed read behaviour: raw inputs from the local `BLOOM_TRAITS_DIR` and versioned-cleaned outputs from Supabase Storage under `bloommcp_output/` as `bloom_agent`. The local raw-input read is **retained as an intentional interim adapter** — it is the only thing serving raw reads on the default (Supabase) backend today, since the `bloommcp_input/` Storage bucket has no producer anywhere in the codebase (confirmed by `bloommcp/docs/data-access-roadmap.md`'s Live-state facts). It SHALL emit a deprecation signal naming the actual tracked retirement path — `bloommcp/docs/data-access-roadmap.md`'s Tier 2 rewrite of this raw tier to query Bloom's Postgres directly by `experiment_id` — not a bucket-upload migration, which was attempted and closed (bloom PR #368, PR #413) because the bucket has no producer.

#### Scenario: Resolves the latest versioned cleaned output from Supabase

- **WHEN** `SupabaseReader.load_experiment(name)` is called and a versioned `qc_<stem>` manifest with a `latest` cleaned output exists
- **THEN** it downloads and returns that cleaned CSV from Supabase Storage, with a source label identifying the version

#### Scenario: Falls back to the local raw input with a deprecation signal

- **WHEN** `SupabaseReader.load_experiment(name)` is called for an experiment with no cleaned output, and only a raw CSV under `BLOOM_TRAITS_DIR` exists
- **THEN** it returns the raw frame and emits a deprecation signal naming `data-access-roadmap.md`'s Tier 2 DB-direct rewrite as the tracked retirement path — not a bucket-upload migration

#### Scenario: Deprecation signal does not cite a superseded migration plan

- **WHEN** the deprecation warning/docstring text is inspected
- **THEN** it does not claim removal is pending "the follow-up that migrates inputs into `bloommcp_input/`" — that plan (bloom PR #368) is closed — and instead points at the currently tracked plan (`data-access-roadmap.md` Tier 2)

#### Scenario: Adapter tests do not touch the network

- **WHEN** the `SupabaseReader` test suite runs
- **THEN** it exercises the adapter against a monkeypatched `supabase_client` boundary (no `supabase.create_client` call) and passes with no `SUPABASE_URL`/`BLOOM_AGENT_KEY` configured
