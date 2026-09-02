## MODIFIED Requirements

### Requirement: Granular Tool Smoke Coverage

`bloommcp/tests/smoke/` SHALL contain a smoke test for each of `qc_clean`, `qc_inspect`,
`remove_outliers`, `pca_analysis`, `clustering` (kmeans, gmm, hierarchical), `umap_analysis`,
`descriptive_stats`, `cross_experiment_correlations`, `heritability_analysis`, and the 3
surviving plotting tools (`plot_trait_histograms`, `plot_trait_boxplots`,
`plot_correlation_matrix`) that exercises the tool against both the `turface_19` and `cylinder`
fixtures through a real call into the running dev stack (network/MCP-transport call, not an
in-process or mocked call).

The retired `plot_heritability_bar` and `plot_variance_decomposition` smoke tests are removed
along with the tools themselves; `heritability_analysis` inherits their coverage, exercising both
the numeric result and — via `include_plots=true` — both rendered figures in a single call. Being
a `ResultStore`-persisting granular consumer rather than a `PLOTS_DIR` writer, its smoke SHALL
resolve its input through the DB-backed experiment-id fixture the other granular tools use, not
the filename-based fixture the retired plot tools used.

(The roster above also names `umap_analysis`, `descriptive_stats`, and
`cross_experiment_correlations`, each of which already has a smoke module. Their omission from the
previous wording was pre-existing drift, corrected here rather than propagated.)

#### Scenario: Every tool has smoke coverage on both fixtures

- **WHEN** the smoke suite in `bloommcp/tests/smoke/` is collected
- **THEN** each of the 12 tool surfaces above has at least one test parametrized (or duplicated)
  over both `turface_19` and `cylinder`, calling the tool through the real running dev stack
  rather than a mock

#### Scenario: The retired plot tools have no smoke tests

- **WHEN** the smoke suite is collected
- **THEN** no test invokes `sleap_roots_plot_heritability_bar` or
  `sleap_roots_plot_variance_decomposition`, and neither smoke module exists

### Requirement: CI vs Pre-Merge Smoke Split

Every test added under the "Granular Tool Smoke Coverage" requirement MUST carry the
`live_smoke` pytest marker (`bloommcp/pyproject.toml`). Tests whose delegate is
numerically ill-conditioned or unreliably slow at cylinder's scale —
`remove_outliers(method="mahalanobis")` on cylinder, `clustering(method="gmm")` on
cylinder, and `plot_correlation_matrix`, `plot_trait_histograms`, and
`plot_trait_boxplots` on cylinder — MUST additionally carry `live_smoke_slow`.
`python-audit`'s per-PR pytest invocation MUST exclude every `live_smoke`-marked test
(`-m "not integration and not live_smoke"`). The `dev-stack-smoke` CI job MUST run the
`live_smoke`-but-not-`live_smoke_slow` subset. The `/pre-merge` workflow MUST run the full
`live_smoke` set (including the `live_smoke_slow` subset) against a locally-brought-up dev stack.

`heritability_analysis` SHALL carry `live_smoke` **only**, on both fixtures — deliberately unlike
its two retired predecessors, which were `live_smoke_slow`. Those read whole trait CSVs from
`TRAITS_DIR` (up to 846 traits at cylinder scale); `heritability_analysis` reads the DB-seeded
smoke experiments, whose largest shape is an order of magnitude smaller, so the per-trait mixed
model that motivated the slow marking no longer dominates. Marking it slow would exclude it from
`python-audit` (no dev stack) **and** from `dev-stack-smoke`, leaving a newly added,
tool-surface-breaking change with no per-PR live-stack signal at all — the opposite of the
coverage its predecessors provided.

#### Scenario: python-audit excludes all live-smoke tests

- **WHEN** the `python-audit` CI job runs bloommcp's pytest suite
- **THEN** its invocation includes `-m "not integration and not live_smoke"`, and no
  test carrying `live_smoke` (with or without `live_smoke_slow`) executes, since no dev
  stack is up in that job

#### Scenario: dev-stack-smoke runs only the bounded-time subset

- **WHEN** the `dev-stack-smoke` CI job runs the granular smoke step
- **THEN** its pytest invocation selects `live_smoke and not live_smoke_slow`, so
  `remove_outliers(method="mahalanobis")` on cylinder, `clustering(method="gmm")` on
  cylinder, and `plot_correlation_matrix`/`plot_trait_histograms`/`plot_trait_boxplots`
  on cylinder do not run there

#### Scenario: heritability_analysis runs in the per-PR smoke job

- **WHEN** the `dev-stack-smoke` CI job runs the granular smoke step
- **THEN** the `heritability_analysis` smoke test executes on both fixtures, because it carries
  `live_smoke` without `live_smoke_slow`

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
