# Cross-tier oracle fixtures (turface_19)

Independently sourced from the **talmolab/sleap-roots-analyze#120 / PR #146**
golden fixtures (`tests/fixtures/real/wheat_edpie/`), **not** re-derived from the
code under test — so the oracle is a genuine cross-tier regression check.

> Note: `#120` / `#146` refer to **talmolab/sleap-roots-analyze**, not this repo.

- `turface_19_final_data.csv` — the post-QC, analysis-ready turface_19 traits
  table (`inputs/post_qc/turface_19_final_data.csv`).
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
