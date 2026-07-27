## Context

This is the sixth granular `sleap_roots_analyze` consumer, sitting alongside `pca_analysis` (#308)
and `clustering` (#309). The reusable seams are already built and unchanged by this proposal:

- `@as_mcp_tool(input_model=, output_model=, errors=)` validates Pydantic I/O, maps exceptions to
  `BloomMCPError`, and stamps one `Provenance` per call. A tool that declares no `random_state`
  parameter records `seed = None`; `descriptive_stats` will not declare one.
  `contract/wrap.py:80-130`.
- `ExperimentReader.load_experiment(name, *, version="latest", require_clean=False)` returns an
  `ExperimentFrame` (`df`, `trait_cols`, `metadata_cols`, role columns, `source`).
  `require_clean=True` raises `CleanedVersionRequiredError` when no cleaned version exists; for a
  cleaned source, `trait_cols` is the set `qc_clean` certified NaN-free.
- `_validate_trait_subset(frame, requested, experiment, require_certified=True)`
  (`tools/_qc_shared.py`) already implements exactly the guard this tool needs: reject empty,
  reject duplicates, reject anything outside `frame.trait_cols`, reject non-numeric. Reused
  verbatim — no new trait-validation logic.
- `ResultStore.create_run(*, experiment, tool_class, provenance, user_label, source_csv) ->
  RunHandle`, then `commit(run, outputs) -> StoredRun`. `_consumer_utils.snapshot_frame(df)` yields
  a temp-CSV path suitable for `source_csv=` (content-addresses the consumed frame, as
  `pca_analysis` does).
- The delegate: `sleap_roots_analyze.calculate_trait_statistics(df: pd.DataFrame, trait_cols:
  List[str]) -> Dict`. Per trait: `{count, mean, std, min, max, median, q25, q75, cv, skewness,
  kurtosis}`, or `{"error": "No valid data"}` if every value is NaN. `cv` is `std/mean`, or
  `np.inf` when `mean == 0`. It silently skips any `trait_cols` entry absent from `df.columns` —
  irrelevant here since `_validate_trait_subset` already rejects that case before delegating.
  Confirmed exported at `sleap_roots_analyze.calculate_trait_statistics` (top-level, in
  `__all__`), already satisfied by the pinned `sleap-roots-analyze>=0.1.0a5`.
- The legacy shape (`tools/workflows/stats.py`, retired by #438): read via `version="latest"` (raw
  **or** whatever cleaned version happened to exist — no `require_clean` gate, no certified-trait
  restriction), delegate, write the full table to `stats.csv`, return a `summary` capped to the
  first 50 traits (`_SUMMARY_TRAIT_CAP`). This proposal keeps the cap and the CSV layout, but
  changes the read contract (see Decision 1).

## Goals / Non-Goals

- Goals: port `calculate_trait_statistics` to the granular pattern; require a certified-clean
  input (composing after `qc_clean`, consistent with every other consumer tool); bound the inline
  payload for wide experiments; never leak a non-finite JSON token; persist the full table for
  lineage.
- Non-Goals: ANOVA, heritability, variance decomposition (explicitly out of scope per the issue —
  each belongs to a different tool/future issue); plots (not requested); a method/seed surface
  (the delegate has neither).

## Decisions

1. **Consumer (`require_clean=True`), not a `qc_clean`-style producer.** The issue's shape
   reference list (`qc_clean`/`pca_analysis`/`remove_outliers`/`clustering`) mixes one producer
   with three consumers. `calculate_trait_statistics` doesn't itself *need* NaN-free input (it
   `dropna()`s per trait), so a raw-frame reading would technically "work" — but it would report
   statistics over whatever numeric columns happen to be in the raw file, including ones
   `get_trait_columns` would exclude as metadata (e.g. `Computation.Time.s`) and ones `qc_clean`
   would drop as too sparse/zero-heavy. Requiring the cleaned version and restricting to
   `frame.trait_cols` reuses `_validate_trait_subset(require_certified=True)` unchanged, keeps this
   tool's reported traits consistent with what `pca_analysis`/`clustering` actually analyze, and
   avoids re-implementing role/trait resolution that `qc_clean` already owns. Alternative
   considered: default to `version="latest"` (raw-or-cleaned, matching the legacy workflow) — rejected
   because it would silently change meaning between experiments depending on whether `qc_clean` had
   been run, which is exactly the ambiguity the granular-tool family was built to remove. The
   missing-cleaned-version guard raises `BloomMCPError(code="tool_error", remedy="run qc_clean
   first")` — **genuinely** mirroring `pca_analysis.py`/`clustering.py`'s own handling of this
   exact case (both use `code="tool_error"`, verified by reading the source; `remove_outliers.py`
   is the one sibling that instead uses `assumption_violated`, and this tool does not follow it
   here — an earlier draft of this document incorrectly attributed `assumption_violated` to
   `pca_analysis`, corrected after review).
2. **New tool class `"stats"`.** Distinct from `"qc"` (`qc_clean`/`remove_outliers`),
   `"pca"`, and `"clustering"` — this tool's output (a long-format stats table) doesn't compose as
   an input to any other tool the way a cleaned CSV or a PCA result does, so there's no reason to
   share a class or a `_resolve_versioned_cleaned`-style resolution rule.
3. **50-trait cap on the inline summary only, never on the persisted CSV.** Carried over from the
   legacy workflow's `_SUMMARY_TRAIT_CAP = 50`, necessary given the cylinder fixture's 649–880
   traits (#483) — an uncapped inline response for that experiment would return a huge JSON-RPC
   payload. `stats.csv` always contains every requested (non-failed) trait; `truncated_in_summary`
   tells the caller the CSV has more rows than the inline list, and `omitted_traits` names exactly
   which ones, so a caller chasing one specific trait doesn't have to download and parse the CSV
   blind to find out whether it's in the missing tail.
4. **Coerce non-finite floats to `None` before both the output model and the CSV write, and name
   which traits were coerced.** `cv` is `np.inf` whenever a certified trait's mean is exactly 0 —
   **genuinely reachable** through real `qc_clean` output: `qc_clean`'s zero-*fraction* threshold
   limits how many values are exactly 0, not whether the *mean* is 0, and no cleanup step rejects a
   zero-mean trait. `skewness`/`kurtosis` are `nan` for a zero-variance (constant) trait (SciPy
   divide-by-zero) — **but this half is defense-in-depth, not a reachable real-data case**: verified
   empirically (a synthetic constant column injected into the raw fixture) that
   `clean_traits_for_analysis` → `apply_data_cleanup_filters` unconditionally runs
   `remove_zero_variance_traits(min_variance=0.0)` as one of its own steps, and `qc_clean`'s
   `QCCleanParams` doesn't expose `min_variance` as an overridable field — so a genuinely constant
   trait can never survive into `frame.trait_cols` through real `qc_clean` output. The
   `skewness`/`kurtosis`=`nan` branch is therefore handled only against a hand-crafted/adversarial
   cleaned frame (e.g. a `FakeReader` seeded directly, bypassing the real pipeline), the same
   "delegate returns rather than raises" caution `qc_clean`/`remove_outliers` apply to their own
   guards — not because real `qc_clean` output can produce it. (An earlier draft of this document
   claimed both halves were "real, reachable" cases; corrected after review — only the `cv=inf`
   half is.)

   Python's `json.dumps` would emit a bare `Infinity`/`NaN` token by default for either case —
   invalid strict JSON, and a real risk if a downstream consumer of the MCP response uses a strict
   parser. Fields are typed `Optional[float]`; the tool maps `inf`/`-inf`/`nan` → `None` via a small
   helper (`float(x) if math.isfinite(x) else None`) applied uniformly to
   `mean`/`std`/`min`/`max`/`median`/`q25`/`q75`/`cv`/`skewness`/`kurtosis` before both
   `TraitStatistics(**...)` construction and the CSV row — so the persisted CSV and the inline
   summary agree (both show an empty cell / `None`, not `inf`/`nan` in one and not the other).
   Rather than leave a bare blank cell a scientist skimming a wide (possibly 600+ trait) CSV could
   miss — a zero-variance trait can itself be a signal of a sensor/pipeline bug worth noticing, not
   just a number to omit — the tool also names every trait that had at least one field coerced in
   a `nonfinite_stat_traits: list[str]` field, mirroring the `dropped_constant_traits` precedent
   `pca_analysis` already established for an analogous "the delegate silently changed something"
   case. Not exercised by the golden fixture (no zero-mean/zero-variance trait in turface_19's 19
   certified traits) — covered by a synthetic unit test instead, mirroring how `remove_outliers`
   tests its degenerate-guard branches beyond what the real golden reaches.
5. **`stats_per_trait` is a list of `{trait, ...}` objects, not a `dict[str, TraitStatistics]`.**
   Preserves trait order (delegate iterates `trait_cols` in the order given) and keeps the 50-cap a
   plain list slice (`rows[:50]`) rather than a dict-ordering assumption; matches the legacy
   workflow's row shape (`{"trait": ..., "n": ..., ...}`) so any code that parsed the old
   `stats.csv`/`summary.stats_per_trait` shape still recognizes the field names (`n` for count is
   kept, not renamed to `count`, for the same reason).
6. **The delegate's per-trait `{"error": "No valid data"}` branch is unreachable through a
   genuinely certified-clean selection, but handled anyway (defense-in-depth).** `qc_clean`
   guarantees zero NaN cells in its kept trait columns before it will commit a run
   (`qc_clean.py`'s own pre-commit guard) — so every certified trait has at least one non-NA value
   by construction. Handling the branch costs one `if "error" in r: failed.append(trait); continue`
   and avoids a `KeyError`/`internal_error` if a future reader implementation or a hand-crafted
   `FakeReader` cleaned frame ever violates that invariant. `n_failed`/`failed_traits` surface it
   rather than raising — consistent with treating it as data-quality information, not a
   caller error.
7. **The golden protects the MCP wrapper, and — unusually for this codebase's goldens — is also
   independently reproducible arithmetic, not merely a characterization snapshot.**
   `calculate_trait_statistics` computes mean/std/quantiles/skewness/kurtosis with plain
   pandas/SciPy calls — the same numbers `df[trait].describe()` + `scipy.stats.skew`/`kurtosis`
   would produce by hand. This is a narrower claim than it might first sound: like every golden in
   this codebase, it protects against a bug in **this tool's own code path** (wrong column
   selection/ordering, JSON/CSV serialization, cap/truncation logic, non-finite mishandling) — it
   is not an external cross-check of `calculate_trait_statistics` itself the way the PCA golden's
   cumulative-variance figure traces to an independently-recorded `sleap-roots-analyze#120`
   viz-metadata file from a *different* pipeline. There is no comparable external source for
   descriptive stats on turface_19. What makes it stronger than the PCA/clustering/heritability
   goldens (whose honest framing is "characterization snapshot, not an independent oracle" because
   reproducing them requires re-running the same library code under test) is that
   mean/std/quantiles/skewness/kurtosis are parameter-free textbook formulas with no model-fit
   degrees of freedom — so an independent by-hand computation from the raw CSV is a genuine,
   cheap cross-check, in the same spirit as `turface_19_qc_inspect_golden.json`'s
   "independently-computed" framing (per `tests/fixtures/README.md`) — **not**
   `turface_19_qc_golden.json`, which that same README itself labels a "characterization snapshot"
   (an earlier draft of this document cited the wrong one of the two; corrected after review).
8. **Re-verify finiteness of the certified selection before delegating, mirroring `pca_analysis`'s
   own guard.** `pca_analysis.py` asserts `np.isfinite(selected.to_numpy(dtype=float)).all()` before
   delegating, precisely because nothing enforces that invariant structurally — `frame.trait_cols`
   is derived by `detect_columns` (name/dtype matching), which never inspects NaN content; only
   `qc_clean`'s own pre-commit guard is what makes a certified trait NaN-free in practice, and
   nothing stops a future reader implementation (or a hand-seeded `FakeReader` in a test) from
   violating that. Without an equivalent check, a certified trait with a residual NaN would make
   `calculate_trait_statistics`'s own per-trait `dropna()` silently return `count < n_samples` with
   no signal — exactly the "no sample is silently lost" guarantee this tool's golden scenario
   asserts, undermined with no error raised. `descriptive_stats` adds the same `np.isfinite` guard
   over the selected trait columns before delegating, raising `BloomMCPError(assumption_violated)`
   (a genuine precondition violation, not a caller-fixable input error) rather than silently
   reporting an undercount.

## Golden fixture

`turface_19_stats_golden.json`: `calculate_trait_statistics` called directly (not through the
tool) on `clean_traits_for_analysis(turface_19_raw_data.csv, ...)` at **canonical-default**
thresholds (`max_zeros_per_trait=0.5, max_nans_per_trait=0.2, max_nans_per_sample=0.0,
min_samples_per_trait=10` — the same `_CANONICAL_*` constants `qc_clean` uses) — **158 samples,
19 kept traits** (all 19 non-`Barcode`/`geno`/`rep`/`Computation.Time.s` numeric columns survive
the looser 0.2 NaN-fraction threshold; this is the same pre-trim clean `remove_outliers`'s golden
documents as its 158-sample input, computed here independently on `sleap-roots-analyze==0.1.0a5`).
Recorded per-trait, e.g.:

```json
"Shoot_Biomass_mg": {"count": 158, "mean": 158.2860759493671, "std": 44.96525972299035,
  "min": 13.8, "max": 253.5, "median": 158.75, "q25": 132.65, "q75": 188.54999999999998,
  "cv": 0.28407590151754053, "skewness": -0.44548624755816857, "kurtosis": 0.2833162444945141}
```

`Root_Shoot_Ratio` is recorded too (`skewness ≈ 6.78`, `kurtosis ≈ 65.6`) — a deliberately
non-normal trait, kept in the golden as a reminder that these values are not expected to look
Gaussian and the tool must not silently clip/transform them. None of the 19 kept traits has
`mean == 0` or zero variance in this fixture, so Decision 4's non-finite coercion is not exercised
by the golden — see the synthetic unit test in tasks.md §3.

## Risks / Trade-offs

- **`require_clean=True` means an experiment with no `qc_clean` run yet cannot get descriptive
  stats**, even though the delegate could technically run on raw data. Mitigated by the structured
  `tool_error` naming the remedy (`run qc_clean first`), same code and UX as
  `pca_analysis`/`clustering`.
- **The 50-trait cap means a caller relying only on the inline response for a >50-trait experiment
  (e.g. cylinder) misses traits 51+** unless they read the persisted `stats.csv`. Mitigated by
  `truncated_in_summary` + `omitted_traits` (naming the cut traits) plus the `resource_link` to the
  full CSV — same trade-off the legacy workflow already made, not a regression.
- **A future upstream `calculate_trait_statistics` change to include a new statistic** (e.g. IQR)
  would silently not appear in `TraitStatistics` until the Pydantic model is updated. No mitigation
  needed now — same exposure every other consumer tool already has to its delegate's dict shape.

## Migration Plan

Pure addition; no existing tool, schema, or persisted-run shape changes. Steps: implement tool +
tests → register in the section → extend the live smoke → document. No rollback complexity beyond
reverting the addition.

## Open Questions

- None outstanding — the issue explicitly scopes this to a single delegate call with no
  method/seed/plot surface, so there is less design space than `remove_outliers`/`clustering` left
  to a reviewer to resolve.
