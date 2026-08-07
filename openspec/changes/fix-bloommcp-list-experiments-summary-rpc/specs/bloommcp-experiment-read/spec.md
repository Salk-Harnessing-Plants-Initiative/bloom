## MODIFIED Requirements

### Requirement: ExperimentReader Port

The system SHALL define a backend-agnostic `ExperimentReader` port exposing `load_experiment(name, version, require_clean)` and `list_experiments()`, where `load_experiment` returns an `ExperimentFrame` carrying the experiment frame, **adapter-declared** column roles (trait vs metadata columns), and a source label. Column roles SHALL be declared by the adapter, not re-inferred by callers, so a future adapter sourcing tidy/long rows can satisfy the contract without reproducing dtype-based detection. Consumers SHALL depend only on this port — never on `supabase`, `experiment_utils`, or `storage/` primitives directly — for reading experiment data. `list_experiments()` SHALL enumerate every available experiment in a bounded, small number of backend round trips regardless of experiment count — not one round trip per experiment — and any per-experiment counts it reports SHALL agree with what `load_experiment` would derive for that same experiment under the same latest-source-selection semantics.

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

- **WHEN** a consumer calls `list_experiments()` against a backend with N available experiments
- **THEN** it returns the available experiments (each identified by name), returns an empty list — not an error — when none are available, and does so using a number of backend round trips that does not scale with N (one bulk summary call, not one call per experiment)

#### Scenario: Listed counts match load_experiment's latest-selection semantics

- **WHEN** `list_experiments()` reports `rows`/`trait_columns` for an experiment that also has data reachable via `load_experiment`
- **THEN** those counts equal the number of distinct sample identities and distinct trait columns `load_experiment(str(experiment_id))` would derive for that same experiment under its default (latest-source) selection

#### Scenario: An experiment with no reachable trait data is listed with zero counts, not excluded

- **WHEN** `list_experiments()` enumerates an experiment that exists in the backend's experiment table but has no trait data reachable under the default (latest) source selection
- **THEN** that experiment still appears in the returned list, with `rows=0` and `trait_columns=0`, rather than being silently omitted

#### Scenario: Single-experiment read consumers go through the port

- **WHEN** `storage_tools.py` and `qc_tools.py` are inspected
- **THEN** neither imports `supabase` or `AnalysisDir`, nor reads experiment CSVs from a local directory directly; each obtains data through an injected `ExperimentReader`

#### Scenario: No consumer imports the storage writer or Supabase directly

- **WHEN** the discovery tools and workflows (`qc_tools`, `storage_tools`, `correlation_tools`, `tools/workflows/*`) are inspected
- **THEN** none imports `supabase`, `AnalysisWriter`, or `AnalysisDir`; `correlation_tools`' cross-experiment local-CSV reads are retained behind the deprecated `BLOOM_TRAITS_DIR` path and routed through the port in the follow-up that removes that path

## ADDED Requirements

### Requirement: Bounded backend query timeout

The `SupabaseReader` adapter's underlying PostgREST/RPC client SHALL apply a deliberately chosen,
overridable timeout to every query and RPC call, rather than relying solely on the client library's
undeclared package default. A caller SHALL be able to override the timeout for a specific client
instance; when not overridden, a module-level default applies. A query that exceeds the timeout SHALL
fail with a clear, structured error rather than hanging indefinitely from the caller's perspective.

#### Scenario: A blocked or slow query fails loudly instead of hanging

- **WHEN** a PostgREST/RPC call takes longer than the configured timeout to respond
- **THEN** the call raises a timeout-shaped error within approximately that bound, rather than blocking
  the caller indefinitely

#### Scenario: The timeout is overridable per client instance

- **WHEN** a caller requests the PostgREST client with an explicit timeout override
- **THEN** that override applies to queries made through that client instance, distinct from the
  module-level default used when no override is given
