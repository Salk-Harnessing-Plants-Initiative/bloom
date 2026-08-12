## MODIFIED Requirements

### Requirement: ExperimentReader Port

The system SHALL define a backend-agnostic `ExperimentReader` port exposing
`load_experiment(name, version, require_clean)` and `list_experiments()`, where `load_experiment`
returns an `ExperimentFrame` carrying the experiment frame, **adapter-declared** column roles
(trait vs metadata columns), and a source label. Column roles SHALL be declared by the adapter,
not re-inferred by callers, so a future adapter sourcing tidy/long rows can satisfy the contract
without reproducing dtype-based detection. Consumers SHALL depend only on this port — never on
`supabase`, `experiment_utils`, or `storage/` primitives directly — for reading experiment data.
`version` accepts `"latest"` (default), `"latest_qc"`, `"raw"`, or an explicit `"v<N>"`.

#### Scenario: Load an experiment returns a frame with declared roles

- **WHEN** a consumer calls `load_experiment(name)` (default `version="latest"`) for a known
  experiment
- **THEN** it returns an `ExperimentFrame` whose frame holds the experiment data, whose trait and
  metadata column roles are populated, and whose source label identifies what was resolved (e.g.
  `raw`, `legacy_cleaned`, or a versioned cleaned output)

#### Scenario: Version selection resolves in the deployed order

- **WHEN** `load_experiment(name, version="latest")` is called
- **THEN** the reader resolves the cleaned tier by checking every cleaned-producing tool-class
  manifest (`qc` and `outliers`) and preferring the `outliers` class's `latest` entry whenever
  one exists at all — a fixed priority, not a recency comparison — falling back to the `qc`
  class's `latest` entry when `outliers` has none, then the legacy un-versioned
  `qc_<stem>/<stem>_cleaned.csv`, then the raw input, returning the first tier that resolves
- **AND** when the `outliers` class wins, the returned source label is qualified with the tool
  class (e.g. `outliers_v2_cleaned`) so the winning manifest is identifiable from the label
  alone; when the `qc` class wins (no `outliers` version exists), the label is the unqualified
  `f"{entry.id}_cleaned"` — byte-for-byte what `version="latest"` already returns today, so an
  experiment that has never been trimmed sees no observable change

#### Scenario: A later plain clean does not silently win over an existing trim

- **WHEN** an `outliers`-class cleaned version exists for an experiment, and a **new** `qc`-class
  clean is subsequently committed (regardless of its `created_at` relative to the `outliers`
  version — it is always later, by construction, since it is committed after)
- **THEN** `load_experiment(name, version="latest")` still resolves the `outliers`-class version
  — the new plain clean does not become "latest" for this or any other `require_clean=True`
  consumer until a fresh `remove_outliers` run (reading via `version="latest_qc"`, which is not
  subject to this preference) commits a new trim on top of it

#### Scenario: `latest_qc` resolves the plain-clean tier regardless of any trim

- **WHEN** `load_experiment(name, version="latest_qc")` is called and both a `qc`-class and an
  `outliers`-class cleaned version exist
- **THEN** the reader resolves the `qc`-class `latest` entry specifically, ignoring the
  `outliers` class entirely — this is the tier `remove_outliers` itself reads as its trimming
  input, so a fresh `qc_clean` re-run is always visible to the next `remove_outliers` call even
  while an older trim remains "latest" for `version="latest"` callers

#### Scenario: Explicit version miss is a hard error

- **WHEN** `load_experiment(name, version="v9")` is called for a version that does not exist
- **THEN** the reader signals a not-found condition for that explicit version rather than
  silently falling back to another tier

#### Scenario: A manifest schema error is never silently routed around

- **WHEN** `load_experiment(name, version="latest")` is called and either the `qc`-class or the
  `outliers`-class manifest fails schema validation
- **THEN** the reader surfaces that schema error immediately rather than treating it as a soft
  miss and falling through to the other class or to a lower tier

#### Scenario: Clean-required load

- **WHEN** `load_experiment(name, require_clean=True)` is called and no cleaned output exists in
  any cleaned-producing tool class
- **THEN** the reader signals that a cleaned version is required and absent, rather than
  returning the raw frame

#### Scenario: Unknown experiment is reported through the contract

- **WHEN** `load_experiment(name)` is called for a name the reader cannot resolve in any tier
- **THEN** the reader surfaces a structured not-found condition with no raw Supabase or
  filesystem traceback, bucket name, or connection string leaked to the caller

#### Scenario: List experiments enumerates available inputs

- **WHEN** a consumer calls `list_experiments()`
- **THEN** it returns the available experiments (each identified by name) and returns an empty
  list — not an error — when none are available

#### Scenario: Single-experiment read consumers go through the port

- **WHEN** `storage_tools.py` and `qc_tools.py` are inspected
- **THEN** neither imports `supabase` or `AnalysisDir`, nor reads experiment CSVs from a local
  directory directly; each obtains data through an injected `ExperimentReader`

