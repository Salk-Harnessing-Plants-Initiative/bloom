## Context

Two contract-wrapped consumer tools (`pca_analysis`, `remove_outliers`) share three
internal patterns that are pasted verbatim rather than shared:

1. **Identity-prepended output frame** — `pca_analysis._scores_frame` prepends
   `frame.metadata_cols`, resets the index, and concatenates onto the payload. The
   upcoming `clustering` tool (#422) will need an identical `_labels_frame`.

2. **`tempfile + source_csv` snapshot** — `pca_analysis` wraps its `create_run` /
   `commit` block in a `TemporaryDirectory`, snapshots `frame.df` to a CSV, and passes
   the path as `source_csv`. The clustering tool will repeat this.

3. **Run-link fields** — `PCAAnalysisResult` and `RemoveOutliersResult` each declare the
   same four Pydantic fields (`run_ref`, `version_dir`, `manifest_path`, `outputs`).

## Goals / Non-Goals

- Goals: eliminate copy-paste so the next consumer tool (`clustering`) uses shared helpers
  from the start; keep `RunLinks` in the public contract layer where result-model authors
  can import it.
- Non-Goals: change any observable behavior, alter response schemas, modify the
  `ResultStore` port, or retire legacy workflow tools (`qc_tools.py`, `correlation_tools.py`, etc.).

## Decisions

### RunLinks lives in `bloom_mcp.contract.models`, not `bloom_mcp.result_store`

`StoredRun` (in `result_store/ports.py`) already exposes `run_ref`, `version_dir`,
`manifest_path`, and `output_keys`. `RunLinks` is the *MCP-facing* projection of those
fields (string dict for `outputs`, matching the tool response envelope). Putting it in
`contract/models.py` keeps the contract layer self-contained; tools import from
`bloom_mcp.contract`, not from `bloom_mcp.result_store`.

### `_consumer_utils.py` is module-private (leading underscore)

The helpers are not public API — they exist so consumer tools avoid copy-paste. A leading
underscore signals this and deters direct import from outside `bloom_mcp.tools`.

### `snapshot_frame` is a context manager, not a plain function

The snapshot must outlive `commit()` (which hashes the file). A context manager enforces
that the `TemporaryDirectory` is cleaned up only after `commit()` returns, and makes the
usage site explicit (`with snapshot_frame(...) as source_csv:`).

### `_build_output_frame` signature: `(frame, payload_df)`

The only varying part between `_scores_frame` and the future `_labels_frame` is the
payload DataFrame (scores vs labels). Everything else — prepend `metadata_cols`, reset
index, concat — is identical. Accepting a plain `payload_df` keeps the helper generic
without coupling it to any specific result type.

## Risks / Trade-offs

- **No behavior change** — the only risk is a regression in the refactored tools. The
  existing unit test suites (`test_pca_analysis_tool.py`, `test_remove_outliers_tool.py`)
  cover every code path that touches these seams; a failing test immediately identifies a
  broken extraction.
- `remove_outliers_tool` does not use `snapshot_frame` today (it does not pass `source_csv`
  to `create_run`). Only `PCAAnalysisResult`'s `RunLinks` inheritance applies there; the
  output-frame and snapshot helpers are extracted from `pca_analysis` only, ready for
  clustering to consume.

## Open Questions

### In-flight `add-bloommcp-remove-outliers-tool` spec reconciliation

`add-bloommcp-remove-outliers-tool` is merged into `staging` but not yet archived — its spec
lives in `openspec/changes/`, not `openspec/specs/`. That spec's "Remove Outliers Persists a
Versioned Trimmed Cleaned Run and Returns Links" requirement describes `RemoveOutliersResult`
as owning four explicit run-link fields. After task 4.1 lands, those fields come from `RunLinks`
inheritance instead.

Because no base spec exists at `openspec/specs/bloommcp-remove-outliers-tool/` yet, a MODIFIED
delta from this change cannot be applied against a non-existent target. **Resolution:** task 0.3
updates the in-flight change's spec directly (a MODIFIED requirement in the in-flight change's own
spec delta) before `add-bloommcp-remove-outliers-tool` is archived. The `refactor-consumer-seams`
proposal must be committed **before** that archive PR is opened.

### `TemporaryDirectory` vs `NamedTemporaryFile` in `snapshot_frame`

`NamedTemporaryFile(delete=True)` cannot be re-read on Windows while open. The
`TemporaryDirectory` approach sidesteps this entirely — the file is a regular path within the
temp dir, readable by the store's hasher after `to_csv` returns. This is the only safe choice
for cross-platform use (the devs run Docker on Windows/WSL).
