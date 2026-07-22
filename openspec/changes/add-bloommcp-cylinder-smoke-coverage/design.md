## Context

Issue #483 bundles four asks that share one underlying problem — bloommcp's live-stack
smoke coverage is thin (2 scripts, 1 fixture, 2 of 10 tool/plot surfaces) and untracked by
any marker, so there's no principled boundary between "runs on every PR" and "needs a
human to bring up the stack locally." This design covers the two places the issue text
is ambiguous or incomplete once checked against the current `staging` tree.

## Goals / Non-Goals

- Goals: cylinder fixture with documented provenance; both smoke scripts relocated;
  granular per-tool-per-fixture smoke coverage; a marker split that keeps `python-audit`
  infra-free while still running the bounded-time subset somewhere in CI.
- Non-Goals: touching `test_oracle.py`'s existing `integration`-marked characterization
  snapshots; provisioning a new CI job; changing the `dev-stack-smoke` job's existing
  teardown/debug-dump behavior.

## Decisions

### Decision 1: two markers (`live_smoke` + `live_smoke_slow`), not one

Issue #483 proposes a single `live_smoke` marker, "excluded from all CI; run via
`/pre-merge` only" — but its own §4 also asks for a "safe for CI" subset
(`qc_clean`, `qc_inspect`, `pca_analysis`, `clustering` kmeans/hierarchical,
`plot_trait_histograms`, `plot_trait_boxplots`) that must still run *somewhere* in CI, since
otherwise "safe for CI" is meaningless. The only CI job with the live dev stack up is
`dev-stack-smoke` — `python-audit` never brings up Supabase/MinIO, so it cannot run *any*
`live_smoke`-marked test regardless of numerical risk.

Reconciling both: every new granular smoke test gets `@pytest.mark.live_smoke` (the
"needs a live stack" fact, used only to exclude the whole set from `python-audit`).
The numerically-risky subset *additionally* gets `@pytest.mark.live_smoke_slow`:

- `remove_outliers(method="mahalanobis")` on cylinder — 129 samples × 649–880 traits
  inverts turface_19's 187×18 samples≫traits ratio; the trait-covariance matrix is
  severely rank-deficient (turface_19's own mahalanobis fit is already
  `goodness_of_fit_fit_quality == "very_poor"` per `turface_19_outlier_golden.json`, and
  cylinder is a worse-conditioned version of the same problem).
- `clustering(method="gmm")` on cylinder — `covariance_type="full"` estimates one full
  covariance matrix per component; ~649 traits vs. 123 samples is wildly underdetermined,
  prone to EM non-convergence.
- `plot_heritability_bar` **and** `plot_variance_decomposition`, both fixtures, especially
  cylinder — both delegate to `calculate_heritability_estimates`, which fits a
  `statsmodels.MixedLM` **per trait** (`plot_heritability_bar.py:41`,
  `plot_variance_decomposition.py:44`) — the same computation family already flagged
  CI-flaky for `test_oracle.py`'s `integration`-marked tests, but now up to ~880 sequential
  fits instead of ~18. Issue #483 never mentions `plot_variance_decomposition`; it shares
  its delegate with `plot_heritability_bar` line-for-line, so it gets the identical
  classification.
- `plot_correlation_matrix` on cylinder — an 880×880 correlation matrix + heatmap render;
  not numerically unstable, included for the same "meaningfully more wall-clock work"
  reasoning the issue gives.
- `plot_trait_histograms` and `plot_trait_boxplots` on cylinder — **added after the
  first real `dev-stack-smoke` run on this change's own PR** (#507 review): both
  delegates (`create_trait_histograms`, `create_trait_boxplots_by_genotype`) have no
  pagination and render all 846 traits into a single figure. This was originally
  classified CI-safe ("matplotlib rendering over already-computed values" — see the
  now-corrected list above), which held for turface_19's ~18-20 traits but not for
  cylinder: observed wall-clock time was variable enough (histograms 46-86s, boxplots
  109s locally vs. a >120s timeout in CI) to sit right at, and sometimes past, the
  120s client timeout in `tests/smoke/conftest.py` — not "bounded time" in the sense
  CI needs. Same root cause as the already-correctly-flagged
  `plot_heritability_bar`/`plot_variance_decomposition` risk (per-trait fan-out with
  no pagination at this scale), just missed here because rendering-only tools looked
  categorically cheaper than statistical model-fitting ones.

`dev-stack-smoke` runs `pytest tests/smoke/ -m "live_smoke and not live_smoke_slow"`
(bounded-time, both fixtures). `/pre-merge` runs the full `pytest tests/smoke/ -m
live_smoke` (superset — includes the slow tests) against a stack the developer has
already brought up locally, mirroring the existing `pytest tests/ -m integration`
pre-merge step. `python-audit`'s `-m "not integration and not live_smoke"` excludes both
tiers in one filter — `live_smoke_slow` tests all carry `live_smoke` too, so no third
term is needed.

Alternative considered: a single `live_smoke` marker plus explicit `--deselect
<node-id>` list in the `dev-stack-smoke` step. Rejected — deselect-by-node-id silently
stops excluding anything the moment a test is renamed or reparametrized, whereas a second
marker fails loudly (an unmarked-but-slow test simply runs where it shouldn't, immediately
visible in CI timing/flake reports rather than silently never running at all).

### Decision 2: fixture naming mirrors turface_19's, not the upstream cylinder paths

