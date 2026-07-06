## ADDED Requirements

### Requirement: LocalReader Adapter

The system SHALL provide a `LocalReader` adapter implementing the `ExperimentReader` port that reads experiment inputs with **no Supabase dependency** — it SHALL NOT import `supabase_client` nor make any PostgREST/table or network call. `LocalReader` SHALL read raw experiment CSVs from a configurable local directory (`BLOOM_EXPERIMENT_LOCAL_ROOT` when set, otherwise `BLOOM_TRAITS_DIR`) and resolve cleaned/versioned outputs from the local output store, returning the **same `ExperimentFrame` contract** as `SupabaseReader` (frame, adapter-declared column roles via the shared `detect_columns` oracle, and a source label) so consumers are unchanged. It SHALL preserve the port's observable behaviour — the same resolution order (versioned-cleaned → legacy cleaned → raw), the same not-found and clean-required signalling, and no host-path leakage in caller-facing errors — and SHALL NOT emit the raw-input deprecation signal, because under this adapter the local input path is a supported opt-in path rather than a deprecated one.

#### Scenario: Reads a raw experiment from the local dir with declared roles

- **WHEN** a consumer calls `LocalReader.load_experiment(name)` for an experiment whose raw CSV exists under the configured local input dir and no cleaned output exists
- **THEN** it returns an `ExperimentFrame` with the frame, declared trait/metadata roles, and a `raw` source label, and emits **no** `DeprecationWarning`

#### Scenario: Resolution order and version signalling match the port

- **WHEN** the shared scenario set (latest resolves versioned-cleaned then legacy then raw; explicit-version miss; `require_clean=True` with no cleaned output; unknown name) runs against `LocalReader`
- **THEN** it resolves in the deployed order and raises `ExperimentNotFoundError` / `CleanedVersionRequiredError` for the miss cases — behaviourally equivalent to `SupabaseReader` and `FakeReader`

#### Scenario: No Supabase access and no host-path leakage

- **WHEN** `LocalReader` runs with `SUPABASE_URL` / `BLOOM_AGENT_KEY` unset, and an unknown experiment is requested
- **THEN** it completes with no Supabase import or network call, and the not-found error carries no absolute host filesystem path

#### Scenario: List experiments enumerates the local dir

- **WHEN** a consumer calls `LocalReader.list_experiments()`
- **THEN** it returns a summary per experiment CSV in the local input dir (with declared roles) and returns an empty list — not an error — when the dir holds none

### Requirement: Fully-Local Reader Selection

The system SHALL select `LocalReader` as the injected `ExperimentReader` when fully-local mode is active, driven by the same `BLOOM_STORAGE_BACKEND=local` switch that #389 introduced for the object-storage backend, so a single switch means "local input AND local output." Supabase SHALL remain the default: when `BLOOM_STORAGE_BACKEND` is unset or `supabase`, `SupabaseReader` is wired and read behaviour is byte-for-byte unchanged. Selection SHALL occur at the composition root (`server.main()` / `tools._ports.configure`) and SHALL remain import-side-effect-free (resolved at boot/wiring, not at module import).

#### Scenario: Local backend wires LocalReader

- **WHEN** the server boots with `BLOOM_STORAGE_BACKEND=local`
- **THEN** the composition root injects a `LocalReader` as the active `ExperimentReader`, and every consumer that reads through the port sources inputs locally

#### Scenario: Default wires SupabaseReader unchanged

- **WHEN** the server boots with `BLOOM_STORAGE_BACKEND` unset or `supabase`
- **THEN** the composition root injects `SupabaseReader` and the read path behaves exactly as before this change

#### Scenario: Import stays side-effect-free

- **WHEN** `import bloom_mcp.server` runs in a fresh interpreter with no environment set
- **THEN** the import succeeds without selecting a reader, reading `BLOOM_STORAGE_BACKEND`, or touching the filesystem — selection happens only at boot/wiring

### Requirement: Cross-Experiment Reads Routed Through the Port

Cross-experiment and provenance reads that currently read raw CSVs directly from the local `BLOOM_TRAITS_DIR` SHALL be routed through the injected `ExperimentReader`, so the active adapter (local or Supabase) is honoured consistently. Specifically, `correlation_tools`' cross-experiment reads and `start_run`'s source-CSV read SHALL obtain data through the port rather than via a direct `pd.read_csv(TRAITS_DIR / …)`. This routing SHALL preserve the observable outputs of the affected tools.

#### Scenario: correlation_tools reads through the port

- **WHEN** the cross-experiment correlation tools load their input experiments
- **THEN** they obtain frames through the injected `ExperimentReader` (honouring the active adapter), and no longer call `pd.read_csv(TRAITS_DIR / …)` directly, while their computed outputs are unchanged

#### Scenario: start_run source provenance goes through the port

- **WHEN** a run is opened via `start_run`
- **THEN** the source CSV is obtained through the reader rather than a hard-coded `TRAITS_DIR` path, and `source_csv` degrades to `None` (already supported) when no concrete path is available, rather than recording a fabricated path

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

- **WHEN** `storage_tools.py` and `qc_tools.py` are inspected
- **THEN** neither imports `supabase` or `AnalysisDir`, nor reads experiment CSVs from a local directory directly; each obtains data through an injected `ExperimentReader`

#### Scenario: No consumer imports the storage writer or Supabase directly

- **WHEN** the discovery tools and workflows (`qc_tools`, `storage_tools`, `correlation_tools`, `tools/workflows/*`) are inspected
- **THEN** none imports `supabase`, `AnalysisWriter`, or `AnalysisDir`; `correlation_tools`' cross-experiment reads are obtained through the injected `ExperimentReader` (honouring the active adapter), not via a direct local-CSV read

### Requirement: SupabaseReader Adapter

The system SHALL provide a `SupabaseReader` adapter implementing `ExperimentReader` that preserves the deployed read behaviour: raw inputs from the local `BLOOM_TRAITS_DIR` and versioned-cleaned outputs from Supabase Storage under `bloommcp_output/` as `bloom_agent`. On this default (Supabase) path the local raw-input read is **retained but deprecated**: it SHALL emit a deprecation signal steering local-input use toward the opt-in `LocalReader` adapter (`BLOOM_STORAGE_BACKEND=local`). The local raw-input path is **promoted** to that first-class adapter rather than slated for outright removal.

#### Scenario: Resolves the latest versioned cleaned output from Supabase

- **WHEN** `SupabaseReader.load_experiment(name)` is called and a versioned `qc_<stem>` manifest with a `latest` cleaned output exists
- **THEN** it downloads and returns that cleaned CSV from Supabase Storage, with a source label identifying the version

#### Scenario: Falls back to the local raw input with a deprecation signal

- **WHEN** `SupabaseReader.load_experiment(name)` is called for an experiment with no cleaned output, and only a raw CSV under `BLOOM_TRAITS_DIR` exists
- **THEN** it returns the raw frame and emits a deprecation signal indicating that local inputs on the Supabase path are deprecated and that the supported local path is the opt-in `LocalReader` adapter

#### Scenario: Adapter tests do not touch the network

- **WHEN** the `SupabaseReader` test suite runs
- **THEN** it exercises the adapter against a monkeypatched `supabase_client` boundary (no `supabase.create_client` call) and passes with no `SUPABASE_URL`/`BLOOM_AGENT_KEY` configured
