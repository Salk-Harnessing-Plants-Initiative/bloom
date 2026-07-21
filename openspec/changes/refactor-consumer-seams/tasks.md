## 0. Housekeeping

- [x] 0.1 Update the module docstring in `bloom_mcp/contract/models.py` — remove the
       stale "Tier 1 / #191" scaffolding note; describe both `ToolParams` (seed base for
       input params) and the new `RunLinks` (run-link fields base for consumer tool results)
- [x] 0.2 Fill in the TBD Purpose in `openspec/specs/bloommcp-tool-contract/spec.md`:
       "Define the contract layer for bloom-mcp tools: the `@as_mcp_tool` decorator
       (validated Pydantic I/O, structured BloomMCPError, single stamped Provenance),
       the `RunLinks` result base (shared run-link fields for consumer tools), and the
       Provenance model unified with the manifest VersionEntry."
- [x] 0.3 Update `openspec/changes/add-bloommcp-remove-outliers-tool/specs/bloommcp-remove-outliers-tool/spec.md`
       — in the "Remove Outliers Persists a Versioned Trimmed Cleaned Run and Returns Links"
       requirement, note that `RemoveOutliersResult` inherits `RunLinks` for its four run-link
       fields (`run_ref`, `version_dir`, `manifest_path`, `outputs`); include the full updated
       requirement text (per AGENTS.md MODIFIED rules)

## 1. Contract layer — RunLinks (test-first)

- [x] 1.0 Write `bloommcp/tests/contract/test_run_links.py` (red phase) covering:
       - `from bloom_mcp.contract import RunLinks` succeeds
       - `"RunLinks"` is in `bloom_mcp.contract.__all__`
       - `RunLinks` field set is exactly `{run_ref, version_dir, manifest_path, outputs}`
       - `PCAAnalysisResult` inherits `RunLinks`: none of the four fields appear directly
         in `PCAAnalysisResult.__fields__` (regression guard for task 1.2 / 3.1)
       - Round-trip: `PCAAnalysisResult.model_validate(instance.model_dump())` produces
         identical run-link and tool-specific field values
       - `run_ref` omitted → `ValidationError`; `outputs={"k": 42}` → `ValidationError`
- [x] 1.1 Add `RunLinks` Pydantic base model to `bloom_mcp/contract/models.py` (make 1.0
       partially green — model exists but is not yet exported)
- [x] 1.2 Add `from .models import RunLinks` and `"RunLinks"` to `__all__` in
       `bloom_mcp/contract/__init__.py` (make 1.0 fully green)
- [x] 1.3 Run `uv run pytest bloommcp/tests/contract/test_run_links.py -x` — all green

## 2. Shared consumer utils (test-first)

- [x] 2.1 Write `bloommcp/tests/tools/test_consumer_utils.py` (red phase) covering:
       - `_build_output_frame` with `metadata_cols` present: identity columns appear as
         the first columns in the result
       - `_build_output_frame` with empty `metadata_cols`: result equals `payload_df`
         unchanged
       - `_build_output_frame` with a **non-default index** on `frame.df` (e.g. starting
         at 100 or a string index): identity columns contain correct values and no NaNs
         — specifically designed to catch a missing `reset_index` call
       - `snapshot_frame` with a normal DataFrame: yields a readable path, the CSV
         round-trips back to an equal DataFrame, written with `index=False` (no
         `"Unnamed: 0"` column)
       - `snapshot_frame` with an empty DataFrame (zero rows): does not raise; yields a
         path containing only the header row
       - `snapshot_frame` cleanup on exception: raise inside the `with` block; assert the
         temp path no longer exists after the exception propagates
- [x] 2.2 Create `bloom_mcp/tools/_consumer_utils.py` with `_build_output_frame(frame:
       ExperimentFrame, payload_df: pd.DataFrame) -> pd.DataFrame` (prepend
       `frame.metadata_cols` onto `payload_df`; `reset_index(drop=True)` on both identity
       and payload before `pd.concat(..., axis=1)`; guard: if `metadata_cols` is empty,
       return `payload_df` directly). Include a module docstring: "Shared helpers for
       contract-wrapped consumer tools. Private to `bloom_mcp.tools`. `_build_output_frame`
       prepends identity columns to a payload DataFrame; `snapshot_frame` snapshots a
       DataFrame to a temp CSV for `create_run`/`commit`."
- [x] 2.3 Add `snapshot_frame(df: pd.DataFrame)` context manager to `_consumer_utils.py`
       (wraps `TemporaryDirectory`; writes `df.to_csv(path, index=False)`; yields the
       `Path`; tempdir cleaned up on exit including on exception via `try/finally`)
- [x] 2.4 Run `uv run pytest bloommcp/tests/tools/test_consumer_utils.py -x` — all green

## 3. Refactor pca_analysis_tool

- [x] 3.0 Add to `bloommcp/tests/tools/test_pca_analysis_tool.py` before editing the
       implementation: a test that reads the snapshot `input.csv` from a completed run
       (via `FakeResultStore.get_run`) and asserts no `"Unnamed: 0"` column is present —
       confirming `index=False` is preserved through the `snapshot_frame` extraction
- [x] 3.1 Import `RunLinks` from `bloom_mcp.contract`; make `PCAAnalysisResult` inherit
       `RunLinks` and remove the four duplicate field declarations (`run_ref`, `version_dir`,
       `manifest_path`, `outputs`) from its body
- [x] 3.2 Import `_build_output_frame` from `bloom_mcp.tools._consumer_utils`. Replace the
       `_scores_frame` function: at the call site in `pca_analysis`, first construct
       `scores_df = pd.DataFrame(pca.scores, columns=[f"PC{i+1}" for i in range(pca.n_components)])`,
       then call `_build_output_frame(frame, scores_df)` to produce the scores with identity
       columns; remove the now-unused `_scores_frame` function entirely
- [x] 3.3 Import `snapshot_frame`; replace the inline `TemporaryDirectory` / `to_csv` /
       `source_snapshot` block with `with snapshot_frame(frame.df) as source_csv:` and
       pass `source_csv` to `store.create_run(...)`
- [x] 3.4 Run `uv run pytest bloommcp/tests/tools/test_pca_analysis_tool.py -x` — all
       tests must pass unchanged (including the new 3.0 snapshot test)

## 4. Refactor remove_outliers_tool

- [x] 4.0 Run `uv run pytest bloommcp/tests/tools/test_remove_outliers_tool.py -x` to
       confirm green baseline before the refactor
- [x] 4.1 Import `RunLinks` from `bloom_mcp.contract`; make `RemoveOutliersResult` inherit
       `RunLinks` and remove the four duplicate field declarations from its body
- [x] 4.2 Run `uv run pytest bloommcp/tests/tools/test_remove_outliers_tool.py -x` — all
       tests must pass unchanged

## 5. Full validation

- [x] 5.0 Run pre-commit on changed files:
       `pre-commit run --files bloommcp/src/bloom_mcp/contract/models.py
       bloommcp/src/bloom_mcp/contract/__init__.py
       bloommcp/src/bloom_mcp/tools/_consumer_utils.py
       bloommcp/src/bloom_mcp/tools/pca_analysis_tool.py
       bloommcp/src/bloom_mcp/tools/remove_outliers_tool.py`
- [x] 5.1 Run full bloommcp unit suite: `uv run pytest bloommcp/tests/ -x`
- [x] 5.2 Run `openspec validate refactor-consumer-seams --strict`
