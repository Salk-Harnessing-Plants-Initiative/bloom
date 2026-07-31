## MODIFIED Requirements

### Requirement: ResultStore Port

The system SHALL define a backend-agnostic `ResultStore` port exposing `create_run(experiment, tool, params, provenance, user_label, source_csv, source)`, `commit(run, outputs)`, `list_runs(experiment, tool)`, and `get_run(experiment, tool, run_ref)`. `create_run` SHALL return a `RunHandle` exposing the allocated version id, the staging directory that consumers write outputs into, and the manifest path consumers surface in responses. `create_run`'s optional `source: Optional[SourceInfo]` parameter (mirroring the existing optional `source_csv` parameter) carries which database source/run backed the experiment read that produced this run, when the active `ExperimentReader` is `SourceSelectable`; when given, it SHALL be merged into the stored `Provenance` before the run's per-run state is recorded, so it survives to the committed `VersionEntry` without a caller needing to build a modified `Provenance` itself. `commit` SHALL return a `StoredRun` whose run reference is **opaque** (backend-specific concepts — `tool_class` naming, `v<N>`, the `latest` pointer, object keys — live in the adapter, not the port). Consumers SHALL depend only on this port — never on `AnalysisWriter`, `AnalysisDir`, or `supabase` directly.

#### Scenario: Create exposes a writable staging surface and version id

- **WHEN** a consumer calls `create_run(experiment, tool, params, provenance)`
- **THEN** the returned `RunHandle` exposes the allocated version id and a staging directory path the consumer can write output files into before commit

#### Scenario: Create with a source pins it onto the run's provenance

- **WHEN** a consumer calls `create_run(experiment, tool, params, provenance, source=SourceInfo(source_id=7, source_name="reprocess-2026-07", pipeline_run_id=None))`
- **THEN** the run's stored provenance carries `source_id=7`/`source_name="reprocess-2026-07"`, without the consumer having to call `provenance.model_copy(...)` itself

#### Scenario: Commit records a versioned run and returns its links

- **WHEN** a consumer writes outputs into the run's staging directory and calls `commit(run, outputs)`
- **THEN** the store records a new version for that experiment and tool and returns a `StoredRun` describing the committed run reference, its manifest path, and its artifact links

#### Scenario: get_run resolves the most recent run

- **WHEN** two `create_run`→`commit` cycles complete for the same experiment and tool
- **THEN** `list_runs(experiment, tool)` returns both in order, `get_run(experiment, tool, "latest")` resolves to the second, and `get_run` for the first run's reference resolves to the first

#### Scenario: Unknown run reference is reported through the contract

- **WHEN** `get_run(experiment, tool, run_ref)` is called for a reference or tool with no recorded run
- **THEN** it surfaces a structured not-found condition (no raw traceback), and `list_runs` for an experiment with no runs returns an empty list

#### Scenario: Lifecycle misuse is rejected

- **WHEN** `commit` is called twice on the same `RunHandle`, or with a handle that was never created by `create_run`
- **THEN** the store rejects the call rather than silently double-recording or corrupting the manifest

#### Scenario: Write consumers depend only on the port

- **WHEN** `tools/workflows/_helpers.py` and the five workflows are inspected
- **THEN** none import `AnalysisWriter`, `AnalysisDir`, or `supabase` directly; each receives a `ResultStore`

### Requirement: Provenance Persisted at Commit

The `ResultStore` SHALL persist the Tier 1 `Provenance` into the committed run's manifest entry by building the `VersionEntry` via `Provenance.to_version_entry`, so `seed`, `agent`, `environment`, `code_versions`, and — when `create_run` was given a `source` — `source_id`/`source_name` are recorded, closing the gap where `AnalysisWriter.commit` hand-rolls a provenance-lossy entry.

#### Scenario: Provenance fields round-trip into the version entry

- **WHEN** a run carrying a stamped `Provenance` is committed
- **THEN** the committed manifest entry equals `provenance.to_version_entry(version_id=...)` for `tool`, `params`, `seed`, `agent`, `environment`, and `code_versions`, with the resolved (non-null) seed recorded

#### Scenario: A source given at create_run is recorded, absent otherwise

- **WHEN** a run created with `create_run(..., source=SourceInfo(source_id=7, source_name="reprocess-2026-07", pipeline_run_id=None))` is committed, versus a run created with no `source` argument
- **THEN** the first committed entry's `source_id`/`source_name` equal `7`/`"reprocess-2026-07"`; the second's are both `None`, not a fabricated value

#### Scenario: Input hash stays on the experiment block

- **WHEN** a run is committed
- **THEN** the input content hash is recorded on the manifest `ExperimentBlock` (not duplicated onto the `VersionEntry`), preserving the deployed manifest shape
