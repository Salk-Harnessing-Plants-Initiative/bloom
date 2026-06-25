## 1. Pre-work — dependency pin + raw fixture (prerequisite)

- [ ] 1.1 Bump `sleap-roots-analyze>=0.1.0a3` in `bloommcp/pyproject.toml` + `uv lock`
      (bloom #327); confirm `from sleap_roots_analyze import clean_traits_for_analysis,
      inspect_nan_samples` imports under the new lock.
- [ ] 1.2 Source the **raw** (pre-QC, NaN-bearing) turface_19 trait table from the same
      talmolab/sleap-roots-analyze #120 / #146 source as the existing post-QC fixture; add it
      as `bloommcp/tests/fixtures/turface_19_raw_data.csv` and document its provenance in
      `tests/fixtures/README.md` (independently sourced, not re-derived from the code).
- [ ] 1.3 Record the golden cleaned shape (`n_samples_out`, `n_traits_out`) and the naive
      `dropna()` sample count for the raw fixture into a small JSON snapshot
      (`turface_19_qc_golden.json`) so the oracle is an explicit, asserted value.

## 2. RED — oracle through the tool (north star, write first)

- [ ] 2.1 Add `bloommcp/tests/tools/test_qc_clean_tool.py`; wire `_ports.configure(...)` with
      a `FakeReader` serving the **raw** turface_19 fixture and a `FakeResultStore`.
- [ ] 2.2 Write the **no-NaN-through-the-tool** test FIRST: invoke `qc_clean` on raw
      turface_19; assert the persisted cleaned table has zero NaNs in its kept trait columns
      and that `n_samples_out` / `n_traits_out` match the recorded golden shape. Confirm it
      fails (no tool yet).
- [ ] 2.3 Write the **fewer-than-dropna** assertion: `n_samples_out` is strictly greater than
      the naive `dropna()` row count over the same raw trait columns. Confirm RED.

## 3. RED — the remaining 4 contract patterns

- [ ] 3.1 `tools/list` presence: a FastMCP `Client` lists `qc_clean` with an input schema;
      `run_qc_workflow` still listed.
- [ ] 3.2 Schema round-trip: valid input/output validate; an invalid input (missing
      experiment, or a cleanup threshold outside `[0,1]`) → `BloomMCPError` (input/validation
      code).
- [ ] 3.3 Provenance presence: a successful call stamps `Provenance` with tool name, the
      cleanup-threshold + trait-selection params, and `seed = None`; the persisted
      `StoredRun` for `(experiment, "qc")` carries the same provenance and includes the
      cleaned CSV + cleanup log outputs.
- [ ] 3.4 Property/invariant: the cleaned table is a subset of the input (kept trait cols ⊆
      input trait cols, rows ⊆ input rows), has no NaNs, and `0 < n_samples_out <=
      n_samples_in`, `0 < n_traits_out <= n_traits_in`.
- [ ] 3.5 Error envelope: an unresolvable experiment → `BloomMCPError` with a remedy and no
      persisted run; a delegate raise → structured error, no leaked traceback/path.
- [ ] 3.6 Composition: after `qc_clean` commits a run, a `require_clean=True` load through the
      same `FakeReader`/store seam resolves the cleaned version (not the raw input).

## 4. GREEN — implement the tool

- [ ] 4.1 Add `bloommcp/src/bloom_mcp/tools/qc_clean_tool.py`: `QCCleanParams(BaseModel)`
      (experiment name, optional trait-column selection, the four cleanup thresholds with
      `[0,1]` / positive-int validation — **no `seed`**) and a `QCCleanResult`-shaped output
      model (`n_samples_in/out`, `n_traits_in/out`, retention, kept trait cols, NaN-location
      summary, `resource_link`s to cleaned CSV + log, `run_ref`, `manifest`).
- [ ] 4.2 Implement `qc_clean(params, *, provenance)` wrapped by `@as_mcp_tool` (declares
      `provenance`, **not** `random_state`): load the **raw** frame via
      `_ports.reader().load_experiment(name)` (no `require_clean`), mapping
      `ExperimentReadError` → `BloomMCPError`.
- [ ] 4.3 Delegate to `sleap_roots_analyze.clean_traits_for_analysis(df, trait_cols,
      barcode_col=frame.sample_id_col, genotype_col=frame.genotype_col,
      replicate_col=frame.replicate_col, **thresholds)`; map the returned
      `(df, kept_cols, log)` into the output model. Optionally call `inspect_nan_samples` for
      the NaN-location summary. **No QC logic in the MCP.**
- [ ] 4.4 Persist via `_ports.store().create_run(..., tool_class="qc", provenance=...)` →
      stage `_cleaned.csv` + `cleanup_log.json` into the run's staging dir → `commit(...)`;
      return the inline summary + `resource_link`s from the `StoredRun`. Match the
      `_cleaned.csv` naming the reader's cleaned-version resolution expects.
- [ ] 4.5 Add `register(mcp)` using `bloom_mcp.contract.register(mcp, qc_clean)`.
- [ ] 4.6 Register the module in `src/bloom_mcp/server.py` (Direct/granular tools section)
      and update its module docstring tool list.
- [ ] 4.7 Run the suite; debug to GREEN **without** weakening the no-NaN / fewer-than-dropna
      oracle.

## 5. Refactor & verify

- [ ] 5.1 Refactor for clarity; keep the delegate `(df, kept_cols, log)` → output-model
      mapping isolated. Confirm `bloom_mcp.data_cleanup` + `run_qc_workflow` untouched and the
      server still boots.
- [ ] 5.2 `/pre-merge`: lint + full bloom-mcp suite + `openspec validate
      add-bloommcp-qc-clean-tool --strict` all green.
- [ ] 5.3 Validate on **Claude Desktop** (capable model): `qc_clean` is selectable, produces a
      no-NaN cleaned run, and a follow-up `pca_analysis` consumes that cleaned run; sanity-check
      on the small Qwen surface that the tool returns a sane structured result.

## 6. Follow-ups (out of this change's scope — tracked, not done here)

- [ ] 6.1 Retirement of the vendored `bloom_mcp.data_cleanup` + `run_qc_workflow` stays
      deferred to after Stage 1 (Tiers 0–4) — do **not** remove here.
- [ ] 6.2 If analyze later exposes a typed cleanup-log result, swap the internal
      `(df, kept_cols, log)` mapping for it — a localized change, not a duplicated adapter.
</content>
