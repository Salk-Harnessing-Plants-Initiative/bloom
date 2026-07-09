# bloommcp-packaging Specification

## Purpose
TBD - created by archiving change add-bloommcp-package-baseline. Update Purpose after archive.
## Requirements
### Requirement: Installable Package Layout

The `bloommcp` service SHALL be an installable `uv` package rooted at
`bloommcp/src/bloom_mcp/`, with the former `source/`, `tools/`, and `storage/` modules
(including `supabase_client`) importable under the `bloom_mcp.*` namespace. The package
SHALL declare a `[build-system]` and `src/` package discovery so `uv build` produces an
importable wheel. The restructure SHALL be additive: the booting MCP server's tool
surface and behavior SHALL remain unchanged.

#### Scenario: Built wheel imports under the new namespace

- **WHEN** the package is built with `uv build` and the resulting wheel is installed into
  a clean environment
- **THEN** `bloom_mcp`, `bloom_mcp.tools`, and `bloom_mcp.storage` import without error
  and expose the same tool surface as the pre-restructure prototype

#### Scenario: No stale prototype imports remain

- **WHEN** every module under `bloommcp/src/bloom_mcp/` is scanned for import statements
- **THEN** no import has a first dotted segment of `source`, `tools`, or `storage` —
  every intra-package import resolves under `bloom_mcp.*` (imports of `bloom_mcp.tools`
  / `bloom_mcp.storage` are not matches)

### Requirement: Container Entry Point Preserved

The bloommcp container SHALL build and launch the server against the `src/` layout. The
`Dockerfile` SHALL install the package so `bloom_mcp.*` resolves at runtime, and the
dev compose bind-mount SHALL still reflect local source edits (hot-reload preserved).

#### Scenario: Image builds and boots under both compose files

- **WHEN** the bloommcp image is built via its `Dockerfile` and started under
  `docker-compose.dev.yml` (bind-mounted) and `docker-compose.prod.yml`
- **THEN** the server process starts, resolves `bloom_mcp.*`, and `/health` returns OK
  in both

### Requirement: Lazy Environment Validation

No `bloom_mcp` module SHALL validate runtime environment at import time. Both the
`bloom_mcp.supabase_client` Supabase credentials (`SUPABASE_URL`,
`BLOOM_AGENT_KEY`) and the `bloom_mcp.experiment_utils` data directories
(`BLOOM_TRAITS_DIR`, `BLOOM_OUTPUT_DIR`, `BLOOM_PLOTS_DIR`, `BLOOM_PLOTS_URL`)
SHALL be validated only by an explicit `validate_env()` (and, for Supabase, at
first access), so that `import bloom_mcp` and the fakes-based unit tests succeed
with **no** runtime environment set.

#### Scenario: Import succeeds with no runtime env

- **WHEN** `import bloom_mcp.server` runs in a fresh interpreter with none of
  `SUPABASE_URL`, `BLOOM_AGENT_KEY`, or the `BLOOM_*_DIR` / `BLOOM_PLOTS_URL`
  variables set
- **THEN** the import succeeds and raises no `RuntimeError`

#### Scenario: First Supabase access validates and names the missing variable

- **WHEN** a Supabase client accessor is called with `SUPABASE_URL` set but
  `BLOOM_AGENT_KEY` unset (and the symmetric case, and both unset)
- **THEN** an error is raised at that call site naming exactly the missing variable, and
  no error is raised for a variable that is set

#### Scenario: Data-directory validation defers to validate_env

- **WHEN** `bloom_mcp.experiment_utils.validate_env()` is called with any of the
  `BLOOM_*_DIR` / `BLOOM_PLOTS_URL` variables unset
- **THEN** it raises a `RuntimeError` naming the missing variable(s), while merely
  importing the module did not

### Requirement: Server Boot Fail-Fast Preserved

