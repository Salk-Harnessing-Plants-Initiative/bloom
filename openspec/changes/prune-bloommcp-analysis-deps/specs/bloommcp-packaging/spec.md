## ADDED Requirements

### Requirement: Necessary-and-Sufficient Declared Dependencies

Every runtime dependency declared in `bloommcp/pyproject.toml` SHALL be imported by
shipped code (`src/bloom_mcp/**`), and no shipped code SHALL import a dependency that is
not declared. This reconciles #305 AC5 and supersedes the Tier 0 "Additive Dependency
Set" stance once the analysis paths whose deletion frees a dependency are delegated.
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
cross-tier oracle.

#### Scenario: Delegated paths reproduce the golden within tolerance

- **WHEN** the shipped `bloom_mcp` heritability and UMAP paths run on the committed
  turface_19 fixture after delegation
- **THEN** their outputs SHALL match the independently recorded golden values within the
  stated tolerance
- **AND** the same assertion SHALL hold for the external `sleap_roots_analyze` functions
  they delegate to

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
