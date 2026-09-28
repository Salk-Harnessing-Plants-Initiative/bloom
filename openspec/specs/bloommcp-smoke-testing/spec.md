# bloommcp-smoke-testing Specification

## Purpose
TBD - created by archiving change add-bloommcp-cylinder-smoke-coverage. Update Purpose after archive.
## Requirements
### Requirement: Cylinder Fixture Availability

`bloommcp/tests/fixtures/` SHALL include a cylinder-shaped fixture bundle — raw and
post-QC CSVs plus golden JSONs for qc, outlier-removal, qc_inspect, PCA, and
clustering — sourced independently from the upstream `sleap-roots-analyze#120`/PR #146
`wheat_edpie` bundle, not re-derived from bloommcp's own code under test. Each fixture
entry MUST be documented in `bloommcp/tests/fixtures/README.md`, noting whether it is an
independent oracle or a characterization snapshot, matching the existing turface_19
entries' convention.

#### Scenario: Cylinder fixture files exist and are documented

- **WHEN** a developer inspects `bloommcp/tests/fixtures/`
- **THEN** `cylinder_raw_data.csv`, `cylinder_final_data.csv`, and
  `cylinder_{qc,outlier,qc_inspect,pca,clustering}_golden.json` are present, and
  `README.md` documents each one's provenance (independent oracle vs. characterization
  snapshot)

#### Scenario: Cylinder role columns differ from turface_19

- **WHEN** a tool's column-detection logic (`SAMPLE_ID_PATTERNS` / `GENOTYPE_PATTERNS` /
  `REPLICATE_PATTERNS`) runs against `cylinder_raw_data.csv`
- **THEN** it resolves `plant_qr_code` / `Geno` / `Rep` as the sample/genotype/replicate
  columns, distinct from turface_19's `Barcode` / `geno` / `rep`

#### Scenario: Cylinder goldens are consumed by fast, unmarked tests

- **WHEN** `bloommcp/tests/tools/test_qc_clean_tool.py`, `test_qc_inspect_tool.py`,
  `test_remove_outliers_tool.py`, and `bloommcp/tests/test_oracle.py` are collected
- **THEN** each is parametrized over both `turface_19` and `cylinder`, asserting the
  corresponding cylinder golden JSON exactly as the turface_19 equivalent is asserted
  today — the first three stay fast/fake-backed and unmarked (running in
  `python-audit`'s per-PR sweep), and `test_oracle.py`'s cylinder assertions keep the
  existing `integration` marker

### Requirement: Smoke Script Location

The bloommcp live-stack smoke drivers SHALL live under `bloommcp/tests/smoke/`, not
`bloommcp/scripts/`. The `bloommcp-smoke` and `bloommcp-plot-smoke` Makefile targets
MUST invoke the scripts at their relocated path, and the existing CI gate test MUST
continue to pass without modification.

#### Scenario: Makefile targets invoke relocated scripts

- **WHEN** `make bloommcp-smoke` or `make bloommcp-plot-smoke` runs
- **THEN** it executes `bloommcp/tests/smoke/live_persistence_smoke.py` or
  `bloommcp/tests/smoke/live_plot_tool_smoke.py` respectively, and
  `bloommcp/scripts/` no longer contains either file

#### Scenario: Existing CI gate test is unaffected

- **WHEN** `tests/unit/test_bloommcp_live_smoke_gate.py` runs after the relocation
- **THEN** it still passes, because it asserts on the `make bloommcp-smoke` step's
  presence and its ordering relative to `make migrate-local` in
  `.github/workflows/pr-checks.yml`, not on the script's file path

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

### Requirement: CI vs Pre-Merge Smoke Split

Every test added under the "Granular Tool Smoke Coverage" requirement MUST carry the
`live_smoke` pytest marker (`bloommcp/pyproject.toml`). Tests whose delegate is
numerically ill-conditioned or unreliably slow at cylinder's scale —
`remove_outliers(method="mahalanobis")` on cylinder, `clustering(method="gmm")` on
cylinder, `plot_heritability_bar` and `plot_variance_decomposition` on either fixture,
and `plot_correlation_matrix`, `plot_trait_histograms`, and `plot_trait_boxplots` on
cylinder — MUST additionally carry `live_smoke_slow`. `python-audit`'s per-PR pytest
invocation MUST exclude every `live_smoke`-marked test (`-m "not integration and not
live_smoke"`). The `dev-stack-smoke` CI job MUST run the `live_smoke`-but-not-
`live_smoke_slow` subset. The `/pre-merge` workflow MUST run the full `live_smoke` set
(including the `live_smoke_slow` subset) against a locally-brought-up dev stack.

#### Scenario: python-audit excludes all live-smoke tests

- **WHEN** the `python-audit` CI job runs bloommcp's pytest suite
- **THEN** its invocation includes `-m "not integration and not live_smoke"`, and no
  test carrying `live_smoke` (with or without `live_smoke_slow`) executes, since no dev
  stack is up in that job

#### Scenario: dev-stack-smoke runs only the bounded-time subset

- **WHEN** the `dev-stack-smoke` CI job runs the new granular smoke step
- **THEN** its pytest invocation selects `live_smoke and not live_smoke_slow`, so
  `remove_outliers(method="mahalanobis")` on cylinder, `clustering(method="gmm")` on
  cylinder, `plot_heritability_bar`, `plot_variance_decomposition`, and
  `plot_correlation_matrix`/`plot_trait_histograms`/`plot_trait_boxplots` on cylinder
  do not run there

#### Scenario: pre-merge runs the full live-smoke set

- **WHEN** a developer runs the `/pre-merge` bloommcp step against a locally-running
  dev stack
- **THEN** the full `live_smoke`-marked set executes, including every test also marked
  `live_smoke_slow`

#### Scenario: A regression-guard test enforces the CI filter split

- **WHEN** a future PR edits the `dev-stack-smoke` step's pytest invocation
- **THEN** a `tests/unit/` guard test fails if the step's `-m` filter string no longer
  contains `not live_smoke_slow`

#### Scenario: A regression-guard test enforces live_smoke_slow implies live_smoke

- **WHEN** the `bloommcp/tests/smoke/` suite is collected
- **THEN** a guard test fails if any test carries `live_smoke_slow` without also
  carrying `live_smoke` — otherwise such a test would evade `python-audit`'s
  `not live_smoke` exclusion and run, unmarked and infra-free, in a job with no dev
  stack up