#### Scenario: No consumer imports the storage writer or Supabase directly

- **WHEN** the discovery tools and workflows (`qc_tools`, `storage_tools`, `correlation_tools`,
  `tools/workflows/*`) are inspected
- **THEN** none imports `supabase`, `AnalysisWriter`, or `AnalysisDir`; `correlation_tools`'
  cross-experiment local-CSV reads are retained behind the deprecated `BLOOM_TRAITS_DIR` path and
  routed through the port in the follow-up that removes that path

### Requirement: SupabaseReader Adapter

The system SHALL provide a `SupabaseReader` adapter implementing `ExperimentReader` that
preserves the deployed read behaviour: raw inputs from the local `BLOOM_TRAITS_DIR` and
versioned-cleaned outputs from Supabase Storage under `bloommcp_output/` as `bloom_agent`. The
local raw-input read is **retained but deprecated**: it SHALL emit a deprecation signal so the
follow-up that migrates inputs to Supabase Storage can remove it.

#### Scenario: Resolves the outliers-preferring cleaned output for version="latest"

- **WHEN** `SupabaseReader.load_experiment(name)` is called and both a `qc_<stem>` and an
  `outliers_<stem>` manifest exist with their own `latest` cleaned output
- **THEN** it downloads and returns the cleaned CSV from the `outliers`-class manifest, with a
  source label identifying it as such

#### Scenario: An untrimmed experiment sees no change in the resolved label

- **WHEN** `SupabaseReader.load_experiment(name)` is called for an experiment that has only ever
  been cleaned, never trimmed (no `outliers_<stem>` manifest exists)
- **THEN** the resolved cleaned CSV and its source label are byte-for-byte identical to what this
  adapter returned before this change — the unqualified `f"{entry.id}_cleaned"` label, not the
  tool-class-qualified form

#### Scenario: Resolves the plain-clean output for version="latest_qc"

- **WHEN** `SupabaseReader.load_experiment(name, version="latest_qc")` is called and both
  manifests exist
- **THEN** it downloads and returns the cleaned CSV from the `qc`-class manifest specifically,
  ignoring the `outliers`-class manifest

#### Scenario: Falls back to the local raw input with a deprecation signal

- **WHEN** `SupabaseReader.load_experiment(name)` is called for an experiment with no cleaned
  output in any cleaned-producing tool class, and only a raw CSV under `BLOOM_TRAITS_DIR` exists
- **THEN** it returns the raw frame and emits a deprecation signal indicating the local raw-read
  path is slated for removal

#### Scenario: Adapter tests do not touch the network

- **WHEN** the `SupabaseReader` test suite runs
- **THEN** it exercises the adapter against a monkeypatched `supabase_client` boundary (no
  `supabase.create_client` call) and passes with no `SUPABASE_URL`/`BLOOM_AGENT_KEY` configured

### Requirement: FakeReader Adapter

The system SHALL provide an in-memory `FakeReader` adapter implementing `ExperimentReader`,
behaviourally equivalent to `SupabaseReader` for observable outcomes, so the full read path is
testable with no live Supabase — **with one explicit, disclosed exception**: `FakeReader` has no
notion of tool classes and cannot model the `qc`/`outliers` priority split. It treats
`version="latest_qc"` as an alias for `version="latest"` rather than resolving a `qc`-class
manifest specifically, since it has no manifests at all. The outliers-preferring behavior of
`version="latest"` and the cross-manifest fallback in `_resolve_versioned_cleaned` are proved
against the real `SupabaseReader`/`SupabaseResultStore` adapters over a shared in-memory object
store, not against `FakeReader` (the same fakes-vs-adapters split the
`bloommcp-remove-outliers-tool` capability already established for cross-port composition).

#### Scenario: In-memory experiment loads without Supabase

- **WHEN** a test seeds `FakeReader` with a fixture experiment and calls `load_experiment(name)`
- **THEN** it returns the expected frame and declared roles with no network or Supabase access

#### Scenario: `latest_qc` is aliased to `latest`

- **WHEN** a test calls `FakeReader.load_experiment(name, version="latest_qc")`
- **THEN** it resolves exactly as `version="latest"` would (`FakeReader` has no tool-class model
  to resolve `latest_qc` against specifically), so existing `FakeReader`-seeded tests that switch
  a call site from `"latest"` to `"latest_qc"` keep passing unchanged

#### Scenario: Fake and Supabase adapters agree on single-class observable behaviour

- **WHEN** the same scenario set (load, version selection, not-found, empty list) runs against
  both `FakeReader` and `SupabaseReader` (on a monkeypatched boundary), for an experiment with no
  `outliers`-class cleaned version
- **THEN** both produce equivalent observable results — return shapes, source labels, and
  not-found signalling match
