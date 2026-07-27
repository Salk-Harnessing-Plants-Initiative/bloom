## Why

Every fixture in `bloommcp/tests/fixtures/` is `turface_19` only, so no bloommcp tool
has ever been exercised against a real-world naming convention other than
`Barcode`/`geno`/`rep`, or against a trait table wider than it is tall. The upstream
`sleap-roots-analyze#120`/PR #146 bundle (`tests/fixtures/real/wheat_edpie/`) already
has an unused cylinder arm — `plant_qr_code`/`Geno`/`Rep` role columns, `scan_id` /
`plant_id` / `plant_name` / `species_name` / `wave_number` as separate columns, and
129 samples × 880 columns (post-QC: 123 × 649) — a genuinely different shape that
inverts turface_19's samples-vs-traits ratio.

Separately, two smoke scripts live in `bloommcp/scripts/` instead of `bloommcp/tests/`,
and only two tools (`qc_clean`+`remove_outliers` via `live_persistence_smoke.py`,
`plot_trait_histograms` via `live_plot_tool_smoke.py`) have live-stack smoke coverage —
`qc_inspect`, `pca_analysis`, `clustering`, and 4 of the 5 plotting tools have none.
bloommcp's `pyproject.toml` has a single `integration` marker (full-fixture
statsmodels/umap oracle tests — slow computation, no live infra needed); nothing marks
"needs the real running dev stack," so there's no principled way to keep new live-stack
smoke tests out of `python-audit`'s per-PR `pytest tests/ -m "not integration"` run
(which has no dev stack up).

