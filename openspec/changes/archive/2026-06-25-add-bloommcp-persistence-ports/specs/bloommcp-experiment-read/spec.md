## ADDED Requirements

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

- **WHEN** `storage_tools.py` and `qc_tools.py` are inspected
- **THEN** neither imports `supabase` or `AnalysisDir`, nor reads experiment CSVs from a local directory directly; each obtains data through an injected `ExperimentReader`

#### Scenario: No consumer imports the storage writer or Supabase directly

- **WHEN** the discovery tools and workflows (`qc_tools`, `storage_tools`, `correlation_tools`, `tools/workflows/*`) are inspected
- **THEN** none imports `supabase`, `AnalysisWriter`, or `AnalysisDir`; `correlation_tools`' cross-experiment local-CSV reads are retained behind the deprecated `BLOOM_TRAITS_DIR` path and routed through the port in the follow-up that removes that path

### Requirement: SupabaseReader Adapter

The system SHALL provide a `SupabaseReader` adapter implementing `ExperimentReader` that preserves the deployed read behaviour: raw inputs from the local `BLOOM_TRAITS_DIR` and versioned-cleaned outputs from Supabase Storage under `bloommcp_output/` as `bloom_agent`. The local raw-input read is **retained but deprecated**: it SHALL emit a deprecation signal so the follow-up that migrates inputs to Supabase Storage can remove it.

#### Scenario: Resolves the latest versioned cleaned output from Supabase

- **WHEN** `SupabaseReader.load_experiment(name)` is called and a versioned `qc_<stem>` manifest with a `latest` cleaned output exists
- **THEN** it downloads and returns that cleaned CSV from Supabase Storage, with a source label identifying the version

#### Scenario: Falls back to the local raw input with a deprecation signal

- **WHEN** `SupabaseReader.load_experiment(name)` is called for an experiment with no cleaned output, and only a raw CSV under `BLOOM_TRAITS_DIR` exists
- **THEN** it returns the raw frame and emits a deprecation signal indicating the local raw-read path is slated for removal

#### Scenario: Adapter tests do not touch the network

- **WHEN** the `SupabaseReader` test suite runs
- **THEN** it exercises the adapter against a monkeypatched `supabase_client` boundary (no `supabase.create_client` call) and passes with no `SUPABASE_URL`/`BLOOM_AGENT_KEY` configured

### Requirement: FakeReader Adapter

The system SHALL provide an in-memory `FakeReader` adapter implementing `ExperimentReader`, behaviourally equivalent to `SupabaseReader` for observable outcomes, so the full read path is testable with no live Supabase.

#### Scenario: In-memory experiment loads without Supabase

- **WHEN** a test seeds `FakeReader` with a fixture experiment and calls `load_experiment(name)`
- **THEN** it returns the expected frame and declared roles with no network or Supabase access

#### Scenario: Fake and Supabase adapters agree on observable behaviour

- **WHEN** the same scenario set (load, version selection, not-found, empty list) runs against both `FakeReader` and `SupabaseReader` (on a monkeypatched boundary)
- **THEN** both produce equivalent observable results — return shapes, source labels, and not-found signalling match