The MCP server SHALL fail fast at startup when its runtime environment is missing, via
explicit `validate_env()` calls (Supabase credentials and data directories) before
`mcp.run()` rather than an import-time side effect, so a misconfigured deploy fails at
container boot before serving requests. The `/health` endpoint SHALL continue to report
healthy on a correctly configured boot.

#### Scenario: Misconfigured deploy fails at boot

- **WHEN** the server starts with `SUPABASE_URL` / `BLOOM_AGENT_KEY` **or** any
  `BLOOM_*_DIR` / `BLOOM_PLOTS_URL` variable unset
- **THEN** a validator raises a clear error naming the missing variable before the port
  is bound or requests are served

#### Scenario: Configured server boots healthy

- **WHEN** the server starts with the Supabase environment correctly set
- **THEN** it boots and `/health` returns OK

### Requirement: Additive Dependency Set

The `bloommcp` `pyproject.toml` SHALL declare a publication-ready project that adds
`sleap-roots-analyze>=0.1.0a2` and `sleap-roots-contracts[pandas]>=0.1.0a1` (the oracle
+ Phase-2 foundation) while retaining the analysis dependencies still imported directly
by the vendored modules. No dependency that is still imported by shipped code SHALL be
removed. Committed lockfiles (`bloommcp/uv.lock` + root) SHALL stay in sync with their
`pyproject.toml`.

#### Scenario: Build and import succeed in a clean environment

- **WHEN** the package is built with `uv` and imported in a clean environment, and the
  dev group is resolved
- **THEN** the build succeeds, `import bloom_mcp` works, and no runtime dependency is
  missing

#### Scenario: Lockfiles are in sync

- **WHEN** `uv lock --check` runs against the committed `bloommcp/uv.lock` and the root
  lock (and `scripts/check-uv-locks.py` runs)
- **THEN** each reports the lockfile in sync with its `pyproject.toml`

### Requirement: Supabase-Free Test Stack with Cross-Tier Oracle

The package SHALL provide a `bloommcp/tests/` layout using `pytest`, `hypothesis`,
`syrupy`, and the FastMCP `Client`, runnable with fakes and **no live Supabase**, and
this suite SHALL be executed by CI. The `talmolab/sleap-roots-analyze#120` turface_19
fixture and its independently recorded golden values SHALL be committed under
`bloommcp/tests/fixtures/` and asserted — with explicit numeric tolerances, not
auto-generated snapshots — by oracle tests that **both** the external
`sleap_roots_analyze` and the shipped `bloom_mcp` analysis reproduce.

#### Scenario: Suite collects, runs without Supabase, and is gated by CI

- **WHEN** the CI bloommcp test job runs `uv run pytest` with no live Supabase and
  `SUPABASE_URL` / `BLOOM_AGENT_KEY` unset
- **THEN** the suite collects and the unit tests pass using fakes, and the job fails the
  PR if they do not

#### Scenario: Oracle reproduces independently recorded golden values

- **WHEN** the external `sleap_roots_analyze` and the shipped `bloom_mcp` PCA functions
  run on the committed turface_19 fixture
- **THEN** their outputs match the independently recorded `talmolab/sleap-roots-analyze#120`
  golden values in `bloommcp/tests/fixtures/` within the stated tolerance

### Requirement: CI Gates the Built-Wheel Import

CI SHALL build the `bloommcp` wheel and import it from a clean environment that cannot
see the `bloommcp/src/` tree, so a packaging regression that ships an unimportable wheel
fails the PR. The import SHALL cover `bloom_mcp`, `bloom_mcp.tools`, `bloom_mcp.storage`,
and `bloom_mcp.server`, SHALL verify the imported package resolves from the installed
wheel and not the source checkout, and SHALL run with no usable Supabase environment
(`SUPABASE_URL` / `BLOOM_AGENT_KEY` empty) so the lazy-validation contract is
load-bearing. CI SHALL retain a regression-guard test asserting the gate's presence and
its load-bearing assertions so it cannot be silently deleted or hollowed out. Built
artifacts (`bloommcp/dist/`) SHALL NOT be committed. (The exact `uv` invocation and the
rationale for omitting `--isolated` live in the proposal and tasks, not this contract.)

