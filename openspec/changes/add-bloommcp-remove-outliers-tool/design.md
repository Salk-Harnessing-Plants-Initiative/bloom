## Context

`qc_clean` (#338) proved the granular-tool pattern: read a frame via `ExperimentReader`,
delegate the science to one tested `sleap-roots-analyze` entry point, persist a versioned run
via `ResultStore`, return a small summary + links. `remove_outliers` is the same shape for the
next QC step. The constraints are fixed by the shipped code and the shipped delegate:

- `@as_mcp_tool(input_model=, output_model=, errors=)` validates Pydantic I/O, maps exceptions
  to `BloomMCPError`, and stamps one `Provenance`. It injects `random_state` / `provenance`
  **only** into parameters the tool declares (explicit kwarg-injection). A tool that declares
  `random_state` gets the resolved seed (`resolve_seed(params.seed)` — a given int validated,
  or a fresh int when `None`) and records that **integer** in `Provenance`; the value is
  recorded only because it actually reaches the delegate. `contract/wrap.py:80-123`,
  `contract/provenance.py:40-54`
- **The `errors=` path yields `code="tool_error"`, never `assumption_violated`.** A declared
  exception is routed through `BloomMCPError.from_exception`, which hard-codes
  `code="tool_error"` (`contract/errors.py:90-95`); anything undeclared → `internal_error` with
  a scrubbed message + correlation id. `assumption_violated` is produced in exactly one way —
  an **explicit `raise BloomMCPError(code="assumption_violated", …)` in the tool body**
  (the pattern `qc_clean` uses at `tools/qc_clean_tool.py:230-269`). So every scenario this
  change marks `assumption_violated` (un-cleaned input, degenerate trim, non-unique index) MUST
  be body-mapped, not declared via `errors=`.
- The degenerate-trim raise is `OutlierRemovalError`, a **`ValueError` subclass** living at
  `sleap_roots_analyze.outlier_removal.OutlierRemovalError` (**not** importable top-level), so a
  body-level `except ValueError` catches it without a submodule import. Cross-method
  `**detect_kwargs`, a non-unique index, and an unknown plot `which=` also raise bare
  `ValueError` from the delegate — all caught by the same body handling.
- `ExperimentReader.load_experiment(name, *, version="latest", require_clean=False)` returns
  an `ExperimentFrame` exposing `df`, `trait_cols`, the detected
  `genotype_col`/`replicate_col`/`sample_id_col`, and a `source` label. With
  `require_clean=True` and no cleaned version, it raises `CleanedVersionRequiredError`.
  `data_access/ports.py:36-90`, `data_access/supabase_reader.py:37-56`
- The reader resolves a cleaned version via `experiment_utils._resolve_versioned_cleaned`,
  which is **hardcoded** to `AnalysisDir("bloommcp_output", f"{stem}.csv", "qc")` and reads
  the latest manifest entry's `CLEANED_CSV_NAME` (`_cleaned.csv`) output.
  `experiment_utils.py:220-313` — **this is why the trimmed table must be persisted under tool
  class `qc` with `_cleaned.csv` to be resolvable downstream (Decision 1).**
- `ResultStore.create_run(*, experiment, tool_class, provenance, user_label, source_csv) ->
  RunHandle` then `commit(run, outputs) -> StoredRun`; `StoredRun` exposes `run_ref`,
  `version_dir`, `manifest_path`, `output_keys`, `output_sha256`, `seed`, `code_versions`.
  Outputs are staged into `run.staging_dir` then committed by logical name → key. The port arg
  is `tool_class=`; `params` flow via `Provenance.stamp(params=…)`. `result_store/ports.py`
- Tools reach the ports through `bloom_mcp.tools._ports` (`reader()`, `store()`); the
  composition root injects Supabase adapters at boot and fakes in tests.
- The delegate:
  `remove_outlier_samples(clean_df, trait_cols=None, *, method="mahalanobis",
  barcode_col="Barcode", genotype_col="geno", replicate_col="rep", random_state=42,
  return_detector_result=False, **detect_kwargs) -> (trimmed_df, report)`. It **composes the
  existing public detectors + `remove_outliers_from_data`** (defines no logic of its own),
  enforces NaN-free + unique-index preconditions, and re-applies the #164 readiness gates after
  removal — raising when the trim is degenerate. The `report` dict carries: `method`,
  `method_params`, `random_state`, `n_input_samples`, `n_outliers`, `n_output_samples`,
  `removal_fraction`, `outlier_indices`, `outlier_barcodes`, and — **method-dependent, all
  `None` for `isolation_forest`** — `threshold_type` (`"chi_squared"` for mahalanobis),
  `threshold_value` (a float for mahalanobis), and `goodness_of_fit` (a **nested dict** with a
  `fit_quality` key for mahalanobis), plus `n_components`, `variance_threshold`.
  `plot_outlier_analysis(clean_df, trait_cols=None, *, method, …, which=None,
  **detect_kwargs) -> {name: Figure}` re-detects with the same params and returns open figures
  (no file IO).

## Goals / Non-Goals

- **Goals:** one contract-wrapped `remove_outliers` tool, registered + discoverable, delegating
  all detection/removal to `remove_outlier_samples`, reading the cleaned frame via
  `require_clean=True` and persisting a **trimmed cleaned run** (tool class `qc`,
  `_cleaned.csv`) that composes into `pca_analysis (require_clean=True)`, with a numeric report
  inline, optional persisted plots, and the 5 contract patterns + golden under test.
- **Non-Goals:** any outlier/plotting logic in the MCP; exposing detectors beyond
  `mahalanobis` + `isolation_forest` (the others stay upstream); removing
  `bloom_mcp.outlier_detection` or `run_outlier_workflow` (deferred to after Stage 1);
  modifying the Tier-2 reader to resolve a dedicated `outliers` class (Decision 1 alternative,
  a possible follow-up); a `v1/` tool namespace; a dependency-pin change (already `0.1.0a4`).

## Decisions

- **Decision 1 (central): persist the trimmed table under tool class `qc` as `_cleaned.csv` so
  it composes into `require_clean=True` for free.** The reader's
  `_resolve_versioned_cleaned` reads only the latest `qc`-class `_cleaned.csv`. Writing the
  trimmed (still NaN-free, still analysis-ready — the delegate re-applies the readiness gates)
  table there makes it the newest cleaned version, so `qc_clean → remove_outliers → pca` works
  through the existing seam with **no reader change**. A second output `outlier_report.json`
  carries the detection report (analogous to `qc_clean`'s `cleanup_log.json`), and the
  manifest's provenance records `tool="remove_outliers"`, so lineage stays honest even though
  the file name is shared.
  - *Caveat (inherited):* "latest cleaned" becomes **order-dependent** — exactly the caveat
    `qc_clean` already documents for `qc_clean` vs `run_qc_workflow` sharing class `qc` +
    `CLEANED_CSV_NAME`. The natural order (clean → trim) is monotonic; re-running `qc_clean`
    after `remove_outliers` reverts "latest" to the un-trimmed clean. Documented as
    "prefer clean → trim once per experiment." Versioning is single-writer (`create_run`
    allocates `v<N>` with no CAS) — correct for one bloom-mcp container; concurrent runs are a
    documented non-goal.
  - *Alternative considered — a dedicated `tool_class="outliers"` + extend
    `_resolve_versioned_cleaned` to prefer the newest cleaned across `{qc, outliers}`.*
    Rejected for this change: it modifies the **shipped** `bloommcp-experiment-read` capability
    (more scope, more risk on a Tier-2 seam), and Decision 1 composes without it. It stays a
    clean follow-up if explicit clean-vs-trimmed separation is later wanted (e.g. a consumer
    that must read the un-trimmed clean after trimming). **Flagged for review** — the one place
    a reviewer might prefer the heavier option.
- **Decision 2: read with `require_clean=True`; un-cleaned input is a self-correctable error.**
  The delegate's preconditions (NaN-free traits, unique index) are exactly what a `qc` cleaned
  version guarantees. `remove_outliers` is a *consumer*; requiring cleaned input keeps it from
  running detectors on raw NaN-bearing data. A missing cleaned version →
  `BloomMCPError(assumption_violated, remedy="run qc_clean first")`, the issue's guardrail — not
  a raw backend message. A non-unique index (defensive) → the delegate raises → mapped to
  `assumption_violated`.
- **Decision 3: forward the `ExperimentFrame`'s detected role columns into the delegate,
  omitting any that are `None`.** Same rule as `qc_clean`: pass `frame.sample_id_col` →
  `barcode_col`, `frame.genotype_col` → `genotype_col`, `frame.replicate_col` → `replicate_col`;
  when a role is `None`, omit the kwarg and let the delegate default apply (never forward
  `None`). Keeps role detection out of the tool. (For the vendored `turface_19_raw_data.csv`
  the detected roles are `Barcode`/`geno`/`rep` — which happen to equal the delegate defaults;
  the fallback + non-default paths are still covered by tests seeded with capitalized roles.)
- **Decision 4: the tool is stochastic — declare `random_state`, record the resolved integer
  seed.** The detectors use `random_state` (mahalanobis PCA projection, isolation-forest
  sampling). The input model carries `seed: int = 42` (default 42 for reproducibility and a
  stable golden; matches the delegate default and the issue signature). The tool declares a
  `random_state` kwarg; the contract injects `resolve_seed(params.seed)` and records that
  integer in `Provenance`. Contrast `qc_clean` (`seed=None`). Recording the resolved seed is
  what makes the trimmed artifact reproducible.
- **Decision 5: small method surface + per-method threshold via `**detect_kwargs`, validated
  up front.** `method: Literal["mahalanobis", "isolation_forest"]`. Expose `chi2_percentile`
  (mahalanobis) and `contamination` (isolation_forest) as optional fields; forward only the one
  matching `method`. A threshold set for the *other* method → `invalid_input` naming the
  offending field (the delegate would otherwise raise an opaque cross-method
  `**detect_kwargs` error). The other detectors (`pca`/`kmeans`/`gmm`/`hierarchical`) stay
  upstream, unexposed.
- **Decision 6: body-map the degenerate trim to `assumption_violated`, persist nothing.** The
  delegate raises `OutlierRemovalError` (a `ValueError` subclass) when the trim leaves
  `< MIN_SAMPLES_FOR_ANALYSIS` survivors or no non-constant trait. The tool wraps the delegate
  call in `except ValueError` and **explicitly raises `BloomMCPError(code="assumption_violated",
  remedy="raise chi2_percentile / lower contamination")`** — it does **not** declare `ValueError`
  in `errors=` (that would yield `tool_error`, not `assumption_violated`; see Context). As
  defense-in-depth for a delegate that *returns* rather than raises a degenerate frame, the tool
  also runs its **own pre-commit guard** (parity with `qc_clean`): assert `trimmed[trait_cols]`
  is NaN-free, `0 < n_output_samples <= n_input_samples`, and rows ⊆ input rows before `commit`.
  No run is persisted on any of these paths.
- **Decision 7: return the numeric report inline + links; never the table.** Inline: `method`,
  `n_input_samples`, `n_outliers`, `n_output_samples`, `removal_fraction`, and — typed to the
  delegate's real, method-dependent shape — `threshold_type: Optional[str]`,
  `threshold_value: Optional[float]`, `goodness_of_fit: Optional[dict]` (all three are `None`
  for `isolation_forest`; a non-Optional model would fail output validation on the iforest
  path), plus the flagged `outlier_barcodes`. The trimmed CSV and `outlier_report.json` go to
  `ResultStore` and come back as `resource_link`s (object keys + `manifest_path` + `run_ref`).
- **Decision 8: surface `goodness_of_fit` honestly and let it steer method choice.** On
  turface_19 the mahalanobis chi-squared assumption fits *poorly* and the delegate emits a
  `UserWarning`. `goodness_of_fit` is the delegate's **fit-report dict**
  (`{"test_type": …, "fit_quality": "very_poor", "p_value": …, "warning": …}`) — **not** a
  scalar string — so the tool returns the dict inline and steers on
  `goodness_of_fit["fit_quality"]`. Its **description guides the agent**: *"Returns a numeric
  outlier report by default. The `goodness_of_fit.fit_quality` field says whether the mahalanobis
  chi-squared threshold is trustworthy; if it is poor, prefer `method='isolation_forest'` with an
  explicit `contamination`. If the user wants to see or inspect the outliers or asks for a
  figure, set `include_plots=true` — the figures are persisted and returned as resource links."*
  No silent trust of a poorly-fit threshold.
- **Decision 9: plots default off; `plots=None` persists all figures, an explicit `plots` maps
  1:1 to the delegate's `which=`.** With `include_plots=False` (default) the tool returns the
  report only — fast. With `include_plots=True` it calls `plot_outlier_analysis` (same
  seed/params → same flagged set), and **persists each returned Figure via `ResultStore`** into
  the same run (so figures share the trimmed run's version + provenance), returning object-key
  links. With `plots=None` it persists every figure the delegate returns for the method — the
  real keys (recorded in task 1.1) are mahalanobis → `mahalanobis_outlier_detection`,
  `mahalanobis_pc_analysis`, `mahalanobis_threshold_analysis`, and `outliers_per_genotype` (when
  the genotype column is present); isolation_forest → `isolation_forest_analysis` +
  `outliers_per_genotype`. An explicit `plots` list is **validated in the tool body** against the
  method's available keys (unknown → `BloomMCPError(invalid_input)`, because the delegate's own
  unknown-`which` raises a bare `ValueError` that would otherwise map to `tool_error`), then
  forwarded as `which=`. The MCP owns **no** plotting logic and **no** friendly-name→key mapping
  table (that would be knowledge the MCP shouldn't hold).

## Risks / Trade-offs

- **Golden is a *characterization* snapshot, not a "correct answer."** turface_19's mahalanobis
  fit is poor, so the flagged set is method+seed-specific. The oracle asserts the *recorded*
  `n_outliers` (8) / `n_output_samples` (150) / sorted `outlier_barcodes` at `method=mahalanobis`,
  `seed=42` on the **canonical-default cleaned** 158-sample frame (task 1.2) — an explicit
  snapshot, exactly like `qc_clean`'s golden — and the test name + a comment say it is a
  characterization pin, not a claim the 8 flagged samples are "truly" outliers. Records the poor
  `goodness_of_fit` dict alongside so the pin is self-documenting.
  - *Disambiguation (a review landmine):* the cleaned input here is 158 samples because it uses
    `clean_traits_for_analysis`'s **canonical defaults** (`max_nans_per_trait=0.2`). The existing
    `turface_19_qc_golden.json` snapshots the `max_nans_per_trait=0.1` clean at **187** samples
    and separately records `naive_dropna_samples=158`. The 158 here is the canonical-default
    *cleaned* count — coincidentally equal to that naive-dropna number but arrived at a different
    way; task 1.2 records the exact cleaning params in the golden JSON so it is self-reproducible
    and not confused with the qc golden.
- **The composition (commit → `require_clean` resolves the trimmed table) is NOT wireable
  through the fakes** — `FakeReader._cleaned` (populated only by `add_cleaned_version`) and
  `FakeResultStore._runs` are disjoint in-memory stores. The honest composition test drives the
  **Supabase adapters** (`SupabaseReader` + `SupabaseResultStore`) over the shared
  `_InMemoryObjectStore` double in `tests/conftest.py` — the same harness `qc_clean` used to
  prove `qc` / `_cleaned.csv` resolution end-to-end. The fakes path asserts the per-port
  contracts; the adapters path asserts the cross-port handoff (trimmed run → `require_clean`).
- **Seed drift across `sleap-roots-analyze` releases** could shift the flagged set. The
  provenance records the resolved seed + `code_versions` (analyze version), so a golden change
  on an analyze bump is *visible* (the golden is re-recorded, not silently re-derived), and the
  live smoke pins the same seed.
- **Shared `qc` class / `_cleaned.csv` with `qc_clean`** — the order-dependence caveat
  (Decision 1). Documented; the natural clean→trim order is monotonic.
- **Non-unique index** in a cleaned frame would misalign detection labels → the delegate
  rejects it; the tool maps that to a structured error rather than mis-dropping rows.

## Migration Plan

Additive only — a new tool + one registration line. No dependency change (already `0.1.0a4`),
no schema or data migration; old manifests are unaffected. Rollback = unregister the tool.

## Open Questions

- **Decision 1 vs its alternative (the one remaining judgment call for review)** — reuse `qc`
  class (composes free, order-dependent) vs a dedicated `outliers` class + a reader extension
  (explicit separation, touches a shipped spec). The composition claim was **verified correct**
  against `experiment_utils._resolve_versioned_cleaned` (resolution is `latest`-by-`entry.id`,
  tool-agnostic), so reuse works. **Settle at review** only if a consumer must read the
  un-trimmed clean *after* trimming — the sole reason to prefer the heavier alternative.
- *(Resolved during review)* **`plots` surface** — keep the MCP mapping-free: `plots=None`
  persists all delegate figures for the method; an explicit `plots` is validated in the body
  against the delegate's real figure keys and forwarded as `which=`. No friendly-name alias.
- *(Resolved during review)* **Default `seed = 42`** — reproducible default + stable golden,
  matches the delegate; the field still accepts an override, and the *resolved* integer is
  recorded so any seed reproduces.
