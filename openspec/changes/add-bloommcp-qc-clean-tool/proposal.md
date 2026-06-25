## Why

bloom-mcp Phase 2 has landed the contract layer (`@as_mcp_tool`, Tier 1 / #306) and
the persistence ports (`ExperimentReader` + `ResultStore`, Tier 2 / #307), but **no
granular analysis tool wraps them end-to-end yet**, and there is **no QC foundation**
that produces a clean, analysis-ready trait table for the downstream tools to consume.
The current QC surface is the bespoke `run_qc_workflow` over the **vendored**
`bloom_mcp.data_cleanup` copy, which re-implements cleanup logic the MCP should not own.

Tier 3 (#338) adds the **first granular tool**, `qc_clean`, and the **QC foundation**:
it turns a raw experiment trait table into a clean, **no-NaN** trait CSV by delegating to
`sleap-roots-analyze`'s tested `clean_traits_for_analysis` entry point — re-orchestrating
nothing. This proves the contract + `ResultStore` write path on a real tool and establishes
the **tool-composition pattern** (`qc_clean` → cleaned versioned run → `pca_analysis`
consumes it): PCA already requires a cleaned input (`require_clean=True`, Tier 4 / #308),
and `qc_clean` is the producer that satisfies it.

## Why QC before PCA

`perform_pca_analysis` does not crash on NaNs — it silently `dropna()`s, dropping *every
row with any NaN*, which is uncontrolled sample loss. analyze's smart cleanup drops bad
*traits* first, minimizing sample loss. So we clean **first**, with the tested upstream
function, not with PCA's blunt internal drop. The oracle pins exactly this: through the
tool, `qc_clean` yields a no-NaN table that **drops fewer samples than a naive `dropna()`**.

## What Changes

- **ADD** a granular `qc_clean` MCP tool: Pydantic input/output models, a tool function
  wrapped by `@as_mcp_tool`, that
  - reads the **raw** experiment frame through the injected `ExperimentReader` port (this
    tool is the *producer* of cleaned data, so it does **not** set `require_clean`), passing
    the adapter-detected role columns (`genotype_col` / `replicate_col` / `sample_id_col`)
    into the delegate rather than relying on the delegate's `geno`/`rep`/`Barcode` defaults;
  - delegates **all** QC to `sleap_roots_analyze.clean_traits_for_analysis` (the minimal-QC
    entry point — cleanup + validate; analyze#164). It **does not** run the full
    `QCPipeline` and **does not** re-stitch `load → cleanup → validate` in the MCP — that
    orchestration is analyze's, tested upstream. The MCP contains **no QC logic**;
  - **persists a versioned run via the `ResultStore` port** under tool class `qc` — the
    cleaned trait CSV (`_cleaned.csv`) + the cleanup log (`cleanup_log.json`: what
    traits/samples were dropped and why) + provenance — so the run is resolvable by the
    reader as a **cleaned version** that `pca_analysis` (`require_clean=True`) consumes;
  - returns a small summary inline (n samples in/out, n traits in/out, retention) +
    `resource_link`s to the cleaned CSV and the log — never inline blobs;
  - optionally surfaces `inspect_nan_samples` output in the summary (where the NaNs were);
  - carries the contract-stamped `Provenance` into the persisted manifest.
- **REGISTER** the tool in `src/bloom_mcp/server.py` so it appears in MCP `tools/list`.
- **Seed is recorded as `None`**: QC is deterministic (filter thresholds, no `random_state`);
  per the Tier-1 contract the resolved seed is recorded *only when actually applied*. The
  tool declares `provenance` but **not** `random_state`; the determinism-governing `params`
  (the four cleanup thresholds + trait-column selection) are captured in provenance instead.
- **BUMP** the `sleap-roots-analyze` pin to `>=0.1.0a3` (bloom #327) + `uv lock`:
  `clean_traits_for_analysis` (analyze#164) landed in `0.1.0a3`, so this is a **hard**
  dependency for the tool (unlike PCA, whose delegate was already public in `0.1.0a2`).
- **LEAVE** the existing `run_qc_workflow` and the vendored `bloom_mcp.data_cleanup` in
  place — this **adds granularity alongside**; retirement of `source/*` + the bespoke
  workflow tools is **deferred to after Stage 1** (deleting them now breaks the booting
  server, whose workflow tools module-level-import them).
- Tests cover the **5 contract patterns + the no-NaN / fewer-than-dropna oracle through the
  tool**: oracle reproduction, `tools/list` presence, schema round-trip, provenance presence,
  property/invariant, and the structured error envelope.

## Impact

- **Affected specs:** `bloommcp-qc-clean-tool` (new capability). Builds on (does not modify)
  the existing `bloommcp-tool-contract`, `bloommcp-experiment-read`, and
  `bloommcp-result-store` capabilities from Tiers 1–2.
- **Affected code:**
  - new `bloommcp/src/bloom_mcp/tools/qc_clean_tool.py` (tool + I/O models + `register`);
  - `bloommcp/src/bloom_mcp/server.py` (register the tool; update the module docstring);
  - new `bloommcp/tests/tools/test_qc_clean_tool.py` (5 patterns) + the oracle through the
    tool;
  - new **raw** turface_19 fixture under `bloommcp/tests/fixtures/` (the pre-QC, NaN-bearing
    input from the same talmolab/sleap-roots-analyze #120 / #146 source) + a recorded
    golden cleaned shape, since only the *post-QC* `turface_19_final_data.csv` exists today;
  - `bloommcp/pyproject.toml` + `uv.lock` (bump the analyze pin to `>=0.1.0a3`);
  - no change to `bloom_mcp.data_cleanup`, `tools/workflows/qc.py`, or the discovery tools.
- **Dependencies:** `sleap_roots_analyze.clean_traits_for_analysis` (analyze#164), released
  in `0.1.0a3`. Consuming any upstream typed cleanup-log result is a possible later upgrade,
  but `clean_traits_for_analysis` returns a plain `(df, kept_cols, log_dict)` tuple the tool
  maps into its own output model — no duplicated QC logic.
- **Branch/PR:** branches off `origin/staging`; PR targets `staging`.
</content>
</invoke>