Upstream cylinder source paths (`traits_11DAG_cleaned_qc_scanner_independent.csv`,
`cylinder_final_data.csv`) partially match and partially don't. To keep
`bloommcp/tests/fixtures/` internally consistent (and because `live_plot_tool_smoke.py`
already hard-codes a `_raw_data.csv` / `_final_data.csv` naming pattern for turface_19),
this change uses `cylinder_raw_data.csv` / `cylinder_final_data.csv` +
`cylinder_{qc,outlier,qc_inspect,pca,clustering}_golden.json`, matching the
`turface_19_*` filenames one-for-one. Each gets the same independent-oracle vs.
characterization-snapshot provenance note the turface_19 README entries already carry
per-key (PCA's cumulative variance is upstream-independent per `viz_pca_metadata.json`;
heritability, UMAP-if-added, and clustering are re-derived characterization snapshots —
see `bloommcp/tests/fixtures/README.md`'s existing per-key caveats for the pattern to
follow).

### Decision 3: cylinder goldens get dual consumption, not just existence

Porting the 5 cylinder golden JSONs (Decision 2's naming) without wiring them into a
consumer would commit dead fixture data. Two tiers of consumption already exist for
turface_19, and cylinder mirrors both:

- **Fast, unmarked contract tests** (`test_qc_clean_tool.py`, `test_qc_inspect_tool.py`,
  `test_remove_outliers_tool.py`) assert the tool's business logic against the golden's
  characterization values, fake-backed, no live stack — these run in `python-audit`'s
  per-PR sweep today for turface_19 and gain a `cylinder` parametrization.
- **`test_oracle.py`** asserts the PCA/clustering characterization snapshots at full
  statsmodels/umap fidelity — already `integration`-marked (slow, no live infra) — gains
  the same cylinder parametrization, preserving its existing marker.

Neither tier is redundant with the new `live_smoke` granular smoke tests (this
proposal's core ask): the fast/integration tiers assert the tool's *numeric output*
against a golden; the smoke tests assert the *real dev-stack round-trip* (network/MCP
transport, bind-mounted volumes, real Supabase/MinIO) regardless of the numeric result.
A regression in bloommcp's business logic should fail the fast tier; a regression in
the container/transport/storage wiring should fail the smoke tier.

## Risks / Trade-offs

- Two markers add one more thing a future contributor must remember when adding a smoke
  test (mark `live_smoke`, and additionally `live_smoke_slow` if it inverts
  samples≪traits or does per-trait `MixedLM` fits) → mitigated by two regression-guard
  unit tests: one (parallel to `test_bloommcp_live_smoke_gate.py`) asserting the
  `dev-stack-smoke` step's `-m` filter string contains `not live_smoke_slow`; a second
  asserting every test carrying `live_smoke_slow` also carries `live_smoke` (closes the
  gap the first guard alone leaves — a `live_smoke_slow`-only test has no `live_smoke`
  marker for `python-audit`'s `not live_smoke` clause to exclude, so it would otherwise
  run, unmarked and infra-free, in a job with no dev stack up). Both fail loudly instead
  of silently running slow or unmarked tests on every PR.
- Cylinder's 129×880 raw / 123×649 post-QC shape is committed as test data — larger than
  turface_19's 187×20, but still small enough (\<1 MB) to commit directly like turface_19,
  no LFS/external-storage decision needed.
- `dev-stack-smoke`'s current runtime (~6-7 minutes as of this writing) leaves headroom
  under its 20-minute timeout for the new CI-safe smoke step, but the proposal does not
  benchmark the new step itself — re-check actual job duration on the first CI run this
  change lands in, and raise the timeout if margin gets thin.
- **Materialized**: the first real `dev-stack-smoke` run on this PR failed —
  `plot_trait_boxplots[cylinder]` exceeded the 120s client timeout, and
  `plot_trait_histograms[cylinder]` passed but with a tight margin (observed 46-86s
  across separate local runs). Both were reclassified `live_smoke_slow` (Decision 1)
  rather than tuning the timeout, since the reviewer-suggested fix — move the
  genuinely-variable-cost case to the pre-merge-only tier — is more consistent with
  this proposal's own risk model than papering over CI-observed variance with a
  bigger number.

## Migration Plan

1. Add cylinder fixtures + README documentation, and in the *same* changeset wire them
   into the existing fast per-tool contract tests and `test_oracle.py` (Decision 3) — a
   golden JSON is never committed without its consumer landing alongside it.
2. Relocate the two existing scripts as one atomic commit: `git mv` both, repoint the
   Makefile targets, fix `test_live_persistence_smoke_logic.py`'s hardcoded driver path,
   fix `local-validation.md`'s stale link, and confirm
   `test_bloommcp_live_smoke_gate.py` and existing CI steps still pass unmodified. None
   of these five changes is safe to split into its own commit — each alone leaves CI (or
   local docs) broken until the rest land.
3. Declare the `live_smoke` / `live_smoke_slow` markers and narrow `python-audit`'s
   exclusion filter to `not integration and not live_smoke` *before or alongside* the
   first new smoke test module — every test added in this step is self-marked at
   creation, never introduced unmarked-and-uncovered-by-the-filter even for one commit.
   (This reorders the previous plan's steps 3-5: the filter narrows early, tied to test
   creation, not last.)
4. Wire the CI-safe subset into `dev-stack-smoke` and the full set into `/pre-merge`; add
   both regression-guard tests. Land this as its own commit, separate from step 3 — the
   `python-audit` filter narrowing is protective and should stay independently
   revertible from this step's new `dev-stack-smoke` job step, which carries the actual
   flake/timeout risk.

## Open Questions

- Exact golden-JSON key shapes for cylinder (which fields are independent-oracle vs.
  characterization-snapshot) depend on what the upstream `expected/qc/cylinder/*` and
  `expected/viz/cylinder/viz_pca_metadata.json` bundle actually contains once ported —
  finalized during `tasks.md` execution, not this proposal.
