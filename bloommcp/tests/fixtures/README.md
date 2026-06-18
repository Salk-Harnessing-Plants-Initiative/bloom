# Cross-tier oracle fixtures (turface_19)

Independently sourced from the **talmolab/sleap-roots-analyze#120 / PR #146**
golden fixtures (`tests/fixtures/real/wheat_edpie/`), **not** re-derived from the
code under test — so the oracle is a genuine cross-tier regression check.

> Note: `#120` / `#146` refer to **talmolab/sleap-roots-analyze**, not this repo.

- `turface_19_final_data.csv` — the post-QC, analysis-ready turface_19 traits
  table (`inputs/post_qc/turface_19_final_data.csv`).
- `turface_19_pca_golden.json` — #120's recorded PCA result for that table
  (`expected/viz/turface_19/viz_pca_metadata.json`): the `trait_cols` used,
  `n_pca_components` (3) and `pca_explained_variance` (≈0.95991) at that cut.
  The recorded `top_features` field was **omitted**: it comes from a viz-specific
  ranking heuristic in #120 that `perform_pca_analysis` does not expose, so it
  cannot be asserted as a faithful oracle (only `n_pca_components` +
  `pca_explained_variance` are). The `_reproduced_by_sleap_roots_analyze_version`
  key records the alpha version whose output matches these values.

`tests/test_oracle.py` asserts both the external `sleap_roots_analyze.pca` and the
shipped `bloom_mcp.pca` reproduce these values within tolerance, plus deterministic
`bloom_mcp` clustering / correlation numerics as a numpy-2 regression guard.
