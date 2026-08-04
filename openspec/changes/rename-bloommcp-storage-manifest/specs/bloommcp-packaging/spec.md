## MODIFIED Requirements

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
- **THEN** `bloom_mcp`, `bloom_mcp.tools`, and `bloom_mcp.manifest` import without error
  and expose the same tool surface as the pre-restructure prototype

#### Scenario: No stale prototype imports remain

- **WHEN** every module under `bloommcp/src/bloom_mcp/` is scanned for import statements
- **THEN** no import has a first dotted segment of `source`, `tools`, or `manifest` —
  every intra-package import resolves under `bloom_mcp.*` (imports of `bloom_mcp.tools`
  / `bloom_mcp.manifest` are not matches)

#### Scenario: Renamed package name is gone

- **WHEN** `import bloom_mcp.storage` is attempted after the `storage/`→`manifest/` rename (#487)
- **THEN** it raises `ModuleNotFoundError` — the old name is not retained as a compatibility
  alias, and callers must import `bloom_mcp.manifest`

#### Scenario: Deleted AnalysisWriter is not importable

- **WHEN** any code attempts `from bloom_mcp.manifest import AnalysisWriter` or
  `from bloom_mcp.manifest.writer import AnalysisWriter` after #487
- **THEN** it raises `ImportError`/`ModuleNotFoundError` — `AnalysisWriter` and the `writer`
  submodule no longer exist anywhere in `bloom_mcp`

### Requirement: CI Gates the Built-Wheel Import

CI SHALL build the `bloommcp` wheel and import it from a clean environment that cannot
see the `bloommcp/src/` tree, so a packaging regression that ships an unimportable wheel
fails the PR. The import SHALL cover `bloom_mcp`, `bloom_mcp.tools`, `bloom_mcp.manifest`,
and `bloom_mcp.server`, SHALL verify the imported package resolves from the installed
wheel and not the source checkout, and SHALL run with no usable Supabase environment
(`SUPABASE_URL` / `BLOOM_AGENT_KEY` empty) so the lazy-validation contract is
load-bearing. CI SHALL retain a regression-guard test asserting the gate's presence and
its load-bearing assertions so it cannot be silently deleted or hollowed out. Built
artifacts (`bloommcp/dist/`) SHALL NOT be committed. (The exact `uv` invocation and the
rationale for omitting `--isolated` live in the proposal and tasks, not this contract.)

#### Scenario: Clean-env wheel import is gated by CI

- **WHEN** the `python-audit` job builds the wheel and imports `bloom_mcp` and its
  `tools`, `manifest`, and `server` submodules in an environment that does not place
  `bloommcp/src/` on the import path
- **THEN** the import resolves the shipped wheel (verified by checking the imported
  package's file location, not the `src/` checkout) and a packaging regression — a
  misconfigured `module-name` / `module-root`, a dropped `__init__.py`, or a wheel that
  ships an empty namespace — fails the job

#### Scenario: Wheel import gate runs with no Supabase env

- **WHEN** the clean-env import runs with `SUPABASE_URL` and `BLOOM_AGENT_KEY` set empty
- **THEN** `import bloom_mcp`, `bloom_mcp.tools`, `bloom_mcp.manifest`, and
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
  bloom_mcp.manifest, bloom_mcp.server"`)
- **THEN** the import SHALL succeed with no missing runtime dependency
- **AND** the resolved `bloom_mcp` SHALL come from the wheel, not the `src/` checkout

#### Scenario: Lockfiles stay in sync after the prune

- **WHEN** `uv lock --check` runs against `bloommcp/uv.lock` and the root lock (and
  `scripts/check-uv-locks.py` runs)
- **THEN** each SHALL report the lockfile in sync with its `pyproject.toml`
