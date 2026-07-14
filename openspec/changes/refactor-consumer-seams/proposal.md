## Why

`pca_analysis` and `remove_outliers` duplicate the same four run-link fields
(`run_ref`, `version_dir`, `manifest_path`, `outputs`) verbatim in their result models.
`pca_analysis` also owns an identity-frame builder and a `tempfile + source_csv` snapshot
block that the imminent clustering tool (#422) and UMAP tool (#425) will copy if not
extracted first. Extracting the three seams now, before a third consumer lands, prevents
the pattern from hardening as boilerplate across four tools.

## What Changes

- **New** `bloom_mcp/tools/_consumer_utils.py` — `_build_output_frame(frame, payload_df)`
  helper (prepend `metadata_cols`, reset index, concat) and `snapshot_frame(df)` context
  manager (tempfile + `to_csv`, yielding the `Path` as `source_csv`)
- **New** `RunLinks` Pydantic base model in `bloom_mcp/contract/models.py` — the four
  run-link fields (`run_ref`, `version_dir`, `manifest_path`, `outputs`) shared by all
  consumer result models; re-exported from `bloom_mcp.contract`
- **Refactor** `pca_analysis_tool.py` — `PCAAnalysisResult` inherits `RunLinks`;
  `_scores_frame` is replaced by `_build_output_frame`; the inline tempfile block is
  replaced by `snapshot_frame`
- **Refactor** `remove_outliers_tool.py` — `RemoveOutliersResult` inherits `RunLinks`
  (the four fields drop from its body)

No behavior change: the same fields are returned in the same positions; existing tests
serve as the regression suite.

## Impact

- Affected specs: `bloommcp-tool-contract` (adds `RunLinks`)
- Affected code: `bloom_mcp/contract/models.py`, `bloom_mcp/contract/__init__.py`,
  `bloom_mcp/tools/pca_analysis_tool.py`, `bloom_mcp/tools/remove_outliers_tool.py`;
  new `bloom_mcp/tools/_consumer_utils.py`
- Unaffected: all tool behaviors, response shapes, test fixtures, smoke driver
- Closes #434
