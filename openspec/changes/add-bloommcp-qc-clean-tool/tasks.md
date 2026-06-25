> **TDD note:** the RED steps below (§2–§3) are a *local* working-tree rhythm — confirm each
> fails, then make it pass. Do **not** push a RED-only commit: this repo's CI gates the PR
> head, and a committed failing/uncollectable test is red. Commit RED+GREEN together (see the
> commit plan in the review). Suggested commits: `chore(#338)` deps+fixtures (green) →
> `feat(#338)` tool+tests (green) → optional `refactor`.

## 1. Pre-work — dependency pin + raw fixture + composition harness (prerequisite)

- [ ] 1.1 Bump `sleap-roots-analyze>=0.1.0a3` in `bloommcp/pyproject.toml` **and regenerate
      `bloommcp/uv.lock` in the same commit** (bloom #327). Acceptance: `python
      scripts/check-uv-locks.py` (the `python-audit` gate) and `uv lock --check` are clean;
      `uv run --frozen --extra test` resolves `0.1.0a3`; the `uv.lock` diff is limited to the
      `sleap-roots-analyze` entry + any genuinely new transitives (no scientific-core pin
      moved — a2/a3 floors are identical). Confirm `from sleap_roots_analyze import
      clean_traits_for_analysis` imports; import `inspect_nan_samples` **only if** the
      NaN-location summary is kept (it is optional — see design Open Questions).
- [ ] 1.2 Source the **raw** (pre-QC, NaN-bearing) turface_19 trait table from the same
      talmolab/sleap-roots-analyze #120 / #146 source as the existing post-QC fixture; add it
      as `bloommcp/tests/fixtures/turface_19_raw_data.csv` (LF, per `.gitattributes`).
      Document **both** the raw CSV **and** the golden snapshot (task 1.3) in
      `tests/fixtures/README.md`, matching how `turface_19_pca_golden.json` is documented
      (independently sourced, not re-derived from the code under test).
- [ ] 1.3 Record the golden cleaned shape (`n_samples_out`, `n_traits_out`) and the naive
      `dropna()` sample count for the raw fixture into `turface_19_qc_golden.json`, computed
      from the LF-normalized fixture, so the oracle is an explicit asserted value. Backfill
      these literal numbers into the spec's no-NaN scenario for parity with the PCA spec.
- [ ] 1.4 Composition harness: stand up the **Supabase adapters** (`SupabaseReader` +
      `SupabaseResultStore`) over the shared `_InMemoryObjectStore` test double
      (`tests/conftest.py:41`) so a committed `qc` run's `_cleaned.csv` is resolvable by the
      reader's cleaned-version rule. (The fakes cannot exercise this — `FakeReader._cleaned`
      and `FakeResultStore._runs` are disjoint; see design Risks.)

## 2. RED — oracle through the tool (north star, write first)

- [ ] 2.1 Add `bloommcp/tests/tools/test_qc_clean_tool.py`; wire `_ports.configure(...)` with
      a `FakeReader` serving the **raw** turface_19 fixture and a `FakeResultStore`.
- [ ] 2.2 Write the **no-NaN-through-the-tool** test FIRST: invoke `qc_clean` on raw
      turface_19; assert the persisted cleaned table has zero NaNs in its kept trait columns
      and that `n_samples_out` / `n_traits_out` equal the recorded golden shape (task 1.3).
      Confirm it fails (no tool yet).
- [ ] 2.3 Write the **fewer-than-dropna** assertion: `n_samples_out` is strictly greater than
      the naive `dropna()` row count over the same raw trait columns. Confirm RED.

## 3. RED — the remaining contract patterns + composition

- [ ] 3.1 `tools/list` presence: a FastMCP `Client` (using the `asyncio.run(async with
      Client(server.mcp) ...)` idiom from `test_package_baseline.py` — there is no
      `pytest-asyncio`) lists `qc_clean` with an input schema; `run_qc_workflow` still listed.
- [ ] 3.2 Schema round-trip: valid input/output validate; an invalid input (missing
      experiment, or a cleanup threshold outside `[0,1]`) → `BloomMCPError` (input/validation
      code).
- [ ] 3.3 Provenance + links: a successful call stamps `Provenance` with tool name, the
      cleanup-threshold + trait-selection params, and `seed = None`; the persisted
      `StoredRun` for `(experiment, "qc")` carries the same provenance and includes the
      `_cleaned.csv` + `cleanup_log.json` outputs; the returned result references those via
      `resource_link`s and does **not** embed the cleaned dataframe inline.
- [ ] 3.4 Property/invariant: the cleaned table is a subset of the input (kept trait cols ⊆
      input trait cols, rows ⊆ input rows), has no NaNs, and `0 < n_samples_out <=
      n_samples_in`, `0 < n_traits_out <= n_traits_in`.
- [ ] 3.5 Delegation pinning (spy on `clean_traits_for_analysis`): assert `qc_clean` calls it
      exactly once, forwards `barcode_col=frame.sample_id_col`,
      `genotype_col=frame.genotype_col`, `replicate_col=frame.replicate_col` (the fixture's
      `Genotype` / `Replicate`, not the `geno` / `rep` defaults), and **never** calls the
      vendored `bloom_mcp.data_cleanup` filters.
- [ ] 3.6 Role-column fallback: seed a `FakeReader` frame whose `genotype_col` /
      `replicate_col` / `sample_id_col` detect as `None`; assert `qc_clean` does **not**
      forward `None` into the delegate (omits the kwarg / uses the default) and still
      produces a no-NaN cleaned run.
- [ ] 3.7 Error envelope: an unresolvable experiment → `BloomMCPError` with a remedy and no
      persisted run; a cleanup that drops **every** trait → `BloomMCPError` (relax-thresholds
      remedy), no persisted run; a delegate raise → structured error, no leaked
      traceback/path.
- [ ] 3.8 Composition (through the harness from task 1.4): after `qc_clean` commits a run,
      a `require_clean=True` load through the `SupabaseReader`/`SupabaseResultStore` over the
      shared `_InMemoryObjectStore` resolves the committed cleaned version (source
      `v<N>_cleaned`, not `raw`). Optional: a second `qc_clean` run increments to `v2` and
      `latest` resolves to it.

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
      barcode_col=…, genotype_col=…, replicate_col=…, **thresholds)` — forwarding detected
      role columns, omitting any that are `None`. Map the returned `(df, kept_cols, log)` into
      the output model; raise a `BloomMCPError` if `kept_cols` is empty. Optionally call
      `inspect_nan_samples` for the NaN-location summary. **No QC logic in the MCP** (no call
      to `bloom_mcp.data_cleanup`).
- [ ] 4.4 Persist via `_ports.store().create_run(experiment=…, tool_class="qc",
      provenance=provenance, …)` → stage `_cleaned.csv` + `cleanup_log.json` into the run's
      staging dir → `commit(...)`; return the inline summary + `resource_link`s from the
      `StoredRun`. Match the `_cleaned.csv` naming the reader's cleaned-version resolution
      expects. (`create_run` takes `tool_class=` and carries no `params` arg — params live in
      `provenance`; matches the shipped port code.)
- [ ] 4.5 Add `register(mcp)` using `bloom_mcp.contract.register(mcp, qc_clean)`.
- [ ] 4.6 Register the module in `src/bloom_mcp/server.py` under "Direct tools (granular)"
      and add `qc_clean` to its module-docstring tool list.
- [ ] 4.7 Update `bloommcp/docs/roadmap.md`: insert a `qc_clean` Tier 3 row (#338) and
      renumber the granular tiers (PCA → Tier 4 / clustering → Tier 5), reconciling the
      sequencing/pre-work lines with the new numbering.
- [ ] 4.8 Run the suite; debug to GREEN **without** weakening the no-NaN / fewer-than-dropna
      oracle.

## 5. Refactor & verify

- [ ] 5.1 Refactor for clarity; keep the delegate `(df, kept_cols, log)` → output-model
      mapping isolated. Confirm `bloom_mcp.data_cleanup` + `run_qc_workflow` untouched and the
      server still boots.
- [ ] 5.2 `/pre-merge`: lint + full bloom-mcp suite + `uv run --frozen` (lock resolves
      `0.1.0a3`) + `python scripts/check-uv-locks.py` + `openspec validate
      add-bloommcp-qc-clean-tool --strict` all green.
- [ ] 5.3 Validate on **Claude Desktop** (capable model): `qc_clean` is selectable, produces a
      no-NaN cleaned run, and a follow-up `pca_analysis` consumes that cleaned run; sanity-check
      on the small Qwen surface that the tool returns a sane structured result.

## 6. Follow-ups (out of this change's scope — tracked, not done here)

- [ ] 6.1 Retirement of the vendored `bloom_mcp.data_cleanup` + `run_qc_workflow` stays
      deferred to after Stage 1 (Tiers 0–4) — do **not** remove here.
- [ ] 6.2 If analyze later exposes a typed cleanup-log result, swap the internal
      `(df, kept_cols, log)` mapping for it — a localized change, not a duplicated adapter.
- [ ] 6.3 Spec-sync (separate change): the `bloommcp-result-store` spec text reads
      `create_run(experiment, tool, params, …)` but the shipped port is
      `create_run(experiment, tool_class, …)` with params via `Provenance`. Reconcile the
      spec to the code.
</content>
