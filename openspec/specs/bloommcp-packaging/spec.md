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
`bloom_mcp.supabase_client` Supabase credentials (`SUPABASE_URL`, `BLOOM_AGENT_KEY`) and the
`bloom_mcp.experiment_utils` data directories (`BLOOM_TRAITS_DIR`, `BLOOM_OUTPUT_DIR`,
`BLOOM_PLOTS_DIR`, `BLOOM_PLOTS_URL`) SHALL be validated only by an explicit `validate_env()`
(and, for Supabase, at first access), so that `import bloom_mcp` and the fakes-based unit tests
succeed with **no** runtime environment set. When `BLOOM_STORAGE_BACKEND=local` and the single
`BLOOM_LOCAL_ROOT` variable is set, `BLOOM_TRAITS_DIR`, `BLOOM_OUTPUT_DIR`, `BLOOM_PLOTS_DIR`,
**and `BLOOM_PLOTS_URL`** SHALL each become individually optional: `validate_env()` SHALL
instead require only that `BLOOM_LOCAL_ROOT` itself exists and is a writable directory, creating
the `input/`, `output/`, and `plots/` subfolders under it as needed (`BLOOM_PLOTS_URL` carries no
directory to create — it resolves to the self-served default described in "Local-Mode
Self-Served Plots URL"). Any of the four variables that IS explicitly set SHALL keep today's
stricter contract (the three directory variables must already exist; an explicitly-set
`BLOOM_PLOTS_URL` is used verbatim) regardless of `BLOOM_LOCAL_ROOT`. In every other combination
— a variable unset with no `BLOOM_LOCAL_ROOT`, or the default (non-local) backend — validation is
unchanged.

#### Scenario: Import succeeds with no runtime env

- **WHEN** `import bloom_mcp.server` runs in a fresh interpreter with none of `SUPABASE_URL`,
  `BLOOM_AGENT_KEY`, or the `BLOOM_*_DIR` / `BLOOM_PLOTS_URL` variables set
- **THEN** the import succeeds and raises no `RuntimeError`

#### Scenario: First Supabase access validates and names the missing variable

- **WHEN** a Supabase client accessor is called with `SUPABASE_URL` set but `BLOOM_AGENT_KEY`
  unset (and the symmetric case, and both unset)
- **THEN** an error is raised at that call site naming exactly the missing variable, and no error
  is raised for a variable that is set

#### Scenario: Data-directory validation defers to validate_env

- **WHEN** `bloom_mcp.experiment_utils.validate_env()` is called with any of the `BLOOM_*_DIR` /
  `BLOOM_PLOTS_URL` variables unset, and this is not the `BLOOM_STORAGE_BACKEND=local` +
  `BLOOM_LOCAL_ROOT` combination
- **THEN** it raises a `RuntimeError` naming the missing variable(s), while merely importing the
  module did not

#### Scenario: BLOOM_LOCAL_ROOT makes all four variables optional

- **WHEN** `validate_env()` runs with `BLOOM_STORAGE_BACKEND=local`, `BLOOM_LOCAL_ROOT` set to an
  existing writable directory, and `BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` /
  `BLOOM_PLOTS_URL` all unset
- **THEN** validation succeeds; the `input/`, `output/`, and `plots/` subfolders are created
  under `BLOOM_LOCAL_ROOT` if they do not already exist; and `experiment_utils.PLOTS_URL`
  resolves to the self-served default rather than the empty string

#### Scenario: An explicit override still requires pre-existence

- **WHEN** `BLOOM_LOCAL_ROOT` is set and each of `BLOOM_PLOTS_DIR`, `BLOOM_EXPERIMENT_LOCAL_ROOT`,
  and `BLOOM_STORAGE_LOCAL_ROOT` is independently tested, explicitly set to a path that does not
  exist
- **THEN** validation still raises for each, rather than auto-creating the explicitly-named path

#### Scenario: The default (non-local) backend's directory requirements are unaffected

- **WHEN** `BLOOM_STORAGE_BACKEND` is unset or `supabase`, `BLOOM_LOCAL_ROOT` is set anyway (e.g.
  left over in a shell profile), and `BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` /
  `BLOOM_PLOTS_URL` are unset
- **THEN** `validate_env()` still raises naming the missing variables, exactly as before this
  change — `BLOOM_LOCAL_ROOT` has no effect outside `BLOOM_STORAGE_BACKEND=local`

#### Scenario: The resolved PLOTS_DIR constant reflects the BLOOM_LOCAL_ROOT default

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `BLOOM_LOCAL_ROOT` is set, and `BLOOM_PLOTS_DIR` is
  unset
- **THEN** the `experiment_utils.PLOTS_DIR` module-level constant itself equals
  `<BLOOM_LOCAL_ROOT>/plots` (not merely "validation succeeds") — the value every plot tool
  actually writes to via `_viz_shared.save_plot()`

### Requirement: Server Boot Fail-Fast Preserved

The MCP server SHALL fail fast at startup when its runtime environment is missing, via explicit
`validate_env()` calls before `mcp.run()` rather than an import-time side effect, so a
misconfigured deploy fails at container boot before serving requests. The exact set of required
variables is **backend-aware**: on the default (Supabase) backend it includes the Supabase
credentials and the data directories (`BLOOM_*_DIR`, `BLOOM_PLOTS_URL`); in fully-local mode
(`BLOOM_STORAGE_BACKEND=local`) the Supabase credentials are not required and the local input
root is validated instead (see this capability's "Backend-Aware Boot Gate" requirement), while
the data-directory / plots validation runs in both modes — **except** that
when `BLOOM_LOCAL_ROOT` is also set, an unset `BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` /
`BLOOM_PLOTS_DIR` / `BLOOM_PLOTS_URL` is no longer itself a missing-variable failure (see "Lazy
Environment Validation"); boot instead fails fast only if `BLOOM_LOCAL_ROOT` is missing or
unwritable, or if an explicitly-set variable's path does not exist. The `/health` endpoint SHALL
continue to report healthy on a correctly configured boot.

#### Scenario: Misconfigured Supabase-backend deploy fails at boot

- **WHEN** the server starts on the default (Supabase) backend with `SUPABASE_URL` /
  `BLOOM_AGENT_KEY` **or** any `BLOOM_*_DIR` / `BLOOM_PLOTS_URL` variable unset
- **THEN** a validator raises a clear error naming the missing variable before the port is bound
  or requests are served

#### Scenario: Configured server boots healthy

- **WHEN** the server starts with the Supabase environment correctly set
- **THEN** it boots and `/health` returns OK

#### Scenario: Fully-local boot with BLOOM_LOCAL_ROOT needs neither Supabase credentials nor the four directory/URL variables

- **WHEN** the server boots with `BLOOM_STORAGE_BACKEND=local`, `BLOOM_LOCAL_ROOT` set to an
  existing writable directory, `SUPABASE_URL` / `BLOOM_AGENT_KEY` unset, and `BLOOM_TRAITS_DIR` /
  `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` / `BLOOM_PLOTS_URL` all unset
- **THEN** boot succeeds — this is the full 2-variable (`BLOOM_STORAGE_BACKEND` +
  `BLOOM_LOCAL_ROOT`) quick-start from `storage-backends.md`

#### Scenario: Fully-local boot fails fast when BLOOM_LOCAL_ROOT does not exist

- **WHEN** the server starts with `BLOOM_STORAGE_BACKEND=local` and `BLOOM_LOCAL_ROOT` set to a
  path that does not exist
- **THEN** boot fails fast naming `BLOOM_LOCAL_ROOT`, before the port is bound

#### Scenario: Fully-local boot fails fast when BLOOM_LOCAL_ROOT is a file, not a directory

- **WHEN** the server starts with `BLOOM_STORAGE_BACKEND=local` and `BLOOM_LOCAL_ROOT` set to an
  existing path that is a regular file rather than a directory
- **THEN** boot fails fast naming `BLOOM_LOCAL_ROOT`, with a message distinct from the
  does-not-exist case

#### Scenario: Fully-local boot fails fast when BLOOM_LOCAL_ROOT is not writable

- **WHEN** the server starts with `BLOOM_STORAGE_BACKEND=local` and `BLOOM_LOCAL_ROOT` set to an
  existing directory without write permission
- **THEN** boot fails fast naming `BLOOM_LOCAL_ROOT` — this check raises, unlike the legacy
  per-directory check's warn-only behavior for `BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` /
  `BLOOM_PLOTS_DIR` (see design.md Decision 6 of `add-bloommcp-local-root`)

#### Scenario: A BLOOM_LOCAL_ROOT-derived subfolder blocked by a non-directory file fails clearly

- **WHEN** `BLOOM_LOCAL_ROOT` is valid but `<BLOOM_LOCAL_ROOT>/plots` already exists as a regular
  file rather than a directory
- **THEN** boot raises a clear, caller-safe error rather than letting `mkdir`'s raw
  `FileExistsError` propagate

### Requirement: Additive Dependency Set

The `bloommcp` `pyproject.toml` SHALL declare a publication-ready project that adds
`sleap-roots-analyze>=0.1.0a3` and `sleap-roots-contracts[pandas]>=0.1.0a1` (the oracle
+ Phase-2 foundation) while retaining the analysis dependencies still imported directly
by the vendored modules. The `sleap-roots-analyze` floor SHALL be `>=0.1.0a3` so that the
serializable result types (`PCAResult`, `HeritabilityResult`, `KMeansResult`,
`GMMResult`) resolve from the released package for downstream tiers. No dependency that is
still imported by shipped code SHALL be removed. The committed `bloommcp/uv.lock` SHALL
stay in sync with `bloommcp/pyproject.toml`, verified by `scripts/check-uv-locks.py`,
which checks the `langchain`, `bloommcp`, and `services/video-worker` service locks (the
root lock is independent of this package and is not a checked service).

#### Scenario: Build and import succeed in a clean environment

- **WHEN** the package is built with `uv` and imported in a clean environment, and the
  dev group is resolved
- **THEN** the build succeeds, `import bloom_mcp` works, and no runtime dependency is
  missing

#### Scenario: Lockfiles are in sync

- **WHEN** `uv lock --check` runs against the committed `bloommcp/uv.lock`, and
  `scripts/check-uv-locks.py` runs across the `langchain`, `bloommcp`, and
  `services/video-worker` service locks (the root lock is independent and not a checked
  service)
- **THEN** each reports the lockfile in sync with its `pyproject.toml`

#### Scenario: Released analyze result types resolve

- **WHEN** the locked `sleap-roots-analyze` is installed in a clean environment
- **THEN** the resolved version is `>=0.1.0a3` and
  `from sleap_roots_analyze import PCAResult, HeritabilityResult, KMeansResult, GMMResult`
  succeeds

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

### Requirement: Local-Mode Self-Served Plots URL

The system SHALL default `experiment_utils.PLOTS_URL` (resolved once at import time, mirroring
`PLOTS_DIR`'s own `_resolve_plots_dir()` treatment) — when `BLOOM_PLOTS_URL` is unset and the
single `BLOOM_LOCAL_ROOT` variable itself supplies a default (i.e. `_fully_local_root()` is not
`None`) — to `bloom_mcp.storage_backend.self_serve_base_url()` plus `/plots`, instead of the
empty string. This resolver SHALL reuse the existing `_fully_local_root()` gate so that
`is_local_backend()` (and therefore `BLOOM_STORAGE_BACKEND`) is read only when `BLOOM_LOCAL_ROOT`
is itself set, preserving the package's side-effect-free import contract in every deployment that
has not opted into `BLOOM_LOCAL_ROOT`. `bloom_mcp.server.build_app()` SHALL mount
`starlette.staticfiles.StaticFiles` at `/plots`, serving `experiment_utils.PLOTS_DIR`, whenever
`BLOOM_STORAGE_BACKEND=local` (regardless of which tier resolved `PLOTS_DIR`), so a plot URL built
from either the default or an explicit `BLOOM_PLOTS_URL` pointing at this same address actually
resolves standalone. The `/plots` mount is unauthenticated, matching `/health`'s precedent —
there is no analogous `/output` mount; output artifacts surface a direct filesystem path instead
(see `bloommcp-result-store`'s "Per-Output Signed Links And Size At Commit"). Outside the
`BLOOM_LOCAL_ROOT`-derived tier (an explicitly-set `BLOOM_PLOTS_DIR` with
no `BLOOM_LOCAL_ROOT`, or the default Supabase backend), `BLOOM_PLOTS_URL` remains exactly as
required as before this change.

#### Scenario: Unset BLOOM_PLOTS_URL defaults under the BLOOM_LOCAL_ROOT tier

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `BLOOM_LOCAL_ROOT` is set to an existing writable
  directory, and `BLOOM_PLOTS_URL` is unset
- **THEN** `experiment_utils.PLOTS_URL` equals `self_serve_base_url() + "/plots"` (e.g.
  `http://localhost:8811/plots` with no `BLOOMMCP_PUBLIC_URL` set) rather than the empty string

#### Scenario: An explicit BLOOM_PLOTS_URL still wins over the default

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `BLOOM_LOCAL_ROOT` is set, and
  `BLOOM_PLOTS_URL=http://elsewhere:9000/plots` is also set
- **THEN** `experiment_utils.PLOTS_URL` equals `http://elsewhere:9000/plots`, unaffected by
  `self_serve_base_url()`

#### Scenario: The default does not apply outside the BLOOM_LOCAL_ROOT tier

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `BLOOM_LOCAL_ROOT` is unset, `BLOOM_PLOTS_DIR` is set
  explicitly, and `BLOOM_PLOTS_URL` is unset
- **THEN** `experiment_utils.PLOTS_URL` equals the empty string, exactly as before this change —
  the granular explicit-override tier is unaffected

#### Scenario: Import stays side-effect-free when BLOOM_LOCAL_ROOT is unset

- **WHEN** `import bloom_mcp.server` runs in a fresh interpreter with `BLOOM_LOCAL_ROOT` unset
  (regardless of `BLOOM_STORAGE_BACKEND` or `BLOOM_PLOTS_URL`)
- **THEN** the import succeeds without reading `BLOOM_STORAGE_BACKEND` or calling
  `is_local_backend()` — `_resolve_plots_url()`'s `BLOOM_LOCAL_ROOT` gate short-circuits before
  either happens

#### Scenario: The /plots mount is present only in local mode

- **WHEN** `bloom_mcp.server.build_app()` is called with `BLOOM_STORAGE_BACKEND` unset or
  `supabase`
- **THEN** the resulting app has no route mounted at `/plots`

#### Scenario: The /plots mount actually serves a generated plot

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `PLOTS_DIR` resolves to a directory containing a real
  file `histogram_x.png`, and a test client issues `GET /plots/histogram_x.png` against
  `build_app()`'s returned app
- **THEN** the response is `200` with that file's bytes