#### Scenario: Clean-env wheel import is gated by CI

- **WHEN** the `python-audit` job builds the wheel and imports `bloom_mcp` and its
  `tools`, `storage`, and `server` submodules in an environment that does not place
  `bloommcp/src/` on the import path
- **THEN** the import resolves the shipped wheel (verified by checking the imported
  package's file location, not the `src/` checkout) and a packaging regression — a
  misconfigured `module-name` / `module-root`, a dropped `__init__.py`, or a wheel that
  ships an empty namespace — fails the job

#### Scenario: Wheel import gate runs with no Supabase env

- **WHEN** the clean-env import runs with `SUPABASE_URL` and `BLOOM_AGENT_KEY` set empty
- **THEN** `import bloom_mcp`, `bloom_mcp.tools`, `bloom_mcp.storage`, and
  `bloom_mcp.server` succeed and raise no `RuntimeError`, proving no import-time Supabase
  dependency

#### Scenario: Gate presence is regression-guarded

- **WHEN** the `tests/unit/` suite parses `.github/workflows/pr-checks.yml`
- **THEN** it asserts the `python-audit` job contains a step that builds the wheel in
  `bloommcp`, imports all four modules from a project-free environment, installs the
  built wheel, verifies the import resolved from the wheel (not `src/`), and pins
  `SUPABASE_URL` / `BLOOM_AGENT_KEY` empty — failing the PR if the gate is removed or any
  of its load-bearing assertions is dropped

### Requirement: Necessary-and-Sufficient Declared Dependencies

Every runtime dependency declared in `bloommcp/pyproject.toml` SHALL be imported by
shipped code (`src/bloom_mcp/**`), and no shipped code SHALL import a dependency that is
not declared. This *partially* reconciles #305 AC5 — it meets the **sufficient** half ("no
missing dep") for the two single-module-gated prunes, while the **necessary** half
(minimizing the viz-held deps) remains deferred to the shipped-viz refactor tracked by
#315. It satisfies the Tier 0 "Additive Dependency Set" requirement's conditional clause
("no dependency **still imported by shipped code** SHALL be removed") rather than
overriding it, because the prune happens only *after* delegation makes the deps unimported.
Specifically, `statsmodels` and `umap-learn` SHALL be removed (no shipped module imports
them after delegation), while `scikit-learn`, `scipy`, `matplotlib`, and `seaborn` SHALL
be retained because shipped visualization and plotting tools import them directly.
Committed lockfiles (`bloommcp/uv.lock` + root) SHALL stay in sync with their
`pyproject.toml`.

#### Scenario: Pruned dependencies are absent from declarations and shipped imports

- **WHEN** the package is inspected after delegation
- **THEN** `statsmodels` and `umap-learn` SHALL NOT appear in `bloommcp/pyproject.toml`
- **AND** no module under `src/bloom_mcp/**` SHALL import `statsmodels` or `umap`

#### Scenario: Every declared dependency is imported by shipped code

- **WHEN** each declared runtime dependency is checked against shipped imports
- **THEN** each SHALL be imported by at least one `src/bloom_mcp/**` module
- **AND** the retained `scikit-learn`, `scipy`, `matplotlib`, and `seaborn` SHALL each be
  traceable to a shipped visualization or plotting tool that imports it

#### Scenario: A shipped import of an undeclared dependency fails the guard

- **WHEN** a module under `src/bloom_mcp/**` imports a top-level package that is not a
  declared runtime dependency in `bloommcp/pyproject.toml`
- **THEN** the import guard SHALL fail
- **AND** the failure SHALL name the offending module and the undeclared import

#### Scenario: Clean-env wheel import resolves all runtime dependencies

- **WHEN** the built wheel is imported in a project-free environment
  (`uv run --no-project --with <wheel> python -c "import bloom_mcp, bloom_mcp.tools,
  bloom_mcp.storage, bloom_mcp.server"`)
- **THEN** the import SHALL succeed with no missing runtime dependency
- **AND** the resolved `bloom_mcp` SHALL come from the wheel, not the `src/` checkout

#### Scenario: Lockfiles stay in sync after the prune

- **WHEN** `uv lock --check` runs against `bloommcp/uv.lock` and the root lock (and
  `scripts/check-uv-locks.py` runs)
- **THEN** each SHALL report the lockfile in sync with its `pyproject.toml`

### Requirement: Heritability and UMAP Analysis Delegated to sleap-roots-analyze

The shipped trait-statistics/heritability and UMAP-embedding paths SHALL source their
analysis from `sleap_roots_analyze` rather than vendored copies. The vendored
`src/bloom_mcp/umap_embedding.py` and `src/bloom_mcp/trait_statistics.py` SHALL be
deleted. The external behavior of the MCP tools that use them (parameter and output
schema exposed to the agent) SHALL be unchanged, and their numerical output SHALL match
the committed turface_19 golden values within the stated tolerance, asserted by the
cross-tier oracle. The heritability golden SHALL be labeled as either an independently
reconciled reference value or an explicit `0.1.0a2` characterization snapshot (a
drift gate), and its `_source` SHALL point at a real heritability artifact. The UMAP gate
SHALL assert a structural invariant (not merely output shape), and the two affected tool
wrappers SHALL have their delegated return keys/units asserted so a library key-rename
fails rather than silently zero-filling.

#### Scenario: Delegated paths reproduce the golden within tolerance

- **WHEN** the shipped `bloom_mcp` heritability and UMAP paths run on the committed
  turface_19 fixture after delegation
- **THEN** their outputs SHALL match the committed golden values within the stated
  tolerance
- **AND** the same assertion SHALL hold for the external `sleap_roots_analyze` functions
  they delegate to
- **AND** the heritability golden SHALL be documented as an independently reconciled value
  or an explicit `0.1.0a2` characterization snapshot, with a `_source` pointing at a real
  heritability artifact (not a PCA-metadata file)

#### Scenario: UMAP delegation is gated on a structural invariant

- **WHEN** the UMAP oracle runs on the committed fixture after delegation
- **THEN** it SHALL assert a structural invariant against a recorded embedding (e.g.
  Procrustes-aligned coordinates or a kNN-overlap / trustworthiness check), not merely
  output shape plus within-process self-equality
- **AND** a delegation using the wrong `n_neighbors` / `min_dist` / `init` SHALL fail the
  gate even if it produces a same-shape deterministic embedding

#### Scenario: Tool wrappers assert the delegated return keys

- **WHEN** the heritability/variance-decomposition and UMAP MCP tools are exercised on the
  committed fixture
- **THEN** the test SHALL assert the delegated return contains the keys the wrappers
  consume — including `var_genetic` and `var_residual` for the variance-decomposition tool
- **AND** a renamed or dropped key SHALL fail the test rather than silently defaulting to
  zero

#### Scenario: Vendored modules removed without changing the tool surface

- **WHEN** the package is inspected after delegation
- **THEN** `src/bloom_mcp/umap_embedding.py` and `src/bloom_mcp/trait_statistics.py`
  SHALL NOT exist
- **AND** the UMAP and statistics/heritability MCP tools SHALL expose the same parameters
  and output schema to the agent as before delegation

#### Scenario: A drift between the vendored copy and the library is caught before deletion

- **WHEN** the oracle is extended to the delegated paths while the vendored modules still
  exist
- **THEN** any numerical divergence beyond tolerance between the vendored copy and
  `sleap_roots_analyze` SHALL fail the gate
- **AND** delegation SHALL proceed only once the gate is green

