## ADDED Requirements

### Requirement: No Vendored Analysis or Plotting Code

bloom-mcp SHALL NOT vendor (copy) analysis, plotting, or shared-utility code that exists in
`sleap-roots-analyze`. Every such symbol used by shipped code SHALL be sourced
from `sleap_roots_analyze` (top-level or a submodule path), never from a copy under
`src/bloom_mcp/`. The vendored modules `pca.py`, `clustering.py`, `cluster_visualization.py`,
`outlier_detection.py`, `outlier_visualization.py`, `visualization.py`, `data_cleanup.py`,
`data_utils.py`, and `cross_experiment_correlations.py` SHALL be deleted. Deleting them SHALL
NOT lose functionality: every symbol any shipped module imported from a deleted vendored
module SHALL be shown to exist in `sleap_roots_analyze` (the deleted copies were an older
snapshot of a strict upstream superset), and the surviving tools' external behavior SHALL be
unchanged. The one submodule-only helper `convert_to_json_serializable` SHALL be imported as
`from sleap_roots_analyze.data_utils import convert_to_json_serializable`.

#### Scenario: No shipped module imports a vendored analysis module

- **WHEN** every module under `src/bloom_mcp/**` is scanned for imports whose first dotted
  segment resolves to a deleted vendored analysis/plotting module (`bloom_mcp.pca`,
  `bloom_mcp.clustering`, `bloom_mcp.cluster_visualization`, `bloom_mcp.outlier_detection`,
  `bloom_mcp.outlier_visualization`, `bloom_mcp.visualization`, `bloom_mcp.data_cleanup`,
  `bloom_mcp.data_utils`, `bloom_mcp.cross_experiment_correlations`)
- **THEN** there SHALL be no such import — every analysis/plotting import resolves under
  `sleap_roots_analyze.*`

#### Scenario: Deleted vendored files are gone from the tree

- **WHEN** the package tree is inspected after the change
- **THEN** none of the nine named vendored modules SHALL exist under `src/bloom_mcp/`

#### Scenario: Every deleted symbol is proven present upstream

- **WHEN** each symbol that any shipped module imported from a deleted vendored module is
  checked against the pinned `sleap_roots_analyze`
- **THEN** each SHALL exist upstream by the same name, and the delegation tests SHALL
  exercise the surviving tools through their upstream-backed path

(The dependency consequence of this deletion — pruning `scipy`/`scikit-learn`/`seaborn` from
runtime deps, moving `scikit-learn` to the `[test]` extra, retaining `matplotlib`, and
re-syncing the locks — is owned by the "Necessary-and-Sufficient Declared Dependencies"
requirement below, not restated here.)

## MODIFIED Requirements

### Requirement: Necessary-and-Sufficient Declared Dependencies

Every runtime dependency declared in `bloommcp/pyproject.toml` SHALL be imported by
shipped code (`src/bloom_mcp/**`), and no shipped code SHALL import a dependency that is
not declared. This change completes the **necessary** half of #305 AC5 that #315 deferred
as a documented follow-up when it closed: with the vendored analysis/plotting modules
deleted, `scikit-learn`, `scipy`, and `seaborn` no longer have any shipped importer and
SHALL be removed from runtime `dependencies`. `scikit-learn` SHALL instead be declared in
the `[project.optional-dependencies].test` extra (its only remaining use is
`test_oracle.py`'s UMAP-trustworthiness metric), keeping it out of the production image via
`uv sync --no-dev`. `matplotlib` SHALL be retained as the sole heavy runtime viz dependency
(the surviving `sleap_roots` section plotting tools import it directly). `statsmodels` and
`umap-learn` remain absent (pruned in #315). Committed lockfiles (`bloommcp/uv.lock` + root)
SHALL stay in sync with their `pyproject.toml`. (Runtime resolution is unaffected: the
`sleap-roots-analyze` pin pulls `scikit-learn`/`scipy`/`seaborn` transitively.)

#### Scenario: Pruned dependencies are absent from runtime declarations and shipped imports

- **WHEN** the package is inspected after the vendored modules are deleted
- **THEN** `scikit-learn`, `scipy`, `seaborn`, `statsmodels`, and `umap-learn` SHALL NOT
  appear in `bloommcp/pyproject.toml` runtime `dependencies`
- **AND** no module under `src/bloom_mcp/**` SHALL import `sklearn`, `scipy`, `seaborn`,
  `statsmodels`, or `umap`
- **AND** `scikit-learn` SHALL appear in the `[project.optional-dependencies].test` extra

#### Scenario: Every declared runtime dependency is imported by shipped code

- **WHEN** each declared runtime dependency is checked against shipped imports
- **THEN** each SHALL be imported by at least one `src/bloom_mcp/**` module
- **AND** `matplotlib` SHALL be the only retained heavy viz dependency, traceable to a
  shipped `sleap_roots` section plotting tool that imports it

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

### Requirement: Supabase-Free Test Stack with Cross-Tier Oracle

The package SHALL provide a `bloommcp/tests/` layout using `pytest`, `hypothesis`,
`syrupy`, and the FastMCP `Client`, runnable with fakes and **no live Supabase**, and
this suite SHALL be executed by CI. The `talmolab/sleap-roots-analyze#120` turface_19
fixture and its independently recorded golden values SHALL be committed under
`bloommcp/tests/fixtures/` and asserted — with explicit numeric tolerances, not
auto-generated snapshots — by oracle tests. Because bloom-mcp no longer vendors analysis
code, the oracle's former **shipped-code layer** (which imported `bloom_mcp.pca`,
`bloom_mcp.clustering`, and `bloom_mcp.cross_experiment_correlations` directly) SHALL be
removed: the shipped code now *is* the upstream package, so the PCA shipped-code assertion
folds into the cross-tier layer, and the k-means and cross-experiment-correlation
shipped-code assertions SHALL be **deleted** (their capabilities are dropped by this change —
there is no surviving bloom-mcp tool to run them, and upstream `cross_experiment_analysis`
has a different contract than the deleted vendored copy, so its recorded off-diagonal literal
does not carry). The cross-tier PCA layer (external `sleap_roots_analyze` reproduces the
recorded PCA cumulative variance and component count) SHALL be retained as the upstream
numeric contract, and the delegating UMAP-trustworthiness oracle SHALL continue to exercise
`sleap_roots_analyze.umap` directly. `test_oracle.py` SHALL NOT import any deleted
`bloom_mcp.<vendored>` module.

#### Scenario: Suite collects, runs without Supabase, and is gated by CI

- **WHEN** the CI bloommcp test job runs `uv run pytest` with no live Supabase and
  `SUPABASE_URL` / `BLOOM_AGENT_KEY` unset
- **THEN** the suite collects and the unit tests pass using fakes, and the job fails the
  PR if they do not

#### Scenario: Oracle reproduces independently recorded golden values

- **WHEN** the external `sleap_roots_analyze` functions — reached directly or through the
  migrated MCP tools — run on the committed turface_19 fixture
- **THEN** their outputs match the independently recorded `talmolab/sleap-roots-analyze#120`
  golden values in `bloommcp/tests/fixtures/` within the stated tolerance

#### Scenario: Oracle has no import dependency on deleted vendored modules

- **WHEN** `test_oracle.py` is collected after the vendored modules are deleted
- **THEN** it SHALL collect and run without importing any `bloom_mcp.<vendored>` module —
  its analysis references resolve to `sleap_roots_analyze` (directly or via a tool)
