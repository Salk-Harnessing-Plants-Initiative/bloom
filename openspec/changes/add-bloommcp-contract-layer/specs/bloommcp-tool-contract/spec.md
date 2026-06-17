## ADDED Requirements

### Requirement: Uniform Tool Contract Decorator

bloom-mcp SHALL provide an `@as_mcp_tool` decorator in `bloom_mcp.contract.wrap` that
wraps a tool function with the contract guarantees: it SHALL validate the tool's declared
Pydantic input and output models, stamp exactly one contract-time `Provenance` record (see
the Canonical Contract-Time Provenance Record requirement), and map exceptions to a
structured `BloomMCPError`. The decorator SHALL wrap, not replace, FastMCP; actual
registration onto a FastMCP instance SHALL occur through a `register(mcp)` seam invoked at
server-wiring time — the decorator SHALL NOT require a live `FastMCP` instance at
decoration time (Tier 1 does not modify `server.py`; the first real registration lands in
Tier 3). The decorator SHALL NOT maintain a per-tool version field (tool identity is the
tool name; versioning is package-SemVer).

#### Scenario: Decorated stub tool validates I/O and is registrable

- **WHEN** a stub tool with declared Pydantic input/output models is decorated with
  `@as_mcp_tool`, invoked with valid input, and then registered onto an in-process
  `FastMCP` instance via the `register(mcp)` seam
- **THEN** the call returns the validated output object, and the tool is discoverable
  under its name via an in-process `fastmcp.Client(mcp).list_tools()`

#### Scenario: Invalid input is rejected before the tool body runs

- **WHEN** a decorated stub tool (whose body sets a call-recording flag) is invoked with
  input that violates its Pydantic input model
- **THEN** the failure surfaces as a `BloomMCPError` and the call-recording flag shows the
  tool body never executed

#### Scenario: Invalid output is an internal contract breach, not a raw error

- **WHEN** a decorated stub tool returns a value that violates its declared output model
- **THEN** the failure surfaces as a `BloomMCPError` whose `code` marks an internal
  output-contract breach (distinct from a user input error), never a raw `ValidationError`
  or traceback

### Requirement: Structured Agent-Safe Errors

bloom-mcp SHALL define `BloomMCPError` in `bloom_mcp.contract.errors` carrying a `code`, a
`message`, and a `remedy`, with a serializable structured form. The `@as_mcp_tool`
decorator SHALL map declared exceptions raised by a tool to a `BloomMCPError`; a raw
traceback SHALL NEVER be returned to the agent.

#### Scenario: Declared exception becomes a structured error

- **WHEN** a decorated stub tool raises a declared exception
- **THEN** the caller receives a `BloomMCPError` with `code`, `message`, and `remedy`
  populated, and no raw traceback or stack frames are exposed

### Requirement: Seed Recording And Propagation Without Global Re-Seed

The `@as_mcp_tool` decorator SHALL record the **resolved** seed (`random_state`) actually
used by the call in the `Provenance` record and SHALL propagate it into the delegated
`perform_*` call as `random_state=`. When the tool's params carry no seed, the decorator
SHALL resolve a concrete integer seed before delegating and record that integer (the
recorded seed SHALL never be null for a stochastic delegation). The decorator SHALL NOT
call `np.random.seed()` or otherwise mutate global RNG state. The kwarg-injection contract
(the decorator forwards `random_state=<resolved seed>` to the declared delegate) SHALL be
explicit so downstream tiers depend on a defined behavior, not an inferred one.

#### Scenario: Provided seed is recorded and forwarded, global RNG untouched

- **WHEN** a decorated stub tool delegating to a fake `perform_*` is invoked with a `seed`
  in its params
- **THEN** the fake `perform_*` receives that value as `random_state=`, the `Provenance`
  record's `seed` equals that value, and `np.random.get_state()` is byte-identical before
  and after the call

#### Scenario: Absent seed is resolved to a concrete integer and recorded

- **WHEN** a decorated stub tool is invoked with no seed in its params
- **THEN** the decorator resolves a concrete integer seed, forwards it as `random_state=`,
  and records that same integer in `Provenance.seed` (never null)

### Requirement: Canonical Contract-Time Provenance Record

bloom-mcp SHALL define a canonical `Provenance` model in `bloom_mcp.contract.provenance`,
stamped exactly once per tool call at **contract time** (around delegation, before any
artifact is written). It SHALL carry the contract-time fields: `tool`, `params`, the
resolved `seed`, `agent`/actor, the source-input digest (`input_sha256`, which on the
manifest lives on the experiment block, not the version entry), the `code_versions` trace,
the `environment` pointer, and timestamps. Because PCA/clustering numbers depend on the
exact matrix fed to `perform_*`, `params` SHALL faithfully capture the resolved
feature/column-role selection and the determinism-governing parameters (e.g. `svd_solver`,
`n_components`, `n_init`) — not a lossily coerced subset. Per-artifact `output_sha256` and
logical `key` are NOT contract-time fields; they are populated at commit by the
`ResultStore` (Tier 2) and merged into the same single version entry. The `Provenance`
model SHALL round-trip Pydantic ↔ JSON with **exact** equality.

#### Scenario: Contract-time Provenance round-trips through JSON exactly

- **WHEN** a fully-populated contract-time `Provenance` (including the resolved `seed`, the
  `environment` pointer, and the resolved feature/column selection in `params`) is
  serialized to JSON and parsed back
- **THEN** the reconstructed model equals the original exactly (`==`), with no numeric
  precision loss and no dropped fields

#### Scenario: Per-artifact hashes are absent at contract time

- **WHEN** the decorator stamps a `Provenance` record before delegation
- **THEN** the per-artifact `output_sha256` / logical `key` collections are empty/unset,
  reflecting that they are filled at commit by the `ResultStore` in a later tier

