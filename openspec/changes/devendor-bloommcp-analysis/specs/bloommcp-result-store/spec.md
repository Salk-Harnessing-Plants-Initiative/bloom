## MODIFIED Requirements

### Requirement: ResultStore Port

The system SHALL define a backend-agnostic `ResultStore` port exposing `create_run(experiment, tool, params, provenance, user_label)`, `commit(run, outputs)`, `list_runs(experiment, tool)`, and `get_run(experiment, tool, run_ref)`. `create_run` SHALL return a `RunHandle` exposing the allocated version id, the staging directory that consumers write outputs into, and the manifest path consumers surface in responses. `commit` SHALL return a `StoredRun` whose run reference is **opaque** (backend-specific concepts — `tool_class` naming, `v<N>`, the `latest` pointer, object keys — live in the adapter, not the port). Consumers SHALL depend only on this port — never on `AnalysisWriter`, `AnalysisDir`, or `supabase` directly.

#### Scenario: Create exposes a writable staging surface and version id

- **WHEN** a consumer calls `create_run(experiment, tool, params, provenance)`
- **THEN** the returned `RunHandle` exposes the allocated version id and a staging directory path the consumer can write output files into before commit

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

- **WHEN** the persistence-writing tools (`pca_analysis`, `qc_clean`, `qc_inspect`, `remove_outliers`,
  `clustering` — landed via #309/#422 after this scenario was first drafted, also writes a versioned
  run through the port) are inspected
- **THEN** none imports `AnalysisWriter`, `AnalysisDir`, or `supabase` directly; each obtains a `ResultStore` through the injected `_ports` seam. (The `sleap_roots` section plotting tools write PNGs to `PLOTS_DIR` and do not persist through the `ResultStore` port, so they are out of scope for this guarantee.)

### Requirement: Live Supabase Persistence Smoke

A live smoke SHALL drive at least one **surviving granular persistence tool** end-to-end
through the real `SupabaseResultStore` and `SupabaseReader` against the running dev stack
(Supabase + storage-api + MinIO) and assert the write-path guarantees the persistence layer
provides: a committed run lands in storage with a v3 manifest carrying resolved provenance,
each recorded content hash equals the bytes actually stored, `get_run("latest")` reads the
committed run back and advances on a second commit, and `import bloom_mcp` is clean with no
Supabase env. The smoke SHALL exit non-zero and name the failing check on any violated
guarantee, so a regression fails the job rather than passing silently. (The smoke previously
drove `run_clustering_workflow`; after the Phase-1 workflow retirement it drives a surviving
tool — e.g. `remove_outliers`, which resolves a seed for a seeded outlier method, or a
deterministic tool such as `pca_analysis`.)

#### Scenario: Committed run lands with a v3 manifest and resolved provenance

- **WHEN** the smoke drives a surviving granular tool through the real `SupabaseResultStore`
  and reads the `manifest.json` back from storage via the real read path
- **THEN** the manifest's schema version equals 3 and its latest `VersionEntry` carries the
  tool's resolved provenance — a non-null `seed` equal to the resolved value when a seeded
  tool is driven, or `null` for a deterministic tool — an `agent` equal to `bloom_agent`, a
  populated `environment`, and non-empty `output_sha256` and `output_keys` maps sharing one
  key-set

#### Scenario: Recorded hash equals the bytes actually stored

- **WHEN** the smoke downloads each object named in the latest entry's `output_keys` from
  the bucket and hashes the returned bytes
- **THEN** each `sha256(downloaded bytes)` equals the corresponding `output_sha256` value
  recorded in the manifest

#### Scenario: get_run("latest") reads back and advances on a second commit

- **WHEN** the smoke calls `get_run(experiment, tool_class, "latest")` after the first
  commit, then runs the tool a second time
- **THEN** the first `get_run("latest")` resolves the committed run, and after the second
  run `latest` advances from `v1` to `v2`

#### Scenario: Import is clean with no Supabase env

- **WHEN** the smoke runs `import bloom_mcp` (including the Tier-2 `_ports` composition
  root that constructs adapters at module load) in a subprocess with `SUPABASE_URL` and
  `BLOOM_AGENT_KEY` removed from the environment, before configuring the live env
- **THEN** the import succeeds with no error, proving the Tier-0 lazy-validation contract
  holds for the real composition root

#### Scenario: A violated guarantee fails the smoke

- **WHEN** any asserted guarantee does not hold — for example a downloaded object's hash
  does not match the recorded `output_sha256`, the resolved provenance is wrong, or the tool
  returns an error
- **THEN** the smoke routes the failure through its per-check summary and exits non-zero,
  naming the failing check, rather than passing or aborting with an unlabelled traceback

## REMOVED Requirements

### Requirement: Workflows Repointed to the ResultStore Port

**Reason:** The Phase-1 workflow tools (`qc`, `stats`, `dimred`, `clustering`, `outlier`) are
retired by this change (see `bloommcp-tool-sections` → "Phase-1 Workflow Tools Retired"), so
there are no "existing workflows" to repoint. The persistence-via-port guarantee for the
surviving consumers is now carried by the "ResultStore Port" requirement's "Write consumers
depend only on the port" scenario, which enumerates the granular tools instead.

**Migration:** No runtime migration — the granular tools already persist through the
`ResultStore` port. Existing persisted runs keep their historical `tool_class` names
(`qc`, `dimred`, etc.); `list_existing_analyses` continues to read them.
