## Why

`remove_outliers`' default `method="mahalanobis"` flags outliers by a chi-squared distance
threshold that is only meaningful when the data satisfy the chi-squared assumption. The tool
already computes and returns a machine-visible `fit_is_trustworthy` flag for exactly this
reason, but the flag is advisory only: on **both** of the project's reference fixtures —
turface_19 (`fit_quality="very_poor"`) and cylinder (`fit_quality="poor"`) — the tool still
trims samples on the untrustworthy threshold and persists them as the new "latest cleaned"
version (tool class `outliers`, per #420) that every `require_clean=True` consumer resolves next
(`pca_analysis`, `umap_analysis`, `clustering`, `descriptive_stats`,
`cross_experiment_correlations`). An agent that doesn't read `fit_is_trustworthy` — the exact
case the flag hedges against — silently makes a bogus-threshold trim canonical.

Filed as [#419](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/419) during
review of #400/#420.

## What Changes

- `remove_outliers` gains a pre-commit gate: when `fit_is_trustworthy is False` (mahalanobis
  fit poor/very_poor/unknown), the tool raises a structured
  `BloomMCPError(code="assumption_violated")` — embedding the already-computed
  `n_input_samples`/`n_outliers`/`n_output_samples`/`goodness_of_fit.fit_quality`/sorted
  `outlier_barcodes` in the message so a caller can still inspect what would have been flagged,
  not just learn that it wasn't persisted — with a remedy naming `method="isolation_forest"` and
  `contamination=0.1` (the delegate's own default, not an invented number) as the starting point.
  **No run and no figures are persisted on this path.** The gate fires before any `plots=`
  validation/figure generation, so a call that combines an untrustworthy fit with an invalid
  `plots` key surfaces the fit gate, not the plot-key error (see design.md Decision 6) —
  the two existing plot-key-validation tests (both built on turface_19, the only fixture either
  currently exercises) move to `method="isolation_forest"` accordingly, since that validation
  logic is method-agnostic.
- The gate never fires when `fit_is_trustworthy` is `None` (`isolation_forest` — no chi-squared
  assumption) or `True` (an acceptable-or-better mahalanobis fit) — behavior is unchanged on
  those paths.
- **BREAKING in effect, not in schema:** a caller invoking `remove_outliers` with mahalanobis
  defaults against data whose fit turns out untrustworthy previously succeeded (with an advisory
  flag); it now raises. This is the corrective behavior the issue asks for. No input/output
  schema field changes.
- The existing "successful default trim" characterization tests/goldens for **both** turface_19
  and cylinder move to `method="isolation_forest"` (mahalanobis defaults now demonstrate the gate
  firing on both fixtures, not a successful persist); new isolation_forest golden values are
  computed against the shipped delegate during implementation, not invented in this proposal.
  This is a larger blast radius than just the two golden tests: at least ~18 currently-passing
  unit tests in `test_remove_outliers_tool.py` invoke mahalanobis defaults against these two
  fixtures (report/round-trip, provenance, versioning/composition, figure-generation, and
  plot-key-validation tests among them) and need per-test triage, not a blanket repoint — see
  `tasks.md` 1.2-1.6 and `design.md`'s Risks section. Because the gate change and this
  repointing are inseparable (landing the gate alone turns ~18 tests red), this ships as a
  single commit — there is no intermediate green state between them.
- Does **not** change the tool's declared default `method` (stays `"mahalanobis"`) and does
  **not** add an opt-in bypass flag — both raised as explicit open questions for reviewer
  sign-off in `design.md` rather than decided here.

## Impact

- Affected specs: `bloommcp-remove-outliers-tool` (ADDED requirement)
- Affected code:
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/remove_outliers.py`
  - `bloommcp/tests/tools/test_remove_outliers_tool.py`
  - `bloommcp/tests/fixtures/turface_19_outlier_golden.json`,
    `bloommcp/tests/fixtures/cylinder_outlier_golden.json` (or new isolation_forest-specific
    golden files — naming decided during implementation)
  - `bloommcp/tests/smoke/test_remove_outliers_smoke.py`
  - `bloommcp/tests/smoke/live_persistence_smoke.py` and its wrapper
    `bloommcp/tests/scripts/test_live_persistence_smoke_logic.py` — the actual `make
    bloommcp-smoke` driver (wired into CI at `.github/workflows/pr-checks.yml`), distinct from
    the unit-style smoke test above; it runs `remove_outliers(method="mahalanobis", seed=42)`
    against real Supabase-backed turface_19 data and gates downstream clustering/descriptive-
    stats legs on the trim persisting
  - `bloommcp/docs/local-validation.md` ("Leg 2" runbook prose, which walks through the same
    live mahalanobis-default call succeeding)
  - `openspec/changes/add-bloommcp-remove-outliers-tool/specs/bloommcp-remove-outliers-tool/spec.md`
    (pointer note only, mirroring how #420 handled the same still-unarchived-capability
    situation — scoped to the "Reproduces the Golden Trim Through the Tool" requirement
    specifically, since the "Guarantees a Non-Degenerate..." requirement's guarantee is
    unaffected)
