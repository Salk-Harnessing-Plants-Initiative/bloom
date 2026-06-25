# bloommcp-tool-contract Specification

## Purpose
TBD - created by archiving change add-bloommcp-contract-layer. Update Purpose after archive.
## Requirements
### Requirement: Uniform Tool Contract Decorator

bloom-mcp SHALL provide an `@as_mcp_tool` decorator in `bloom_mcp.contract.wrap` that
wraps a tool function with the contract guarantees: it SHALL validate the tool's declared
Pydantic input and output models, stamp exactly one contract-time `Provenance` record (see
the Canonical Contract-Time Provenance Record requirement), and map exceptions to a
structured `BloomMCPError`. The decorator SHALL wrap, not replace, FastMCP; actual
registration onto a FastMCP instance SHALL occur through a `register(mcp, *tools)` seam
(provided in `bloom_mcp.contract`) invoked at server-wiring time — the decorator SHALL NOT
require a live `FastMCP` instance at decoration time (Tier 1 does not modify `server.py`;
the first real `server.py` wiring lands in Tier 3). The wrapped callable SHALL advertise a
single `params` parameter (so FastMCP builds a correct input schema) that is accepted both
positionally and by keyword. The decorator SHALL NOT maintain a per-tool version field
(tool identity is the tool name; versioning is package-SemVer).

#### Scenario: Decorated stub tool validates I/O and is registrable

- **WHEN** a stub tool with declared Pydantic input/output models is decorated with
  `@as_mcp_tool`, invoked with valid input, and then registered onto an in-process
  `FastMCP` instance via `register(mcp, stub_tool)`
- **THEN** the call returns the validated output object, and the tool is discoverable
  under its name via an in-process `fastmcp.Client(mcp).list_tools()`

#### Scenario: The params argument is accepted positionally and by keyword

- **WHEN** a decorated stub tool is invoked as `tool({...})`, `tool(params={...})`, and
  `tool(params=Model(...))`
- **THEN** all three validate identically — the real signature matches the advertised
  single-`params` schema (no positional-only mismatch)

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
decorator SHALL map a *declared* (author opted-in via `errors=`) exception to a
`BloomMCPError` whose message is passed through. A raw traceback SHALL NEVER be returned to
the agent, and an *internal* failure (an undeclared exception or an output-contract breach)
SHALL NOT leak internal detail (paths, hosts, connection strings, SQL, bucket keys): it
SHALL return a fixed message plus a short correlation id, with the detail logged
server-side. Input-validation errors SHALL surface only the offending field locations and
error types, never the offending values.

#### Scenario: Declared exception becomes a structured error

- **WHEN** a decorated stub tool raises a declared exception
- **THEN** the caller receives a `BloomMCPError` with `code`, `message`, and `remedy`
  populated, and no raw traceback or stack frames are exposed

#### Scenario: Internal failure does not leak detail to the agent

- **WHEN** a decorated stub tool raises an undeclared exception whose text carries a
  connection string / host / bucket key
- **THEN** the `BloomMCPError` is `internal_error`, its message omits that detail and
  carries a correlation id (`ref:`), and the detail is logged server-side only

#### Scenario: Input validation surfaces locations, not values

- **WHEN** input validation fails on a field whose value is sensitive
- **THEN** the `BloomMCPError` (`invalid_input`) names the field location and error type
  but not the offending value

### Requirement: Seed Recording And Propagation Without Global Re-Seed

The `@as_mcp_tool` decorator SHALL propagate the seed into the delegated `perform_*` call
as `random_state=` via an **explicit** kwarg-injection contract (the resolved seed is
passed as `random_state=` when, and only when, the delegate declares a `random_state`
parameter) — never inferred. It SHALL record the **resolved** seed in `Provenance` *only
when the seed is actually applied*: for a stochastic delegate (one declaring
`random_state`) the recorded seed SHALL be a concrete integer (resolving one when the
params carry none), never null. A non-stochastic tool (whose delegate declares no
`random_state`) SHALL record `seed=None`. If a seed is *explicitly provided* but the
delegate cannot accept it, the decorator SHALL raise an `internal_error` rather than record
a seed that never reached the computation. A provided seed SHALL be a plain integer in
`[0, 2**32)` — a float/bool/out-of-range value SHALL be rejected as `invalid_input`. The
decorator SHALL NOT call `np.random.seed()` or otherwise mutate global RNG state.

#### Scenario: Provided seed is recorded and forwarded, global RNG untouched

- **WHEN** a decorated stub tool delegating to a fake `perform_*` is invoked with a `seed`
  in its params
- **THEN** the fake `perform_*` receives that value as `random_state=`, the `Provenance`
  record's `seed` equals that value, and `np.random.get_state()` is byte-identical before
  and after the call

#### Scenario: Absent seed is resolved to a concrete integer and recorded

- **WHEN** a decorated stub tool delegating to a stochastic `perform_*` is invoked with no
  seed in its params
- **THEN** the decorator resolves a concrete integer seed, forwards it as `random_state=`,
  records that same integer in `Provenance.seed` (never null), and leaves
  `np.random.get_state()` byte-identical before and after

#### Scenario: Non-stochastic tool records no seed

- **WHEN** a decorated tool whose delegate declares no `random_state` is invoked with no
  seed
- **THEN** the `Provenance` record's `seed` is `None` (no fabricated seed)

#### Scenario: Provided seed the delegate cannot apply is an internal error

- **WHEN** a seed is provided to a tool whose delegate declares no `random_state`
- **THEN** the call raises a `BloomMCPError` with code `internal_error` (rather than
  recording an unapplied seed)

### Requirement: Canonical Contract-Time Provenance Record

bloom-mcp SHALL define a canonical `Provenance` model in `bloom_mcp.contract.provenance`,
stamped at most once per tool call at **contract time** (around delegation, before any
artifact is written; skipped on input-validation failure, discarded on later failure
paths). It SHALL carry the contract-time fields: `tool`, `params`, the resolved `seed`
(or `None` for a non-stochastic tool), `agent`/actor, the source-input digest
(`input_sha256`, which on the
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
and the schema-version guard SHALL accept any manifest whose version is less than or equal
to the known version and SHALL reject one that is newer. Because new v3 entries would trip
`extra="forbid"` if read by old v2 code, a deployment SHALL upgrade readers before any
writer emits v3 — this is a Tier 2 (live-write) deploy gate, recorded here.

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

#### Scenario: A newer manifest version is rejected

- **WHEN** the schema-version guard reads a manifest declaring a version newer than the
  known version (e.g. `4`)
- **THEN** it raises a schema error rather than silently accepting unknown structure

#### Scenario: A v3 VersionEntry round-trips through JSON

- **WHEN** a `VersionEntry` carrying the new v3 fields (retained string `outputs` plus the
  per-artifact `output_sha256` / `output_keys` siblings, `seed`, `agent`, `environment`)
  is dumped with `model_dump(mode="json")` and re-validated
- **THEN** the reconstructed entry equals the original exactly

