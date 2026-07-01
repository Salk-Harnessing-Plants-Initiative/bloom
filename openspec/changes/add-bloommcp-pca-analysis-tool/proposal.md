## Why

bloom-mcp Phase 2 has landed the contract layer (`@as_mcp_tool`, Tier 1 / #306), the
persistence ports (`ExperimentReader` + `ResultStore`, Tier 2 / #307), and the **QC producer**
`qc_clean` (Tier 3 / #338): a tool that turns a raw trait table into a versioned, no-NaN
**cleaned** run the reader resolves via `require_clean=True`. But **nothing consumes a cleaned
run yet**. The current PCA surface is the bespoke `run_dimensionality_reduction_workflow` over
the **vendored** `bloom_mcp.pca` copy, which the MCP should not own.

Tier 4 (#308) adds `pca_analysis`: the **first granular consumer**. It performs PCA on a
_cleaned_ experiment by delegating to `sleap-roots-analyze`'s tested `perform_pca_analysis` →
typed `PCAResult` (`PCAResult.from_pca_dict`) — re-orchestrating nothing. This closes the
composition the tier sequence is built to prove: `qc_clean` → cleaned versioned run →
`pca_analysis` consumes it.

## Why require a cleaned input (consume, don't re-clean)

`perform_pca_analysis` does not reject NaNs — it silently `dropna()`s, dropping _every row with
any NaN_ before fitting (an uncontrolled sample loss; the function's own docstring lists "no
valid samples remain after dropping NaN values" as a raise condition). The whole point of Tier 3
was to clean **first**, with analyze's tested `clean_traits_for_analysis`, dropping bad _traits_
before dropping samples. So `pca_analysis` **must not** re-clean and **must not** run PCA on raw
data: it loads with `require_clean=True`, which the reader satisfies only from a committed
cleaned version, **and it selects only from that version's certified-clean trait set**
(`frame.trait_cols`). Because the reader's cleaned frame guarantees no-NaN _only_ in its surviving
trait columns, restricting the selection to those columns is what makes PCA's internal `dropna()`
a genuine no-op — so the sample set PCA sees is exactly the one `qc_clean` certified. The
composition guarantee `qc_clean` was designed to provide is the guarantee `pca_analysis` relies
on, made explicit rather than assumed.

## What Changes

- **ADD** a granular `pca_analysis` MCP tool: Pydantic input/output models, a tool function
  wrapped by `@as_mcp_tool`, that
  - reads the experiment frame through the injected `ExperimentReader` port with
    **`require_clean=True`** (this tool is the _consumer_ of cleaned data). An experiment with no
    committed cleaned version raises `CleanedVersionRequiredError`, which the tool **catches and
    re-raises** as a structured `BloomMCPError` whose remedy is "run `qc_clean` first" — never a
    raw backend message and never a silent PCA over raw, NaN-bearing data;
  - is **deterministic** and records `seed = None`. The tool declares **no** `random_state`
    parameter — like `qc_clean`, and per the Tier-1 contract the resolved seed is recorded only
    when a `random_state` is actually applied. PCA here fits via sklearn's deterministic
    `covariance_eigh`/full-SVD solver (no randomized path in this data regime), so there is no
    stochastic input to record; the determinism-governing `params` (standardize, threshold,
    `n_components`, trait selection) are captured in provenance instead;
  - delegates **all** PCA to `sleap_roots_analyze.perform_pca_analysis` and wraps its result into
    the upstream typed `PCAResult` via `PCAResult.from_pca_dict`. The MCP contains **no PCA
    math** — no standardization, eigendecomposition, component selection, or loadings computation
    of its own. It does **not** call the vendored `bloom_mcp.pca`;
  - validates a caller-supplied `trait_columns` subset up front: each requested column MUST be a
    member of the reader's **certified-clean trait set** (`frame.trait_cols`) and numeric →
    otherwise `invalid_input` naming the offending columns. Restricting to the certified set (not
    merely "exists + numeric" over the whole frame) is what forecloses the silent-row-drop path —
    a NaN-bearing numeric column that `qc_clean` did not adopt as a surviving trait cannot be
    selected. As defense-in-depth the tool also asserts the selected subset is NaN-free before
    fitting;
  - forwards `standardize`, `explained_variance_threshold`, and an optional `n_components`
    (overrides threshold-based selection; the delegate clamps it to the feature count, never
    raising on `n_components > n_features`) to the delegate, with validated ranges;
  - **persists a versioned run via the `ResultStore` port** under tool class `pca` — the loadings
    and component scores as CSV artifacts + the serialized `PCAResult` (`pca_result.json`, via
    `to_json`) + provenance — and records **`based_on_version` = the cleaned source version**
    (`frame.source`, e.g. `v3_cleaned`) so the `qc_clean` → `pca_analysis` lineage (which cleaned
    run produced this PCA's input) is recoverable from the manifest. It returns the small variance
    summary inline with `resource_link`s to those artifacts, **never** the N×k score matrix
    inline;
  - carries the contract-stamped `Provenance` (`seed = None`) into the persisted manifest.
- **REGISTER** the tool in `src/bloom_mcp/server.py` so it appears in MCP `tools/list`, and add
  it to the module docstring's "Direct tools" list.
- **NO pin bump required** — `perform_pca_analysis` has been public since `0.1.0a2`, and the typed
  `PCAResult` + `PCAResult.from_pca_dict` ship in `0.1.0a3`, which `bloommcp/pyproject.toml`
  already pins (bloom #327 / #354, merged). Unlike `qc_clean`, this tier adds no dependency.
- **LEAVE** the existing `run_dimensionality_reduction_workflow` and the vendored `bloom_mcp.pca`
  in place — this **adds granularity alongside**; retirement of `source/*` + the bespoke workflow
  tools is **deferred to after Stage 1** (deleting `pca.py` now breaks the booting server, whose
  `tools/workflows/dimred.py` module-level-imports `run_pca_and_export_artifacts`).
- Tests cover the **5 contract patterns + the golden-PCA oracle through the tool**: oracle
  reproduction, `tools/list` presence, schema round-trip, provenance presence (seed **recorded as
  `None`**; two runs identical), property/invariant (`require_clean` consumption + certified-set
  restriction), and the structured error envelope.
- **EXTEND** the live persistence smoke (`make bloommcp-smoke`) with a **Tier-4 `pca_analysis`
  leg** that runs PCA over a committed `qc_clean` cleaned version through the **real**
  `SupabaseReader` / `SupabaseResultStore`, proving the cross-tier composition end-to-end. This
  leg builds on the qc_clean smoke leg and lands once #356 is merged.

## Impact

- **Affected specs:** `bloommcp-pca-analysis-tool` (new capability). Builds on (does not modify)
  the existing `bloommcp-tool-contract`, `bloommcp-experiment-read`, `bloommcp-result-store`
  (Tiers 1–2) and `bloommcp-qc-clean-tool` (Tier 3, #338) capabilities — it **consumes** the
  cleaned version `qc_clean` produces.
- **Affected code:**
  - new `bloommcp/src/bloom_mcp/tools/pca_analysis_tool.py` (tool + I/O models + `register`);
  - `bloommcp/src/bloom_mcp/server.py` (register the tool; update the module docstring);
  - new `bloommcp/tests/tools/test_pca_analysis_tool.py` (5 patterns) + the oracle through the
    tool;
  - **reuses** the already-vendored `bloommcp/tests/fixtures/turface_19_pca_golden.json` and the
    post-QC `turface_19_final_data.csv` (both on `staging`). The existing `pca_explained_variance`
    (cumulative `0.9599…`) + `n_pca_components: 3` are the **independent** #120 / PR #146 oracle
    (from `viz_pca_metadata.json`). This change **adds** a per-PC `pca_explained_variance_ratio`
    key **honestly labeled as a characterization snapshot** of `perform_pca_analysis==0.1.0a3`
    (its own `_pca_evr_source` provenance note, matching how the fixture already frames its
    `heritability_mean` / `umap_trustworthiness` keys) — a per-PC drift gate, **not** an
    independently recorded oracle. The upstream viz metadata records only the cumulative value;
    the per-PC split is not independently sourced, so it is not claimed to be;
  - `bloommcp/scripts/live_persistence_smoke.py` + `tests/scripts/` — extend the live smoke with
    the `pca_analysis` composition leg + its pure-helper unit tests (lands after #356);
  - possibly extract the shared `trait_columns` subset validator (shared with `qc_clean_tool.py`)
    into a small helper — a refactor deferred to a follow-up after both tools are on `staging`
    (`qc_clean_tool.py` is not on `staging` until #356 merges);
  - no change to `bloom_mcp.pca`'s logic, `run_dimensionality_reduction_workflow`, or the
    discovery tools; **no** edit to `bloommcp/docs/roadmap.md` (its tier-number reshape —
    qc_clean=Tier 3, pca→4, clustering→5 — is owned by #339).
- **Dependencies:** `sleap_roots_analyze.perform_pca_analysis` + `PCAResult.from_pca_dict`
  (analyze#149's typed result, released in `0.1.0a3`, already pinned). No new pin.
- **Composition dependency & merge order:** exercising `require_clean=True` against a real
  `qc_clean` run needs Tier 3 (#356) merged. The proposal, spec, and the **fakes-backed** unit
  oracle (which seeds a cleaned version directly via `FakeReader.add_cleaned_version`) do **not**
  block on #356. This PR **can merge into `staging` independently of #356** on the strength of the
  fakes oracle proving the consumer contract; only the live-smoke composition leg is a follow-up
  gated on #356 (rebase onto `staging` after it lands). Note: until #356 ships, no production path
  produces a cleaned version, so `pca_analysis` correctly returns the "run `qc_clean` first"
  remedy end-to-end — expected behavior, not a defect.
- **Branch/PR:** branches off `origin/staging` (`egao28/bloommcp-tier4-pca`); PR targets
  `staging`.
