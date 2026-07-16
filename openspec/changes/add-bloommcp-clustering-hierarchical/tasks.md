## 1. Upstream Dependency

- [ ] 1.1 Confirm `sleap-roots-analyze 0.1.0a5` is released and provides a public labeled hierarchical
      entry point returning a `ClusterResult` (tracked: `talmolab/sleap-roots-analyze#179`)
- [ ] 1.2 Bump `bloommcp/pyproject.toml` pin to `>=0.1.0a5` and regenerate `uv.lock`

## 2. Tool Implementation

- [ ] 2.1 Add `"hierarchical"` to the `method` `Literal` in `clustering_tool.py`'s input model
- [ ] 2.2 Add the hierarchical dispatch arm: call the new upstream entry point and wrap via the
      appropriate `ClusterResult` constructor; record `seed = None` in `Provenance`
- [ ] 2.3 Extend the cross-method control guard to reject `n_clusters`, `max_clusters`, `n_components`,
      `max_components`, and `covariance_type` when `method = "hierarchical"`
- [ ] 2.4 Verify the `seed = None` provenance path propagates correctly end-to-end

## 3. Tests

- [ ] 3.1 Determinism oracle: same input → identical `cluster_labels` (no seed tolerance)
- [ ] 3.2 `tools/list` schema includes `"hierarchical"` as a valid enum value
- [ ] 3.3 Schema round-trip with `method = "hierarchical"`
- [ ] 3.4 Provenance records `seed = None` for hierarchical
- [ ] 3.5 `require_clean` consumption and certified-set restriction for hierarchical arm
- [ ] 3.6 Cross-method control rejection (kmeans/gmm controls rejected with `invalid_input`)
- [ ] 3.7 Error envelope: `CleanedVersionRequiredError` maps to structured `BloomMCPError` for hierarchical
- [ ] 3.8 Characterization snapshot: generate/extend `turface_19_clustering_golden.json` with a
      `hierarchical` entry (labeled as drift gate, not independent oracle)

## 4. Fixtures and Smoke

- [ ] 4.1 Extend `turface_19_clustering_golden.json` with hierarchical snapshot via the committed generator
- [ ] 4.2 Add a hierarchical leg to `bloommcp/scripts/live_persistence_smoke.py` (consumes a committed
      `qc_clean` run through real `SupabaseReader` / `SupabaseResultStore`)
- [ ] 4.3 Update smoke driver test helpers and docs to reflect the new leg

## 5. Docs

- [ ] 5.1 Update `bloommcp/README.md` smoke-leg sentence to include hierarchical (or use non-exhaustive
      phrasing to avoid future churn)
- [ ] 5.2 Update `DEV_SETUP.md` same sentence
- [ ] 5.3 Update `bloommcp/docs/local-validation.md`: add "Leg N — clustering (hierarchical)" section,
      disambiguated from the kmeans/gmm leg
- [ ] 5.4 Update `server.py` "Direct tools (granular)" docstring note to include `"hierarchical"` in the
      clustering entry description