### Requirement: Provenance Maps Into The Manifest VersionEntry

bloom-mcp SHALL provide a single mapping from a contract-time `Provenance` to a manifest
`VersionEntry` (schema v3) so that provenance has one home in the manifest, not a parallel
record. The mapping SHALL be unit-testable without a live Supabase connection and without
performing a live manifest write (the live write, and the population of per-artifact
`output_sha256` / `key`, are the `ResultStore`'s responsibility in a later tier). The
mapping SHALL preserve the existing v2 `VersionEntry` fields (`id`, `created_at`, `tool`,
`params`, `based_on_version`, `code_versions`, `outputs`, `user_label`, `version_dir`).

#### Scenario: Mapping yields a v3 VersionEntry with contract-time fields set

- **WHEN** a contract-time `Provenance` record is mapped to a `VersionEntry`
- **THEN** the entry carries `seed`, `agent`, the extended `code_versions`, and the
  `environment` pointer; it preserves the existing v2 fields (`id`, `created_at`, `tool`,
  `params`, `based_on_version`, `code_versions`, `outputs`, `user_label`, `version_dir`);
  and its per-artifact `output_sha256` / `key` collections are empty (to be filled at
  commit in Tier 2)

### Requirement: Installed-Only Code Versions

The `code_versions` provenance SHALL record a version only for an actually pip-installed
distribution, for **every** entry (including `bloommcp` and `supabase`), and SHALL extend
the set with `sleap-roots-analyze` and `sleap-roots-contracts`. A distribution that is not
installed SHALL be omitted rather than recorded as the literal `"unknown"`. This makes the
existing `supabase: str = "unknown"` default an omit-when-absent optional field (a benign,
additive behavior change).

#### Scenario: Installed distributions are recorded with no "unknown"

- **WHEN** `code_versions` is computed where `sleap-roots-analyze` and
  `sleap-roots-contracts` are installed
- **THEN** their versions are recorded and no `code_versions` field holds the literal
  string `"unknown"`

#### Scenario: Uninstalled distribution is omitted, not "unknown"

- **WHEN** `code_versions` is computed and a tracked distribution is not installed
  (`importlib.metadata.version` raises `PackageNotFoundError`)
- **THEN** that key is absent from the result rather than set to `"unknown"`

### Requirement: Exact-Environment Provenance Pointer

The `Provenance` record SHALL carry an `environment` pointer identifying the exact
environment that produced the artifact, resolved by a documented precedence chain:
container image digest (from `BLOOM_MCP_IMAGE_DIGEST`, a `sha256:…` value) → the
`bloom-mcp` release version whose committed `uv.lock` reproduces the env via `uv sync` →
a `uv.lock` content hash. The pointer is distinct from the human-readable `code_versions`
trace. The field MAY be absent in unit/dev environments (it is optional for schema
back-compat), but any **persisted** run (Tier 2) SHALL carry at least one identifier that
actually resolves to a reproducible environment.

#### Scenario: Image digest takes precedence when present

- **WHEN** a `Provenance` record is stamped with `BLOOM_MCP_IMAGE_DIGEST` set
- **THEN** the `environment` pointer equals that `sha256:…` digest

#### Scenario: Fallback resolves to a non-empty, reproducible identifier

- **WHEN** a `Provenance` record is stamped with no image digest set (the unit/CI case)
- **THEN** the `environment` pointer falls back per the precedence chain to a non-empty
  value (the `bloom-mcp` version and/or `uv.lock` hash), is never the literal `"unknown"`,
  and is not equal to the `code_versions` trace

### Requirement: Additive Manifest Schema v3

The manifest `VersionEntry` and `CodeVersions` schema SHALL advance from version 2 to
version 3 **additively** under the existing `extra="forbid"` strictness: the existing
`outputs: dict[str, str]` field SHALL be retained unchanged, and the per-artifact content
hashes and logical keys SHALL be carried in NEW optional sibling collections (e.g.
`output_sha256: dict[str, str]`, `output_keys: dict[str, str]`, keyed by the same logical
output name). All new `VersionEntry` fields (`seed`, `agent`, `environment`, and the
per-artifact sibling collections) and new `CodeVersions` fields SHALL be optional so that
previously-written **v2** manifests — including their string-valued `outputs` — continue
to validate and read without error. `output_sha256` values SHALL be hex-encoded SHA-256
over the exact pre-upload artifact bytes (app-computed, never the object-store ETag),
populated at commit by the `ResultStore` (Tier 2). `CURRENT_SCHEMA_VERSION` SHALL be `3`,
and the schema-version guard SHALL continue to accept any manifest whose version is less
than or equal to the known version.

#### Scenario: Old v2 manifest with string outputs still reads under v3 code

- **WHEN** a manifest recorded under schema version 2 — including a `VersionEntry` with a
  populated string-valued `outputs` (e.g. `{"cleaned": "_cleaned.csv"}`) and no v3 fields —
  is loaded by the v3 code
- **THEN** it validates and loads without error, and its absent v3 fields default to unset
  rather than failing `extra="forbid"` validation

#### Scenario: New manifests are written at schema version 3

- **WHEN** a new manifest is created after this change
- **THEN** `CURRENT_SCHEMA_VERSION` is `3`, a freshly built `Manifest` reports
  `manifest_schema_version == 3`, and a `VersionEntry` can carry the new v3 fields

#### Scenario: A v3 VersionEntry round-trips through JSON

- **WHEN** a `VersionEntry` carrying the new v3 fields (retained string `outputs` plus the
  per-artifact `output_sha256` / `output_keys` siblings, `seed`, `agent`, `environment`)
  is dumped with `model_dump(mode="json")` and re-validated
- **THEN** the reconstructed entry equals the original exactly
