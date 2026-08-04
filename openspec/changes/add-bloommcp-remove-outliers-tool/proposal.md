## Why

bloom-mcp Tier 3 landed the QC foundation (`qc_clean`, #338): a granular tool that turns a
raw experiment into a **clean, no-NaN, analysis-ready** trait table by delegating to
`sleap-roots-analyze`'s tested `clean_traits_for_analysis`, persisted as a versioned `qc`
cleaned run that downstream tools consume via `require_clean=True`. But a clean table can
still carry **outlier samples** — rows that are not NaN yet distort PCA / UMAP / clustering.
Removing them is a distinct **quality** step, and `sleap-roots-analyze` already exposes a
tested entry point for it. There is no MCP tool that surfaces it yet.

This change adds the second granular quality tool, **`remove_outliers`** (#378): sibling to
`qc_clean`, composing **after** it and **before** `pca_analysis` (#308). It detects + trims
outlier samples from a cleaned experiment by **thin delegation** to
`sleap_roots_analyze.remove_outlier_samples` — **no outlier or plotting logic lives in the
MCP** — and persists the trimmed table as a new cleaned version so `qc_clean → remove_outliers
→ pca` composes end-to-end through the existing `require_clean` seam. Optional persisted plots
(default **off**) delegate to `sleap_roots_analyze.plot_outlier_analysis`.

## Dependency is already satisfied (diverges from the issue text)

The issue lists a prerequisite: "analyze `0.1.0a4` release carrying `remove_outlier_samples`
**and** `plot_outlier_analysis`, then a bloom-mcp pin bump to `>=0.1.0a4` + re-lock." **That
bump has already landed** (commit `56dd4b2`, PR #387): `bloommcp/pyproject.toml` pins
`sleap-roots-analyze>=0.1.0a4`, `uv.lock` resolves `0.1.0a4`, and both
`remove_outlier_samples` and `plot_outlier_analysis` import from the installed package. So —
unlike `qc_clean` (#338), whose task 1.1 was a hard pin bump — **this change needs no
dependency change**. The delegate is already importable; this is a pure tool-addition.

## Why outlier removal after QC, before PCA

`clean_traits_for_analysis` guarantees a NaN-free, non-degenerate table — but the detectors
(`detect_outliers_mahalanobis` / `_isolation_forest`) run PCA that silently `dropna()`s and
report indices against the post-`dropna` frame, so they **require** NaN-free input with a
unique index. `qc_clean`'s output satisfies exactly this precondition. Trimming here, on
already-cleaned data, keeps the two QC steps ordered and delegated: `qc_clean` drops bad
*traits/rows*; `remove_outliers` drops distorting *samples*; PCA then runs on a table that is
both clean and outlier-trimmed. The oracle pins this chain: raw turface_19 → `qc_clean` (at its
canonical-default thresholds → 158 samples) → `remove_outliers` (mahalanobis, seed 42) yields a
**characterized** trimmed table (8 samples flagged, 150 retained) whose flagged barcodes match a
recorded golden. (The 158-sample cleaned input is the *canonical-default* clean — distinct from
`turface_19_qc_golden.json`'s 187-sample `max_nans_per_trait=0.1` snapshot; the golden records
its exact cleaning params so the number is self-reproducing, not confused with the qc golden's
naive-dropna figure.)

## What Changes

- **ADD** a granular `remove_outliers` MCP tool: Pydantic input/output models and a tool
  function wrapped by `@as_mcp_tool`, that
  - reads the **cleaned** experiment frame through the injected `ExperimentReader` port with
    **`require_clean=True`** (this tool is a *consumer* of cleaned data — outliers run on the
    clean, NaN-free table), passing the adapter-detected role columns
    (`genotype_col` / `replicate_col` / `sample_id_col`) into the delegate rather than relying
    on the delegate's `geno`/`rep`/`Barcode` defaults;
  - if no cleaned version exists (`require_clean` raises), returns a structured
    `BloomMCPError(assumption_violated, remedy="run qc_clean first")` — the issue's guardrail
    — and persists nothing;
  - delegates **all** detection + removal to
    `sleap_roots_analyze.remove_outlier_samples(clean_df, trait_cols, method=…,
    random_state=…, **detect_kwargs) -> (trimmed_df, report)`. The MCP contains **no** outlier
    detection or removal logic and does **not** call the vendored
    `bloom_mcp.outlier_detection` filters;
  - exposes a **small method surface** — `method="mahalanobis"` (default) + `isolation_forest`
    — with the per-method threshold forwarded via `**detect_kwargs` (`chi2_percentile` for
    mahalanobis, `contamination` for isolation_forest); a threshold set for the wrong method
    is rejected up front as `invalid_input` rather than surfacing the delegate's opaque
    cross-method error;
  - **is stochastic**: it declares `random_state`, so the contract resolves the input `seed`
    (default `42`, for reproducibility + a stable golden) and records the **resolved integer**
    seed in `Provenance` (contrast `qc_clean`, which records `seed=None`);
  - **guards before persisting**: the delegate re-applies analyze's readiness gates
    (≥ `MIN_SAMPLES_FOR_ANALYSIS` survivors, ≥ 1 non-constant trait) and **raises**
    `OutlierRemovalError` (a `ValueError` subclass) when trimming would leave a degenerate frame;
    the tool catches it **in its body** (a body-level `except ValueError`) and raises
    `BloomMCPError(assumption_violated)` with a relax-the-threshold remedy — **not** via the
    contract's `errors=`, which would yield `tool_error`, not `assumption_violated`. As
    defense-in-depth (parity with `qc_clean`) it also runs its own pre-commit no-NaN / row-count
    guard for a delegate that *returns* rather than raises a degenerate frame, and persists
    nothing on any failure;
  - **[Superseded by #420 — see below] persists a versioned run via the `ResultStore` port
    under tool class `qc`** — the trimmed trait CSV written under the shared `CLEANED_CSV_NAME`
    (`_cleaned.csv`) + the outlier `report` (`outlier_report.json`) + provenance — so the
    reader's `_resolve_versioned_cleaned` (which reads the latest `qc`-class `_cleaned.csv`)
    resolves the **trimmed** table as the newest cleaned version and a downstream
    `pca_analysis (require_clean=True)` consumes it. This is the whole composition point and
    reuses `qc_clean`'s exact persistence shape (see design for the "latest-cleaned is
    order-dependent" caveat, inherited from `qc_clean` vs `run_qc_workflow`);
  - returns a **numeric report inline** — `method`, `n_input_samples`, `n_outliers`,
    `n_output_samples`, `removal_fraction`, and the method-dependent
    `threshold_type: Optional[str]` / `threshold_value: Optional[float]` /
    `goodness_of_fit: Optional[dict]` (all three are `None` for isolation_forest), plus the
    flagged `outlier_barcodes` — with `resource_link`s to the persisted trimmed CSV and report;
    never the table inline;
  - **surfaces goodness-of-fit honestly**: on turface_19 the mahalanobis chi-squared
    assumption fits *poorly* (the delegate itself warns), so the tool returns the
    `goodness_of_fit` **dict** inline (its `fit_quality` reads `"very_poor"`) and its description
    guides the agent to consider `isolation_forest` (with an explicit `contamination`) when
    `goodness_of_fit["fit_quality"]` is poor — the flagged set is not silently trusted.
- **ADD optional plots (default `include_plots=False` → fast, report-only).** When
  `include_plots=True`, delegate to `sleap_roots_analyze.plot_outlier_analysis(...) ->
  {name: Figure}` (re-detects with the same seed/params), **persist each returned Figure as a
  run artifact via `ResultStore`** (versioned, in `output_keys`/`output_sha256`), and return
  **`resource_link`s** — not inline blobs, and **not** the legacy `viz_tools._save_plot`
  URL-string shape. The MCP holds **no plotting logic**: with `plots=None` it persists every
  figure the delegate returns for the chosen method; an explicit `plots` list is forwarded as
  the delegate's `which=` and validated against the available figure keys (unknown key →
  `invalid_input`). (The issue's friendly `distance`/`pca_scatter`/`per_genotype` names
  predate the shipped delegate, whose keys are method-specific — e.g.
  `mahalanobis_pc_analysis`, `isolation_forest_analysis`; reconciled in design, an open
  question for review.)
- **REGISTER** the tool in `src/bloom_mcp/server.py` under "Direct tools (granular)" so it
  appears in MCP `tools/list`; add it to the module docstring's tool list.
- **LEAVE** the existing `run_outlier_workflow` and the vendored `bloom_mcp.outlier_detection`
  in place — this **adds granularity alongside**; retirement of `source/*` + the bespoke
  workflow tools stays **deferred to after Stage 1** (Tiers 0–4), per the roadmap.
  `run_outlier_workflow`'s `outlier` class is untouched.

> **Superseded by #420 (`fix-bloommcp-remove-outliers-tool-class`).** This proposal shipped
> (PR #400) persisting `remove_outliers` under the shared `qc` tool class, accepting the
> documented order-dependence caveat below as a known trade-off flagged for review. #420
> implements the "dedicated class" alternative that this proposal's own design.md Decision 1
> flagged as the one open judgment call: `remove_outliers` now persists under its own
> `tool_class="outliers"`, and — **not** a recency comparison (an early draft of #420 tried
> "prefer whichever manifest committed most recently" and found it does not actually fix this
> proposal's own order-dependence caveat, since the reverting `qc_clean` re-run is by
> construction always the more recent commit) — the reader now gives `outliers` **fixed
> priority** over `qc` for `version="latest"` whenever any `outliers` version exists, while
> `remove_outliers`'s own read of its trimming input uses a new, distinct `version="latest_qc"`
> so a fresh `qc_clean` is never hidden from the tool whose job is to trim it. See #420's
> design.md for the full mechanism and its one disclosed trade-off. This proposal is left
> otherwise unedited as the historical record of what actually shipped in PR #400; the
> still-inaccurate scenario in this proposal's own spec delta
> (`specs/bloommcp-remove-outliers-tool/spec.md`) is corrected by that change rather than here,
> since this change is not being re-archived.
- Tests cover the **5 contract patterns + the golden trim through the tool**: golden
  reproduction (flagged barcodes + counts), `tools/list` presence, schema round-trip,
  provenance (resolved integer seed) + links, property/invariant, delegation pinning, the
  structured error envelope (un-cleaned input, degenerate trim, cross-method threshold), and —
  when `include_plots` — the persisted run carries the expected plot `output_keys` + resolvable
  links.
- **EXTEND** the live persistence smoke (`make bloommcp-smoke`) with a `remove_outliers` leg
  driven through the **real** `SupabaseReader` / `SupabaseResultStore`, and **DOCUMENT** local
  validation (a `remove_outliers` leg in `bloommcp/docs/local-validation.md` + a Claude
  dogfood row), matching the pattern `qc_clean` established.

## Impact

- **Affected specs:** `bloommcp-remove-outliers-tool` (new capability). Builds on (does not
  modify) `bloommcp-tool-contract`, `bloommcp-experiment-read`, `bloommcp-result-store`, and
  the `bloommcp-qc-clean-tool` producer.
- **Affected code:**
  - new `bloommcp/src/bloom_mcp/tools/remove_outliers_tool.py` (tool + I/O models +
    `register`);
  - `bloommcp/src/bloom_mcp/server.py` (register the tool; update the module docstring);
  - new `bloommcp/tests/tools/test_remove_outliers_tool.py` (5 patterns + golden + plots);
  - new **outlier golden** `bloommcp/tests/fixtures/turface_19_outlier_golden.json` (the
    raw → clean → trim characterization snapshot: `n_outliers`, `n_output_samples`, sorted
    `outlier_barcodes` at `method=mahalanobis`, `seed=42`), computed from the **existing**
    LF-normalized `turface_19_raw_data.csv` fixture (already vendored by #338 — **no new raw
    fixture needed**) + a `tests/fixtures/README.md` entry;
  - reuse of the shared `CLEANED_CSV_NAME` constant from `experiment_utils.py` (producer +
    consumer agree via it, not a literal);
  - `bloommcp/scripts/live_persistence_smoke.py` + `tests/scripts/`
    `test_live_persistence_smoke_logic.py` — a `remove_outliers` leg + its pure-helper unit
    tests;
  - `bloommcp/docs/local-validation.md` (a `remove_outliers` leg + Claude dogfood row), and the
    smoke-leg enumeration sentences in `bloommcp/README.md` and `DEV_SETUP.md` (which today read
    "drives clustering and `qc_clean`…" and go stale when a third leg lands — updated to add
    `remove_outliers`, or reworded to a non-exhaustive "the granular QC tools" so future legs
    need no doc churn); no change to `bloom_mcp.outlier_detection`'s logic or the
    discovery/workflow tools.
  - **`bloommcp/docs/roadmap.md` is deliberately NOT edited** — like `qc_clean`, `remove_outliers`
    is a granular QC-foundation tool added *alongside* the roadmap tiers (Tiers 0–4:
    PCA/clustering), not a tier itself; the roadmap tier reshape is owned separately, so touching
    it here would risk a conflict.
- **Dependencies:** `sleap_roots_analyze.remove_outlier_samples` +
  `plot_outlier_analysis`, **already available** in the pinned `0.1.0a4` — **no pin change**.
- **Branch/PR:** branches off `origin/staging`; PR targets `staging` (link #378 + the
  roadmap).
