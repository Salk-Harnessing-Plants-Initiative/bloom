# Cross-tier oracle fixtures (#120 turface_19)

Independently sourced from the `sleap-roots-analyze` #120 / PR #146 golden
fixtures (`tests/fixtures/real/wheat_edpie/`), **not** re-derived from the
library under test — so the oracle is a genuine cross-tier regression check.

- `turface_19_final_data.csv` — the post-QC, analysis-ready turface_19 traits
  table (`inputs/post_qc/turface_19_final_data.csv`).
- `turface_19_pca_golden.json` — #120's recorded PCA result for that table
  (`expected/viz/turface_19/viz_pca_metadata.json`): the `trait_cols` used,
  `n_pca_components` (3) and `pca_explained_variance` (≈0.95991) at that cut,
  and the `top_features`.

`tests/test_oracle.py` runs `sleap_roots_analyze.pca.perform_pca_analysis` on
`trait_cols` and asserts it reproduces these recorded values within tolerance.
