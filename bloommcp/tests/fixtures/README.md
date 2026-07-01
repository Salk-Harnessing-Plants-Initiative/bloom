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
- `turface_19_pca_golden.json` — recorded golden + drift snapshots for that table.
  The keys carry **distinct provenance** (see the `_*_source` fields):

  - **PCA** (`pca_explained_variance` ≈0.95991, `n_pca_components` = 3) is an
    _independently recorded_ golden from #120's `viz_pca_metadata.json`. The recorded
    `top_features` field was **omitted**: it comes from a viz-specific ranking
    heuristic in #120 that `perform_pca_analysis` does not expose, so it cannot be
    asserted as a faithful oracle. The per-PC `pca_explained_variance_ratio`
    (`[0.8613, 0.0582, 0.0404]`, added for Tier 4 / #308) is a **characterization
    snapshot** re-derived from `perform_pca_analysis==0.1.0a3` (see `_pca_evr_source`):
    the upstream viz metadata records only the _cumulative_ value, so this per-PC split
    is a drift gate, **not** an independent oracle — its three entries sum to the
    independent cumulative `pca_explained_variance` above.
  - **Heritability** (`heritability_mean` = 0.7650…, `heritability_method`,
    `heritability_n_above_0.5`) is a _characterization snapshot_ of
    `sleap-roots-analyze==0.1.0a2` on this fixture — **not** an independently validated
    value (the PCA-metadata source above does not contain a heritability mean). It gates
    future drift only; reconciling it to the R/lme4 reference the library docstring
    claims to match is tracked by **#315**.
  - **UMAP** (`umap_trustworthiness` ≈0.95, `umap_trustworthiness_floor` = 0.88) is a
    _structural_ snapshot: UMAP coordinates are not cross-OS bit-stable, so the gate
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
