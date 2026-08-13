## MODIFIED Requirements

### Requirement: ExperimentReader Port

The system SHALL define a backend-agnostic `ExperimentReader` port exposing
`load_experiment(name, version, require_clean, source_id, run_id)` and `list_experiments()`,
where `load_experiment` returns an `ExperimentFrame` carrying the experiment frame,
**adapter-declared** column roles (trait vs metadata columns), a source label, and — for a raw-tier
read on a source-versioned adapter — the resolved `SourceInfo` plus the total number of known
sources at the time of that same resolution (`available_source_count`), so a caller building a
source-ambiguity advisory never needs an independent `list_sources` round-trip against the same
read. Column roles
SHALL be declared by the adapter, not re-inferred by callers, so a future adapter sourcing
tidy/long rows can satisfy the contract without reproducing dtype-based detection. Consumers SHALL
depend only on this port — never on `supabase`, `experiment_utils`, or `storage/` primitives
directly — for reading experiment data. `source_id`/`run_id` are optional, default `None`, and
every adapter MUST accept both kwargs: an adapter backed by a source-versioned substrate honors a
non-`None` pin (or raises `AmbiguousSourceSelectionError` when both are given, or `SourcePinNotFoundError`
when a pin does not resolve); an adapter with no source concept MUST raise
`SourcePinningUnsupportedError` immediately when either is non-`None`, rather than silently
ignoring the pin or raising an unrelated `TypeError`.

#### Scenario: Load an experiment returns a frame with declared roles

- **WHEN** a consumer calls `load_experiment(name)` (default `version="latest"`) for a known experiment
- **THEN** it returns an `ExperimentFrame` whose frame holds the experiment data, whose trait and metadata column roles are populated, and whose source label identifies what was resolved (e.g. `raw`, `legacy_cleaned`, or a versioned cleaned output)

#### Scenario: Version selection resolves in the deployed order

- **WHEN** `load_experiment(name, version="latest")` is called and a versioned `qc_<stem>` manifest with a `latest` cleaned output exists
- **THEN** the reader resolves outputs in the deployed order — versioned-manifest `latest` cleaned CSV, then the legacy un-versioned `qc_<stem>/<stem>_cleaned.csv`, then the raw input — returning the first that resolves

#### Scenario: Explicit version miss is a hard error

- **WHEN** `load_experiment(name, version="v9")` is called for a version that does not exist
- **THEN** the reader signals a not-found condition for that explicit version rather than silently falling back to another tier

#### Scenario: An explicit version id colliding across cleaned tool classes is ambiguous, not silently qc

- **WHEN** `load_experiment(name, version="v1")` is called and BOTH the `qc` and `outliers` classes
  independently have their own, differently-content `v1` entry (each class has its own
  independently-numbered `v<N>` sequence — see the `bloommcp-clean-version-selection` capability)
- **THEN** the reader refuses to resolve either one and signals the id is ambiguous, rather than
  silently preferring `qc` and returning the wrong, untrimmed dataset — the fix for the bloom#644
  review's blocking finding, where an earlier revision of this behavior resolved against the `qc`
  class only

#### Scenario: An explicit version id that exists in exactly one cleaned tool class resolves there

- **WHEN** `load_experiment(name, version="v1")` is called and only the `outliers` class (not `qc`)
  has a `v1` entry
- **THEN** the reader resolves the `outliers`-class entry — a version a caller saw listed under
  `outliers` via `list_existing_analyses` is always pinnable by id, not only versions that happen
  to also exist under `qc`

#### Scenario: Clean-required load

- **WHEN** `load_experiment(name, require_clean=True)` is called and no cleaned output exists
- **THEN** the reader signals that a cleaned version is required and absent, rather than returning the raw frame

#### Scenario: Unknown experiment is reported through the contract

- **WHEN** `load_experiment(name)` is called for a name the reader cannot resolve in any tier
- **THEN** the reader surfaces a structured not-found condition with no raw Supabase or filesystem traceback, bucket name, or connection string leaked to the caller

#### Scenario: A resolvable-but-unreadable committed version is a caller-safe error, not a leaked exception

- **WHEN** `load_experiment(name, version=...)` resolves a manifest entry that names a real, committed version, but reading that entry — the manifest lookup itself, its recorded output key, its version directory, or the file download — raises a storage or filesystem exception partway through
- **THEN** the reader converts that exception into a structured error before it reaches the caller, never letting a raw exception, filesystem path, or storage traceback escape

#### Scenario: List experiments enumerates available inputs

- **WHEN** a consumer calls `list_experiments()`
- **THEN** it returns the available experiments (each identified by name) and returns an empty list — not an error — when none are available

#### Scenario: Single-experiment read consumers go through the port

- **WHEN** `storage_tools.py` and `qc_tools.py` are inspected
- **THEN** neither imports `supabase` or `AnalysisDir`, nor reads experiment CSVs from a local directory directly; each obtains data through an injected `ExperimentReader`

#### Scenario: No consumer imports the storage writer or Supabase directly

- **WHEN** the discovery tools and workflows (`qc_tools`, `storage_tools`, `correlation_tools`, `tools/workflows/*`) are inspected
- **THEN** none imports `supabase`, `AnalysisWriter`, or `AnalysisDir`; `correlation_tools`' cross-experiment local-CSV reads are retained behind the deprecated `BLOOM_TRAITS_DIR` path and routed through the port in the follow-up that removes that path

#### Scenario: A source pin given to an adapter with no source concept is rejected clearly

- **WHEN** `load_experiment(name, source_id=7)` or `load_experiment(name, run_id="run-1")` is called
  on an adapter that does not implement `SourceSelectable` (e.g. `LocalReader`, `FakeReader`)
- **THEN** the adapter raises `SourcePinningUnsupportedError` rather than silently ignoring the pin
  or raising a bare `TypeError` for an unexpected keyword argument

#### Scenario: Both source_id and run_id given is rejected at the Protocol level

- **WHEN** `load_experiment(name, source_id=7, run_id="run-1")` is called on any adapter, with both
  arguments non-`None`
- **THEN** the call raises `AmbiguousSourceSelectionError` — verified generically through the
  `ExperimentReader` Protocol type, not only against `SupabaseReader`'s concrete implementation

#### Scenario: An explicit source pin that matches nothing is reported distinctly from ambiguity

- **WHEN** `load_experiment(name, source_id=999)` is called with a `source_id` that does not match
  any known source for `name`, on an adapter backed by a source-versioned substrate
- **THEN** the call raises `SourcePinNotFoundError` — distinct from `AmbiguousSourceSelectionError`
  (wrong pin, not conflicting pins) and from `ExperimentNotFoundError` (the experiment itself
  exists; only the pin is wrong)

#### Scenario: A raw-tier read on a source-versioned adapter reports how many sources it saw

- **WHEN** `load_experiment(name)` performs a raw-tier read against an adapter implementing
  `SourceSelectable`, and the experiment has more than one known source
- **THEN** the returned `ExperimentFrame.available_source_count` equals the number of sources
  `list_sources(name)` would report, without the adapter making a second `list_sources` call to
  compute it — a consumer building a source-ambiguity advisory reads this field rather than
  re-querying independently
