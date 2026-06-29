## Why

`qc_clean` (#338 / #356) is **agent-driven**, and its cleanup thresholds are
**data-dependent**, not one-size-fits-all. Today the agent has no way to *see* the
missingness pattern or know which trait crosses which threshold, so it runs `qc_clean` on the
canonical defaults (`max_nans_per_trait = 0.2`, `max_nans_per_sample = 0.0`, …). On the raw
`turface_19` fixture that is the wrong
call: the two NaN-heavy traits (`Root_Biomass_mg`, `Root_Shoot_Ratio`) sit at **15.5 % NaN**,
*under* the canonical `max_nans_per_trait = 0.2` default, so they are **kept** — and because
the canonical `max_nans_per_sample = 0.0` then drops any sample with a residual NaN, keeping
those traits silently discards their **29 samples** (187 → 158). That is the exact
uncontrolled sample loss the QC tier exists to prevent. **The fix is not to change the
defaults** — it is to give the agent a read-only feedback loop so it can pick a good threshold
(here `≤ 0.15`, which drops the two offending traits and loses **zero** samples) *before*
committing a `qc_clean` run.

This adds `qc_inspect`, a Tier-3 **read-only** sibling to `qc_clean`: it visualizes trait
**missingness / NaN structure at QC time** and returns a structured **threshold
recommendation**, so the agent runs `qc_clean` with eyes open instead of blind on defaults.

## What to wrap (already exists upstream — public; analyze's own EDA pipeline uses it)

`sleap-roots-analyze` (`>=0.1.0a3`, **already pinned** by #356 — no new dependency) ships the
threshold-aware EDA visualization its `exploratory_analysis` pipeline step uses. This is a
**wrap, not an upstream addition** (all four are in `sleap_roots_analyze.__all__`, verified on
`0.1.0a3`):

- **`create_trait_eda_plots(df, trait_cols, thresholds, cleanup_log, min_samples_per_trait)`**
  — *primary*. Per-trait **NaN / zero / outlier fractions** as bar charts with the cleanup
  **threshold lines drawn**, plus the "traits actually removed" panel fed by the
  `apply_data_cleanup_filters` cleanup log.
- **`apply_data_cleanup_filters(df, trait_cols, …thresholds…, role_cols)`** — produces the
  `(filtered_df, cleanup_log)` the overlay's `traits_actually_removed` and the recommendation
  are computed from (exactly how analyze's own EDA step feeds the plots — it is **not** the
  vendored `bloom_mcp.data_cleanup`).
- **`create_exploratory_summary_plots(...)["missing_data_pattern"]`** — the sample × trait
  **missingness heatmap** (`sns.heatmap(df[trait_cols].isna().T)`).
- **`inspect_nan_samples(df, trait_cols, role_cols)`** — tabular per-sample report (which
  samples, which traits, `nan_fraction`).

## What Changes

- **ADD** a granular, **read-only** `qc_inspect` MCP tool: Pydantic input/output models, a
  tool function wrapped by `@as_mcp_tool`, that
  - reads the **raw** experiment frame through the injected `ExperimentReader` port (no
    `require_clean` — it inspects the *raw* missingness), forwarding the adapter-detected role
    columns (`genotype_col` / `replicate_col` / `sample_id_col`) into the delegates the same
    way `qc_clean` does (the shared `_role_kwargs` helper);
  - accepts the **same threshold params as `qc_clean`** (`max_zeros_per_trait`,
    `max_nans_per_trait`, `max_nans_per_sample`, `min_samples_per_trait`, optional
    `trait_columns`) so the threshold overlays and the recommendation reflect what a subsequent
    `qc_clean` would apply — runs `apply_data_cleanup_filters` to get the `cleanup_log`, then
    feeds it to `create_trait_eda_plots`;
  - delegates **all** plotting / NaN-inspection to the three analyze functions above and
    contains **no EDA or plotting logic of its own** — and, like `qc_clean`, **never** calls
    the vendored `bloom_mcp.data_cleanup`;
  - validates a caller-supplied `trait_columns` subset up front (existence + numeric) →
    `invalid_input` naming the bad columns (the shared `_validate_trait_subset` helper),
    rather than an opaque `KeyError` / `internal_error`;
  - returns a small inline summary **plus a structured `recommendation`**: per-trait NaN
    fractions, `traits_exceeding_thresholds` (at the supplied params), `traits_would_be_removed`
    (from the cleanup log), and a `recommended_max_nans_per_trait` with its rationale
    (`would_remove_traits`, `samples_lost_at_recommendation`, `samples_lost_naive_dropna`,
    `residual_nan_cells_at_current_params`) — the agent reads this to pick a threshold;
  - is wrapped by `@as_mcp_tool` (Pydantic I/O, structured `BloomMCPError`, one `Provenance`
    per call); QC inspection is deterministic, so it declares `provenance` but **not**
    `random_state` and records `seed = None`.
- **PERSIST a versioned *report* run via the `ResultStore` port** under a **distinct tool
  class `qc_inspect`** (deliberately **not** `qc`): the EDA figures (PNG) +
  `inspect_nan_samples` CSV + a `recommendation.json`, returned as **links** (`run_ref`,
  `manifest_path`, object keys) — **never inline blobs / base64 figures**, mirroring
  `qc_clean`'s reproducibility contract. (See the design's persist-vs-transient decision.)
- **READ-ONLY guarantee:** `qc_inspect` writes a *report* artifact set and **does not** write
  `CLEANED_CSV_NAME` under tool class `qc`, so a later `load_experiment(require_clean=True)`
  **does not** resolve a `qc_inspect` run — it produces no cleaned version and mutates no data.
- **REGISTER** the tool in `src/bloom_mcp/server.py` under "Direct tools (granular)" so it
  appears in MCP `tools/list`.
- **EXTRACT** the role-forwarding (`_role_kwargs`) and trait-subset validation
  (`_validate_trait_subset`) helpers `qc_clean` introduced into a small shared module both
  tools import, so the two tools forward roles and reject bad columns identically (reuse, not
  a second copy).
- **`qc_clean` tie-in (message-only, lands in #356 — not a deliverable here):** `qc_clean`'s
  result nudges to `qc_inspect` when it dropped samples (`n_samples_dropped > 0`). This corrects
  the issue's stale "residual NaNs in the cleaned output > 0" trigger — #356's no-NaN guard means
  that never fires; the honest trigger is **sample loss**. It is an additive, behavior-compatible
  message with **no spec delta**, owned by #356 (which keeps the change with its capability). See
  tasks §6.1.
- Tests cover the **oracle on the raw `turface_19` fixture + the 5 contract patterns + the
  read-only assertion + a figure-persistence round-trip**: the recommendation flags the two
  15.5 %-NaN traits and recommends `≤ 0.15` (0 samples lost vs naive `dropna` = 29); `tools/list`
  presence; schema round-trip + invalid-input envelope; provenance (`seed = None`); structured
  error envelope; links-not-blobs; and a `qc_inspect` run is **not** resolvable as a cleaned
  version by `require_clean=True`.
- **No new fixture, no new dependency:** reuses the raw `turface_19_raw_data.csv` +
  `turface_19_qc_golden.json` #356 already vendored; records the inspection oracle
  (per-trait NaN fractions, recommended threshold, naive-dropna loss) into a small golden.

## Impact

- **Affected specs:** `bloommcp-qc-inspect-tool` (**new** capability). Builds on (does not
  modify) the existing `bloommcp-tool-contract`, `bloommcp-experiment-read`, and
  `bloommcp-result-store` capabilities, and depends on the in-flight `bloommcp-qc-clean-tool`
  (#356) for the raw fixture, the shared role/validation helpers, and the persistence shape.
- **Affected code:**
  - new `bloommcp/src/bloom_mcp/tools/qc_inspect_tool.py` (tool + I/O models + `register`);
  - new shared helper module (e.g. `bloommcp/src/bloom_mcp/tools/_qc_shared.py`) holding
    `_role_kwargs` + `_validate_trait_subset`, with `qc_clean_tool.py` updated to import them
    (no behavior change to `qc_clean`);
  - `bloommcp/src/bloom_mcp/server.py` (register the tool; update the module docstring);
  - new `bloommcp/tests/tools/test_qc_inspect_tool.py` (oracle + 5 patterns + read-only +
    figure round-trip);
  - extend `bloommcp/tests/fixtures/README.md` + add `turface_19_qc_inspect_golden.json`
    (per-trait NaN fractions + recommendation oracle, computed from the LF-normalized raw
    fixture — an explicit asserted value, not re-derived from the code under test);
  - **roadmap reshape stays owned by PR #339** (`eberrigan/bloommcp-tier3-qc`), so
    `bloommcp/docs/roadmap.md` is **not** edited here, to avoid a conflict. **Safeguard:** #339
    owns the Tier-row edit for *both* `qc_clean` and `qc_inspect` and has not landed — confirm
    `qc_inspect` is in #339's scope, and file a follow-up if #339 stalls (tasks §6.2);
  - **no edit needed** to `bloommcp/README.md` (it lists tool *categories*, not individual tools,
    and `qc_inspect` is QC) or `DEV_SETUP.md` (`qc_inspect` ships **no** `make bloommcp-smoke`
    leg, so the existing clustering + `qc_clean` smoke pointers are unaffected) — noted explicitly
    so the omission reads as deliberate, not an oversight;
  - the `qc_clean` nudge (message-only) is owned by **#356**, not this change — see What Changes.
- **Dependencies:** none new — `create_trait_eda_plots`, `create_exploratory_summary_plots`,
  `inspect_nan_samples`, `apply_data_cleanup_filters` are all public in
  `sleap_roots_analyze 0.1.0a3` (already pinned by #356).
- **Sequencing (branch base is load-bearing):** Tier-3 companion to `qc_clean` (#356) → feeds
  `pca_analysis` (#308). Base this change on **#356's branch** while #356 is open (the raw
  fixture and shared helpers exist only there, not on `staging`); **rebase onto `origin/staging`
  once #356 merges**; **never merge `qc_inspect` before #356**. PR targets `staging` (not `main`).
- **Implements #360.** Parent: #338 · Sibling: #356 (`qc_clean`) · Consumer: #308 (`pca_analysis`).
</content>
