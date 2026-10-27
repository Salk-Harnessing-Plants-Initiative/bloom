## MODIFIED Requirements

### Requirement: Granular Tool Smoke Coverage

`bloommcp/tests/smoke/` SHALL contain a smoke test for each of `qc_clean`, `qc_inspect`,
`remove_outliers`, `pca_analysis`, `clustering` (kmeans, gmm, hierarchical), and the 5
plotting tools (`plot_trait_histograms`, `plot_trait_boxplots`, `plot_correlation_matrix`,
`plot_heritability_bar`, `plot_variance_decomposition`) that exercises the tool against
both the `turface_19` and `cylinder` fixtures through a real call into the running dev
stack (network/MCP-transport call, not an in-process or mocked call).

Two mutually exclusive experiment-identification harnesses exist in `conftest.py`, and each
smoke test SHALL use the one matching its tool's read path: the `seeded_experiment` fixture
(a filename copied into the local, bind-mounted `BLOOM_TRAITS_DIR`) for a tool that still
calls `experiment_utils.load_experiment_data` directly, and the `db_experiment_id` fixture
(a numeric id already seeded in Postgres) for a tool that reads via the `ExperimentReader`
port's DB-only `SupabaseReader` raw tier. `plot_trait_histograms`, `plot_trait_boxplots`, and
`plot_correlation_matrix` moved from the former to the latter group when they converged onto
`@as_mcp_tool` (#466) — `seeded_experiment` now covers only `plot_heritability_bar` and
`plot_variance_decomposition` (the 2 tools not yet converged), and `db_experiment_id` covers
every other granular tool, including these 3.

#### Scenario: Every tool has smoke coverage on both fixtures

- **WHEN** the smoke suite in `bloommcp/tests/smoke/` is collected
- **THEN** each of the 10 tool surfaces above has at least one test parametrized (or
  duplicated) over both `turface_19` and `cylinder`, calling the tool through the real
  running dev stack rather than a mock

#### Scenario: A converged tool's smoke test uses the DB-only harness

- **WHEN** a smoke test targets `plot_trait_histograms`, `plot_trait_boxplots`, or
  `plot_correlation_matrix` (or any of the other 7 tools already on `@as_mcp_tool`)
- **THEN** it obtains its experiment identifier from the `db_experiment_id` fixture, not
  `seeded_experiment` — the tool's read path resolves a DB-registered experiment, not an
  arbitrary local CSV filename

#### Scenario: A not-yet-converged plotting tool keeps the legacy harness

- **WHEN** a smoke test targets `plot_heritability_bar` or `plot_variance_decomposition`
  (retiring into `heritability_analysis` per #462, not yet converged)
- **THEN** it obtains its experiment identifier from the `seeded_experiment` fixture, matching
  that tool's still-direct `experiment_utils.load_experiment_data` read path