Refs issue #483. **Note on issue staleness**: #483's text says `live_plot_tool_smoke.py`
is "not yet wired into any CI job" (open PR #473 at time of writing). PR #473 merged
as part of #472's fix and is confirmed present as a step in `dev-stack-smoke`
(`.github/workflows/pr-checks.yml`) on `staging` today — that half of ask #2 is
already done. The relocation half (both scripts still live in `bloommcp/scripts/`) is
still open, as is all of asks #1, #3, and #4.

## What Changes

- **ADD** a cylinder fixture bundle to `bloommcp/tests/fixtures/` — `cylinder_raw_data.csv`,
  `cylinder_final_data.csv`, and golden JSONs for qc, outlier-removal, qc_inspect, PCA, and
  clustering — sourced independently from the upstream wheat_edpie bundle, documented in
  `bloommcp/tests/fixtures/README.md` following the existing turface_19 provenance
  convention (independent-oracle vs. characterization-snapshot called out per key, as
  turface_19's entries already do). **CONSUME** each golden by parametrizing the existing
  fast, unmarked per-tool contract tests (`test_qc_clean_tool.py`, `test_qc_inspect_tool.py`,
  `test_remove_outliers_tool.py`) and `test_oracle.py`'s PCA/clustering characterization
  assertions over `{turface_19, cylinder}`, matching exactly how each already consumes its
  turface_19 counterpart — otherwise the cylinder goldens are committed data with no
  regression protection.
- **MOVE** `bloommcp/scripts/live_persistence_smoke.py` and
  `bloommcp/scripts/live_plot_tool_smoke.py` into `bloommcp/tests/smoke/`, repointing the
  `bloommcp-smoke` / `bloommcp-plot-smoke` Makefile targets' script paths. No marker,
  behavior, or CI-job change for either — `tests/unit/test_bloommcp_live_smoke_gate.py`
  asserts on the `make` invocation and step order, not the script's path, so it needs no
  changes.
- **ADD** new smoke tests under `bloommcp/tests/smoke/` — one per tool
  (`qc_clean`, `qc_inspect`, `remove_outliers`, `pca_analysis`, `clustering` ×
  kmeans/gmm/hierarchical, and all 5 plotting tools) × both fixtures (turface_19,
  cylinder) — each a real network/port call against the running dev stack (matching
  `live_plot_tool_smoke.py`'s real-call approach), not an in-process/mocked call.
- **ADD** a `live_smoke` pytest marker ("needs the real running dev stack; excluded from
  `python-audit`'s per-PR run") plus a `live_smoke_slow` sub-marker for the
  numerically-risky subset (see design.md Decision 1 for why two markers, not one).
  Update `python-audit`'s pytest invocation to `-m "not integration and not live_smoke"`;
  add a `dev-stack-smoke` step running the CI-safe subset
  (`-m "live_smoke and not live_smoke_slow"`); add a `/pre-merge` step running the full
  `-m live_smoke` set (superset, includes the slow tests) against a locally-brought-up
  stack.
- **CLASSIFY** `plot_variance_decomposition` as `live_smoke_slow` alongside
  `plot_heritability_bar` — issue #483 never analyzes this 5th plotting tool, but it
  delegates to the same `calculate_heritability_estimates` per-trait `statsmodels.MixedLM`
  fit (`plot_variance_decomposition.py:44`, matching `plot_heritability_bar.py:41`), so it
  carries the identical CI-flakiness risk the `integration` marker already exists to
  contain for the oracle tests.

## Impact

- **Affected specs**: new capability `bloommcp-smoke-testing` (ADDED only).
- **Affected code**:
  - `bloommcp/tests/fixtures/` — cylinder CSVs + golden JSONs (new); `README.md` (new
    section).
  - `bloommcp/tests/tools/test_qc_clean_tool.py`, `test_qc_inspect_tool.py`,
    `test_remove_outliers_tool.py` — parametrized over cylinder (consumes the new
    goldens; fast, unmarked, stays in `python-audit`'s per-PR sweep).
  - `bloommcp/tests/test_oracle.py` — PCA/clustering characterization assertions
    parametrized over cylinder (stays `integration`-marked).
  - `bloommcp/tests/smoke/` — new directory; relocated `live_persistence_smoke.py` +
    `live_plot_tool_smoke.py`; new `conftest.py`; new per-tool smoke test modules.
  - `bloommcp/scripts/` — the two files above removed from here.
  - `bloommcp/tests/scripts/test_live_persistence_smoke_logic.py` — hardcoded
    `_DRIVER_PATH` repointed to the relocated script (module-level import; breaks
    `python-audit` at collection time if not updated in the same commit as the move).
  - `bloommcp/docs/local-validation.md` — stale relative link to the pre-relocation
    script path fixed.
  - `Makefile` — `bloommcp-smoke` / `bloommcp-plot-smoke` targets' script paths updated.
  - `bloommcp/pyproject.toml` — `live_smoke` + `live_smoke_slow` markers added.
  - `.github/workflows/pr-checks.yml` — `python-audit`'s pytest `-m` filter updated; a new
    step added to `dev-stack-smoke` for the CI-safe smoke subset.
  - `.claude/commands/pre-merge.md` — a new step for the full `live_smoke` set, plus a
    drive-by fix adding the existing-but-undocumented `make bloommcp-plot-smoke` to
    Step 4b.
  - `tests/unit/` (repo root) — two new regression-guard tests: one asserting
    `dev-stack-smoke`'s `-m` filter excludes `live_smoke_slow`, one asserting every
    `live_smoke_slow`-marked test also carries `live_smoke`.
- **Affected CI**: no new job. `dev-stack-smoke` gains one more step (bounded-time subset,
  both fixtures); `python-audit`'s exclusion filter widens; nothing changes about the
  existing `integration` marker or `test_oracle.py`.

## Non-goals

- Removing or restructuring the existing `dev-stack-smoke` job, its gate test, or the
  `integration` marker — all stay as-is.
- Reconciling `test_oracle.py`'s existing heritability/UMAP characterization caveats — out
  of scope here (tracked separately per `bloommcp/tests/fixtures/README.md`).
- Adding a cylinder-equivalent of every turface_19 doc caveat verbatim — provenance notes
  are drafted fresh for cylinder's actual source keys, not copy-pasted.
