# Cross-tier oracle fixtures (turface_19)

Independently sourced from the **talmolab/sleap-roots-analyze#120 / PR #146**
golden fixtures (`tests/fixtures/real/wheat_edpie/`), **not** re-derived from the
code under test — so the oracle is a genuine cross-tier regression check.

> Note: `#120` / `#146` refer to **talmolab/sleap-roots-analyze**, not this repo.

- `turface_19_final_data.csv` — the post-QC, analysis-ready turface_19 traits
  table (`inputs/post_qc/turface_19_final_data.csv`).
- `turface_19_raw_data.csv` — the **pre-QC, NaN-bearing** turface_19 input
  (`inputs/raw/turface_19/Turface_all_traits_2024_RSR_diameter_angle_traits_removed.csv`):
  187 samples × 20 traits (+ `Barcode`/`geno`/`rep`), 58 NaNs confined to two derived
  traits (`Root_Biomass_mg`, `Root_Shoot_Ratio`, 29 samples each). This is the input the
  `qc_clean` tool's oracle cleans; the post-QC `turface_19_final_data.csv` above is the
  result of the **full** `QCPipeline` (cleanup → samples → outlier removal → heritability
  filter), whereas `qc_clean` delegates only to `clean_traits_for_analysis` (cleanup +
  validate), so the two are **not** expected to match.
- `turface_19_qc_golden.json` — characterization snapshot of
  `sleap_roots_analyze.clean_traits_for_analysis` (v0.1.0a3) on `turface_19_raw_data.csv`
  at `max_nans_per_trait=0.1`, called with the reader-detected role + trait columns exactly
  as `qc_clean` calls it: it drops the two NaN-heavy traits (`Root_Biomass_mg`,
  `Root_Shoot_Ratio`) and so **retains all 187 samples (18 traits) with zero NaNs**, versus a
  naive `dropna()` that would discard 29 samples (158 left). This is the tool's oracle:
  no-NaN output with strictly less sample loss than `dropna()`. Reproduced-by version is
  recorded in the `_reproduced_by_sleap_roots_analyze_version` key.
- `turface_19_qc_inspect_golden.json` — independently-computed oracle for the **read-only
  `qc_inspect`** tool (#360), using `sleap_roots_analyze.apply_data_cleanup_filters` (the
  delegate `qc_inspect` wraps) on `turface_19_raw_data.csv` at the **canonical defaults**
  (`max_zeros=0.5, max_nans_per_trait=0.2, max_nans_per_sample=0.0, min_samples=10`, i.e.
  `qc_clean`'s defaults). It records the consequence the agent must see: at the defaults the
  two NaN-heavy traits (see the `turface_19_raw_data.csv` entry above for the shared 187×20 /
  58-NaN / 29-sample facts) are **kept** and, because `max_nans_per_sample=0.0`, their 29
  NaN-bearing samples are **dropped** — whereas lowering `max_nans_per_trait` to `≤0.15`
  drops the two traits instead and **retains all 187 samples (0 lost)**. The recommendation
  block pins `recommended_max_nans_per_trait=0.15`, `would_remove_traits=[Root_Biomass_mg,
  Root_Shoot_Ratio]`, `samples_lost_at_recommendation=0`. **Not** re-derived from the tool.
- `turface_19_pca_golden.json` — recorded golden + drift snapshots for that table.
  The keys carry **distinct provenance** (see the `_*_source` fields):
  - **PCA** (`pca_explained_variance` ≈0.95991, `n_pca_components` = 3) is an
    *independently recorded* golden from #120's `viz_pca_metadata.json`. The recorded
    `top_features` field was **omitted**: it comes from a viz-specific ranking
    heuristic in #120 that `perform_pca_analysis` does not expose, so it cannot be
    asserted as a faithful oracle.
  - **Heritability** (`heritability_mean` = 0.7650…, `heritability_method`,
    `heritability_n_above_0.5`) is a *characterization snapshot* of
    `sleap-roots-analyze==0.1.0a2` on this fixture — **not** an independently validated
    value (the PCA-metadata source above does not contain a heritability mean). It gates
    future drift only; reconciling it to the R/lme4 reference the library docstring
    claims to match is tracked by **#315**.
  - **UMAP** (`umap_trustworthiness` ≈0.95, `umap_trustworthiness_floor` = 0.88) is a
    *structural* snapshot: UMAP coordinates are not cross-OS bit-stable, so the gate
    asserts neighbor-preservation (trustworthiness) of the embedding w.r.t. the
    standardized input — a wrong `n_neighbors`/`min_dist`/`init` delegation drops it
    below the floor (verified by a companion negative test).

  The `_reproduced_by_sleap_roots_analyze_version` key records the alpha version whose
  output matches these values.

`tests/test_oracle.py` asserts both the external `sleap_roots_analyze.pca` and the
shipped `bloom_mcp.pca` reproduce the PCA golden within tolerance; pins the delegated
heritability + UMAP snapshots (with a structural UMAP invariant and a
wrapper-consumed-key contract); exercises a zero-variance / small-N edge-case branch;
and checks deterministic `bloom_mcp` clustering / correlation numerics as a numpy-2
regression guard.
