> **TDD note:** the RED steps below (§2–§3) are a *local* working-tree rhythm — confirm each
> fails, then make it pass. Do **not** push a RED-only commit: this repo's CI (`pr-checks.yml`
> line 128, `cd bloommcp && uv run --frozen --extra test pytest tests/`) gates the PR head, and
> a committed failing/**uncollectable** test (importing `remove_outliers_tool` before it exists)
> is red. Commit RED+GREEN together.
>
> **Commit plan** (no dependency commit — `sleap-roots-analyze>=0.1.0a4` already carries both
> delegates, landed in #387). Every commit ends with
> `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`:
> 1. `docs(#378): openspec proposal — granular remove_outliers tool` — the `openspec/changes/…`
>    artifacts (green; run `openspec validate --strict` locally).
> 2. `chore(#378): add turface_19 outlier golden fixture` — the golden JSON + `fixtures/README.md`
>    entry (green; inert JSON, no lock change, deliberately orphaned for exactly one commit).
> 3. `feat(#378): add granular remove_outliers MCP tool` — tool + I/O models + `register` +
>    `server.py` wiring + **all** §2–§3 tests (green; RED+GREEN atomic — splitting tool-from-tests
>    reintroduces the uncollectable-test hazard).
> 4. `test(#378): add remove_outliers live smoke leg + local-validation docs` — §6 (green via the
>    `dev-stack-smoke` job; kept separate from `feat` — different CI job, different failure domain).
> Expect 1–2 reactive `fix(#378): address review` commits after `/review-pr`. Single PR → `staging`.

## 1. Pre-work — outlier golden + composition harness (prerequisite)

- [x] 1.1 Confirm the delegate is importable on the pinned lock: `uv run --frozen python -c
      "from sleap_roots_analyze import remove_outlier_samples, plot_outlier_analysis"` resolves
      against `0.1.0a4` (no pin change needed). Record, from the real delegate on the vendored
      fixture: (a) the `report` dict keys and which are method-dependent (**`threshold_type`,
      `threshold_value`, and `goodness_of_fit` are all `None` for `isolation_forest`**;
      `goodness_of_fit` is a **nested dict** with a `fit_quality` key, not a scalar string); (b)
      the real `plot_outlier_analysis` figure keys per method (mahalanobis →
      `mahalanobis_outlier_detection`, `mahalanobis_pc_analysis`, `mahalanobis_threshold_analysis`,
      `outliers_per_genotype`; isolation_forest → `isolation_forest_analysis`,
      `outliers_per_genotype`); (c) the import path of the degenerate-trim raise —
      **`OutlierRemovalError` is a `ValueError` subclass at
      `sleap_roots_analyze.outlier_removal.OutlierRemovalError`, NOT importable top-level**, so the
      tool catches it via `except ValueError` (no non-top-level import needed).
- [x] 1.2 Record the **outlier golden** into `bloommcp/tests/fixtures/turface_19_outlier_golden.json`,
      computed from the **existing** LF-normalized `turface_19_raw_data.csv` (added by #338) via
      `clean_traits_for_analysis` at **canonical-default thresholds** (`max_zeros_per_trait=0.5,
      max_nans_per_trait=0.2, max_nans_per_sample=0.0, min_samples_per_trait=10`) → **158 samples**
      → `remove_outlier_samples(method="mahalanobis", random_state=42)`: `n_input_samples` (158),
      `n_outliers` (8), `n_output_samples` (150), the sorted `outlier_barcodes`, and the recorded
      `goodness_of_fit` **dict** (documenting `fit_quality == "very_poor"`). Store the exact
      cleaning params in the JSON so 158 is self-reproducible. Document it in
      `tests/fixtures/README.md` next to `turface_19_qc_golden.json`, **explicitly noting the
      158 here is the canonical-default cleaned count, not the naive-dropna number and not the
      0.1-config's 187** (independently recorded, not re-derived from the code under test).
- [x] 1.3 Composition harness: reuse the `qc_clean` pattern — stand up the **Supabase adapters**
      (`SupabaseReader` + `SupabaseResultStore`) over the shared `_InMemoryObjectStore` double
      (`tests/conftest.py`) so a committed `remove_outliers` `qc`-class run's trimmed
      `_cleaned.csv` is resolvable by the reader's cleaned-version rule. (The fakes cannot
      exercise this — `FakeReader._cleaned` and `FakeResultStore._runs` are disjoint.)

## 2. RED — golden trim through the tool (north star, write first)

- [x] 2.1 Add `bloommcp/tests/tools/test_remove_outliers_tool.py`; wire `_ports.configure(...)`
      with a `FakeReader` serving the **canonical-default cleaned** turface_19 frame (158 samples,
      seeded via `add_cleaned_version` from the cleaned snapshot behind task 1.2) and a
      `FakeResultStore`.
- [x] 2.2 Write the **golden-trim-through-the-tool** test FIRST: invoke `remove_outliers`
      (`method="mahalanobis"`, `seed=42`); assert `n_input_samples == 158`, `n_outliers == 8`,
      `n_output_samples == 150` (the recorded golden) and the sorted `outlier_barcodes` match.
      Confirm it fails (no tool yet). Name the test so it reads as a *characterization* pin
      (turface_19's fit is poor).
- [x] 2.3 Assert the persisted trimmed table has `n_output_samples` rows and zero NaNs in its
      trait columns. Confirm RED.
- [x] 2.4 Assert `goodness_of_fit` is present as a dict with `["fit_quality"] == "very_poor"`
      (the poor-fit characterization marker), and that the output model types
      `goodness_of_fit: Optional[dict]`, `threshold_type: Optional[str]`,
      `threshold_value: Optional[float]`. Confirm RED.

## 3. RED — the remaining contract patterns + composition

- [x] 3.1 `tools/list` presence: a FastMCP `Client` (the `asyncio.run(async with
      Client(server.mcp) ...)` idiom, no `pytest-asyncio`) lists `remove_outliers` with an
      input schema; `run_outlier_workflow` still listed.
- [x] 3.2 Schema round-trip: valid input/output validate; an invalid input (missing experiment,
      unknown `method`, `chi2_percentile` out of range) → `BloomMCPError` (`invalid_input`).
- [x] 3.3 Provenance + links: a successful call stamps `Provenance` with tool name, the method +
      threshold + trait-selection params, and the **resolved integer** `seed = 42`; the
      persisted `StoredRun` for `(experiment, "qc")` carries the same provenance and includes the
      trimmed `_cleaned.csv` + `outlier_report.json`. Assert the result references those via
      `resource_link`s (object keys + manifest path), does **not** embed the trimmed dataframe
      inline (no `df`/blob attr; no value > ~5 KB — the qc_clean assertion), and that reloading
      the committed `outlier_report.json` bytes parses as JSON carrying `n_outliers == 8` (guards
      a numpy-not-serializable regression; write it defensively via `convert_to_json_serializable`).
- [x] 3.4 Property/invariant: the trimmed table is a row-subset of the cleaned input (rows ⊆
      input rows, trait cols unchanged), has no NaNs, `0 < n_output_samples <= n_input_samples`,
      `n_outliers == n_input_samples - n_output_samples`, and `removal_fraction == n_outliers /
      n_input_samples` (guards a wrong-denominator mapping).
- [x] 3.5 Delegation pinning (spy on `remove_outlier_samples`): assert `remove_outliers` calls
      it exactly once, forwards `barcode_col=frame.sample_id_col`,
      `genotype_col=frame.genotype_col`, `replicate_col=frame.replicate_col`, `method`, and
      `random_state=42` **by keyword** (the delegate's `random_state` is keyword-only), and
      **never** calls the vendored `bloom_mcp.outlier_detection` filters.
- [x] 3.5b Default-method: invoke `remove_outliers` with **no** `method` and assert the delegate
      spy receives `method="mahalanobis"` (covers the default-method scenario, which 2.2 passes
      explicitly and so does not cover).
- [x] 3.6 Role-column fallback: seed a `FakeReader` cleaned frame whose role columns detect as
      `None`; assert `remove_outliers` does **not** forward `None` (omits the kwarg / uses the
      default) and still produces a trimmed run. Plus a non-default (`Genotype`/`Replicate`)
      fixture proving the detected roles override the delegate defaults.
- [x] 3.7 Guardrail — un-cleaned input: a `FakeReader` that raises `CleanedVersionRequiredError`
      on `require_clean=True` → `BloomMCPError(assumption_violated)` with a "run qc_clean first"
      remedy, no persisted run. (Mapped in the **tool body**, not via `errors=`.)
- [x] 3.8 Degenerate-trim guard (real delegate, no mock): an aggressive **`chi2_percentile`** low
      enough to trim below the minimum surviving samples makes `remove_outlier_samples` *raise*
      (`ValueError`/`OutlierRemovalError`) → the tool body maps it to `assumption_violated` with a
      relax-threshold remedy (not `internal_error`), no persisted run. Non-unique-index frame →
      structured error, no run. (Note: aggressive `contamination` is a weaker degenerate lever —
      use the mahalanobis `chi2_percentile` path.)
- [x] 3.8b Leak-scrub: monkeypatch `remove_outlier_samples` to raise a **non-`ValueError`** (e.g.
      `RuntimeError("secret /var/secrets/key host db.internal")`) so it hits the contract's
      `internal_error` path; assert the surfaced `BloomMCPError.code == "internal_error"` and
      neither `message` nor `remedy` contains "secret", "/var", or "db.internal".
- [x] 3.9 Method-surface validation: `method="mahalanobis"` + `contamination` set →
      `invalid_input` naming the field; `method="isolation_forest"` + `chi2_percentile` set →
      `invalid_input` (both validated in the tool body). `isolation_forest` happy path returns
      `threshold_type is None`, `threshold_value is None`, `goodness_of_fit is None` and a valid
      report; the mahalanobis golden path has `threshold_type == "chi_squared"`.
- [x] 3.10 Composition (through the harness from task 1.3): after `remove_outliers` commits a
      run, a `require_clean=True` load through `SupabaseReader`/`SupabaseResultStore` over the
      shared `_InMemoryObjectStore` resolves the committed **trimmed** cleaned version (source
      `v<N>_cleaned`) with fewer rows than the pre-trim clean and `isna()==0`. **The reloaded
      artifact is the golden-trim oracle** — the FakeResultStore path can't reload, so this real
      round-trip guards against a persisted-NaN / wrong-rows regression.
- [x] 3.11 Plots: `include_plots=False` (default) persists no figures; `include_plots=True,
      method="mahalanobis"` persists the delegate's figures into the same run — assert
      `output_keys` ⊇ the recorded mahalanobis figure key set (task 1.1) — and the result returns
      links, not inline blobs; `include_plots=True, plots=["mahalanobis_pc_analysis"]` persists
      **exactly** that one figure (the `which=` happy path); an explicit `plots` naming an
      unavailable figure key → `invalid_input` (validated in the tool body before delegating).
- [x] 3.12 Second run increments version and supersedes latest: two `remove_outliers` runs →
      `v<N>`, `v<N+1>`; a `require_clean=True` load resolves the **second** (latest) trim — the
      documented order-dependent "latest cleaned".

## 4. GREEN — implement the tool

- [x] 4.1 Add `bloommcp/src/bloom_mcp/tools/remove_outliers_tool.py`:
      `RemoveOutliersParams(BaseModel)` (experiment name; optional `trait_columns`;
      `method: Literal["mahalanobis","isolation_forest"] = "mahalanobis"`; `seed: int = 42`;
      optional per-method `chi2_percentile` (`(0,100)`) / `contamination` (`(0,0.5)`) with range
      validation; `include_plots: bool = False`; optional `plots: list[str] | None`; optional
      `user_label`) and a `RemoveOutliersResult` output model (`experiment`, `source`, `method`,
      `n_input_samples`, `n_outliers`, `n_output_samples`, `removal_fraction`,
      **`threshold_type: Optional[str]`**, **`threshold_value: Optional[float]`**,
      **`goodness_of_fit: Optional[dict]`**, `outlier_barcodes`, `run_ref`, `version_dir`,
      `manifest_path`, `outputs`).
- [x] 4.2 Implement `remove_outliers(params, *, random_state, provenance)` wrapped by
      `@as_mcp_tool` (declares **both** `random_state` and `provenance`): load the **cleaned**
      frame via `_ports.reader().load_experiment(name, require_clean=True)`. **Map errors in the
      tool body** (not via `errors=` — the contract's `errors=` path yields `tool_error`, never
      `assumption_violated`): wrap the reader load in `except CleanedVersionRequiredError → raise
      BloomMCPError(assumption_violated, "run qc_clean first")`.
- [x] 4.3 Validate the method/threshold pairing and any caller `trait_columns` subset up front in
      the **body** (`invalid_input` on cross-method threshold or unknown/non-numeric column).
      Delegate to `remove_outlier_samples(frame.df, trait_cols, method=…,
      random_state=random_state, **role_kwargs, **detect_kwargs)` — forwarding detected roles
      (omitting `None`), `random_state` by keyword — inside `try/except ValueError` (which catches
      `OutlierRemovalError`, its subclass) → `raise BloomMCPError(assumption_violated,
      relax-threshold remedy)`. Map the returned `(trimmed_df, report)` into the output model
      (extract nested fields; leave `goodness_of_fit` as the dict). **No outlier logic in the
      MCP.**
- [x] 4.4 Own pre-commit guard (defense-in-depth, parity with qc_clean, for a delegate that
      *returns* rather than raises a degenerate frame): assert `trimmed[trait_cols].isna().sum()
      .sum() == 0`, `0 < n_output_samples <= n_input_samples`, and rows ⊆ input rows → else
      `BloomMCPError`, persist nothing. Then persist via `_ports.store().create_run(experiment=…,
      tool_class="qc", provenance=provenance, user_label=…, source_csv=…)` → stage the trimmed
      table as `CLEANED_CSV_NAME` + `outlier_report.json` (via `convert_to_json_serializable`) →
      `commit(...)`; return the inline report + links from the `StoredRun`. Use the shared
      `CLEANED_CSV_NAME` constant so the trimmed table is resolvable as the latest cleaned version.
- [x] 4.5 Plots: when `include_plots`, validate an explicit `plots` against the delegate's
      available figure keys **in the body** (`invalid_input` on unknown — the delegate's own
      unknown-`which` raises a bare `ValueError` that would otherwise map to `tool_error`), then
      call `plot_outlier_analysis(frame.df, trait_cols, method=…, random_state=random_state,
      which=params.plots, **role_kwargs, **detect_kwargs)`; stage each `Figure` (e.g.
      `fig.savefig(png)`) into the same run and include the figure names in the committed outputs.
      No plotting logic — persistence only.
- [x] 4.6 Add `register(mcp)` using `bloom_mcp.contract.register(mcp, remove_outliers)`.
- [x] 4.7 Register the module in `src/bloom_mcp/server.py` under "Direct tools (granular)" and
      add `remove_outliers` to its module-docstring tool list. Write the tool description per
      design Decision 8 (steer on `goodness_of_fit["fit_quality"]` → prefer `isolation_forest`
      when poor; `include_plots` guidance).
- [x] 4.8 Run the suite; debug to GREEN **without** weakening the golden-trim oracle.

## 5. Refactor & verify

- [x] 5.1 Refactor for clarity; keep the delegate `(trimmed_df, report)` → output-model mapping
      isolated. Confirm `bloom_mcp.outlier_detection` + `run_outlier_workflow` untouched and the
      server still boots (`uv run python -c "import bloom_mcp.server"` clean with the new tool
      registered).
- [x] 5.2 `/pre-merge`: `black --check` + `ruff check` over `bloommcp/`; full bloom-mcp suite
      (`uv run --frozen --extra test pytest tests/`); `uv lock --check` (proves the lock was not
      accidentally touched — no dep change) + `python scripts/check-uv-locks.py`; `import
      bloom_mcp.server` boot; `openspec validate add-bloommcp-remove-outliers-tool --strict` — all
      green.
- [ ] 5.3 Validate on **Claude Desktop** (capable model): `remove_outliers` is selectable after
      `qc_clean`, produces a trimmed run + a numeric report, surfaces the poor
      `goodness_of_fit`, and a follow-up `require_clean=True` consumer reads the trimmed cleaned
      run; sanity-check on the small Qwen surface that the tool returns a sane structured result.

## 6. Live persistence smoke leg + local-validation docs

- [x] 6.1 Add a `remove_outliers` leg to `bloommcp/scripts/live_persistence_smoke.py`, driven
      through the **real** `SupabaseReader` / `SupabaseResultStore` against the dev stack: after
      the existing `qc_clean` leg commits a cleaned version, run `remove_outliers(experiment=…,
      method="mahalanobis", seed=42)`, then assert the committed outputs include `_cleaned.csv` +
      `outlier_report.json`, the manifest is `manifest_schema_version == 3`, and each recorded
      `output_sha256` matches the actual stored bytes. Assert **structural** invariants (fewer
      rows than the pre-trim clean, zero NaNs) rather than the exact unit-golden counts (the
      smoke's cleaned input uses the qc_clean leg's own threshold, which may differ from the
      unit golden's canonical-default 158). Make the leg visibly distinct in stdout + the final
      summary line.
- [x] 6.2 In the same leg, call `SupabaseReader().load_experiment(…, require_clean=True)` after
      the run commits and assert the resolved `source` is the trimmed cleaned artifact
      (`v<N>_cleaned`) with fewer rows than the pre-trim clean and `df[trait_cols].isna().sum()
      .sum() == 0` — the `qc_clean → remove_outliers → require_clean` chain proven over the real
      ports.
- [x] 6.3 Add pure-logic unit tests for the new smoke helpers to
      `tests/scripts/test_live_persistence_smoke_logic.py`, matching the existing no-live-stack
      pattern.
- [x] 6.4 Add a `remove_outliers` leg to `bloommcp/docs/local-validation.md` (what it asserts;
      how to run) and a Claude dogfood row (run `qc_clean` → `remove_outliers`, capture the
      report + a persisted plot when `include_plots=true`, note the goodness-of-fit steering).
      **Update the smoke-leg enumeration** in `bloommcp/README.md` (the "drives clustering and
      `qc_clean`…" sentence) and `DEV_SETUP.md` (the `make bloommcp-smoke` line) to include
      `remove_outliers` — or reword both to a non-exhaustive "the granular QC tools" so future
      legs need no doc churn (DRY-preferred).
- [ ] 6.5 Re-run `make bloommcp-smoke` (all legs green) and the smoke helper unit tests.

## 7. Follow-ups (out of this change's scope — tracked, not done here)

- [ ] 7.1 Retirement of the vendored `bloom_mcp.outlier_detection` + `run_outlier_workflow`
      stays deferred to after Stage 1 (Tiers 0–4) — do **not** remove here.
- [ ] 7.2 Dedicated `tool_class="outliers"` + a `_resolve_versioned_cleaned` extension that
      prefers the newest cleaned across `{qc, outliers}` — only if a consumer later needs to read
      the un-trimmed clean after trimming (design Decision 1 alternative). A localized reader
      change, tracked separately.
- [ ] 7.3 roadmap.md tier reshape (if outlier removal is ever promoted to a named tier) is **not**
      edited here — like `qc_clean`, this tool is added alongside the tiers and the roadmap
      reshape is owned separately, to avoid a conflict on `roadmap.md`.
- [ ] 7.4 If `remove_outlier_samples` later exposes `return_detector_result=True` payloads worth
      persisting (e.g. per-sample distances), add them as an optional linked artifact — a
      localized mapping change, not duplicated logic.

## 8. Review response (PR #400, 5-agent `/review-pr`) — reactive fixes

- [x] 8.1 **B1 (blocking):** coerce the delegate's barcode-less `outlier_barcodes=None` return to
      `[]` (`report.get("outlier_barcodes") or []`) so a cleaned frame with no barcode column no
      longer crashes into an opaque `internal_error`. Add a real-delegate integration test on a
      barcode-less cleaned frame (the role-less spy tests masked it by returning `[]`; turface_19
      has a Barcode column so the golden never hits it).
- [x] 8.2 **I1:** implement the spec's pre-commit guard fully — besides NaN-free + `0 <
      n_output <= n_input`, verify the trait columns are unchanged (none dropped/renamed) and the
      returned rows are a subset of the cleaned input (by the sample-id column when present, else
      the index), inspecting the *returned frame* rather than the delegate's self-reported counts.
      Add own-guard tests for a returned NaN frame, an all-dropped frame, and a row-foreign frame.
- [x] 8.3 **I2:** set `provenance.based_on_version = frame.source` so the manifest records the
      trim derived from the cleaned version (`v<N>_cleaned`), not the `"raw"` default; assert it
      at `create_run`.
- [x] 8.4 **I4:** add the two uncovered spec scenarios — the own-guard degenerate-return branch
      (8.2) and the unknown/non-numeric `trait_columns` validator.
- [x] 8.5 **I5:** relax the flaky live-smoke row-count leg from strict `<` to `<=` (a zero-outlier
      no-op trim on the smoke's own cleaned frame is not a regression) and add a provenance-based
      composition anchor (`tool == "remove_outliers"` on the latest `qc` run). Update the
      smoke-logic unit tests.
- [x] 8.6 **I6:** add a machine-visible `fit_is_trustworthy: Optional[bool]` to the result,
      derived from `goodness_of_fit.fit_quality`, so a poor mahalanobis fit is visible to the next
      tool without parsing the dict / description prose.
- [x] 8.7 **Suggestions:** distinguish the structural (non-unique-index / duplicate-columns)
      `ValueError` remedy from the degenerate-trim one (import `OutlierRemovalError`, split the
      handler); type figures via a `TYPE_CHECKING` `matplotlib.figure.Figure` forward-ref instead
      of `"object"`; wrap the plot-persist loop in `try/finally` so a mid-loop `savefig` failure
      still closes every figure; add one-line comments for the deliberate `qc_clean` deviations
      (lazy `matplotlib.use("Agg")`, omitted `source_csv=`) and the defense-in-depth
      `n_output > n_input` branch.
- [ ] 8.8 Re-run `/pre-merge` (black/ruff, full bloom-mcp suite, `uv lock --check`, server boot,
      `openspec validate --strict`) — all green.
