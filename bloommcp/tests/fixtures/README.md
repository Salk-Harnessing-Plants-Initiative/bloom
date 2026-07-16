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
- `turface_19_outlier_golden.json` — characterization snapshot of the `remove_outliers` tool
  (#378) driving `sleap_roots_analyze.remove_outlier_samples` (`method="mahalanobis"`, `seed=42`)
  on the turface_19 frame cleaned at `clean_traits_for_analysis`'s **canonical defaults**
  (`max_nans_per_trait=0.2` → **158 samples**, recorded in the `cleaning_params` /
  `cleaned_samples` keys). Recorded via the **exact tool path** (`detect_columns` on the cleaned
  frame → `remove_outlier_samples`): it flags **8 outliers → 150 retained**
  (`outlier_barcodes` listed). **NB the 158 here is the canonical-default *cleaned* count** —
  distinct from `turface_19_qc_golden.json`'s 187 (that snapshot uses `max_nans_per_trait=0.1`)
  and **not** the naive-`dropna` number. turface_19's mahalanobis chi-squared fit is poor
  (`goodness_of_fit_fit_quality == "very_poor"`; the delegate warns), so this is a **method+seed
  characterization pin**, not a claim that the 8 flagged samples are ground-truth outliers.
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

- `turface_19_clustering_golden.json` — per-method cluster-metric **characterization
  snapshot** for the clustering MCP tool (Tier 5 / #309), over the same 8 PCA golden
  `trait_cols`. Regenerated by `scripts/gen_clustering_golden.py` from
  `perform_kmeans_clustering` / `perform_gmm_clustering==0.1.0a4` — every metric literal is
  machine-derived, never hand-authored (see `_source`). It is a **drift gate, NOT an
  independent oracle**: unlike PCA (whose cumulative variance comes from #120's
  `viz_pca_metadata.json`), no external clustering oracle exists for turface_19, so the same
  honest caveat as `heritability_mean` / `umap_trustworthiness` applies. The genuine
  correctness oracle for clustering is **determinism** (same seed → identical
  `cluster_labels`), asserted directly in `tests/tools/test_clustering_tool.py`, not here.
  kmeans (`n_clusters=3, seed=42`) and gmm (`n_components=3, covariance_type="full",
  seed=42`) each record the three internal-validation scores + `cluster_sizes` (+ `inertia`
  for kmeans; `bic`/`aic`/`converged` for gmm).

`tests/test_oracle.py` asserts both the external `sleap_roots_analyze.pca` and the
shipped `bloom_mcp.pca` reproduce the PCA golden within tolerance; pins the delegated
heritability + UMAP snapshots (with a structural UMAP invariant and a
wrapper-consumed-key contract); exercises a zero-variance / small-N edge-case branch;
and checks deterministic `bloom_mcp` clustering / correlation numerics as a numpy-2
regression guard.
