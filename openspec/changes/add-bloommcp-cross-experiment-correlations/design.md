## Context

Every granular consumer shipped so far (`pca_analysis` #308, `clustering` #309/#422,
`remove_outliers` #378, `umap_analysis` #425) takes exactly one `experiment` filename
and consumes it through `ExperimentReader.load_experiment(require_clean=True)`. The
shared persistence primitives are shaped around that single-experiment call:

- `ResultStore.create_run(*, experiment: str, tool_class: str, provenance, user_label=None, source_csv: Optional[Path]=None)`
- `Provenance.based_on_version: str` — one consumed source version
- `AnalysisDir(output_root, experiment_filename, tool_class)` — derives its storage
  prefix as `Path(experiment_filename).stem`, and content-addresses exactly one
  `source_csv` via `input_sha256`

Issue #489 explicitly names this as one of the two real contract mismatches: "Every
current bloommcp granular tool ... takes one `filename`. A cross-experiment tool needs
two — a genuinely new interface shape." This design resolves that mismatch, plus
confirms the correct upstream delegation surface (the issue's own suggested
implementation sketch has one factual gap, corrected below).

## Goals / Non-Goals

**Goals:**
- One new tool, `cross_experiment_correlations`, delegating all correlation math to
  tested `sleap_roots_analyze.cross_experiment_analysis` entry points.
- Both experiments consumed through the existing `ExperimentReader` port
  (`require_clean=True`) — no direct `pd.read_csv`, no bypass of Supabase-backed
  reads.
- A versioned, traceable persisted run despite the two-experiment input, without
  changing `ResultStore`, `Provenance`, or the manifest schema.

**Non-Goals:**
- Extending `Provenance`/`StoredRun`/`ResultStore` to a first-class multi-experiment
  shape. One dual-input consumer does not justify a schema change touching every
  existing tool's provenance record (OpenSpec's own complexity-trigger guidance:
  add structure only with "multiple proven use cases requiring abstraction"). Revisit
  if a second dual-experiment tool appears.
- `calculate_per_trait_correlations`, `calculate_cross_experiment_correlations_extended`,
  `calculate_correlation_confidence_intervals`, any plotting function, power analysis,
  or redundant-trait clustering — see proposal.md's deferred list.
- Re-deriving `pc_correlations`/`cross_platform_prediction` — issue #489's own
  exclusion, unchanged here.

## Decisions

### D1 — Two experiments, one single-experiment-shaped persisted run

The `experiment`/`based_on_version`/`source_csv` fields stay single-string/single-path
as declared on the port; this tool encodes both experiments into them rather than
changing the shared types:

- `experiment=` — a synthetic composite key,
  `f"{Path(experiment_1).stem}__x__{Path(experiment_2).stem}"`, passed to
  `create_run`. `AnalysisDir` only ever uses this value as `Path(...).stem` for a
  storage-prefix string (`bloommcp/src/bloom_mcp/manifest/analysis_dir.py:33`) — it is
  never validated against a real raw filename, so a synthetic key produces a normal,
  readable prefix (`cross_experiment_correlation_cylinder_traits__x__turface_traits/`)
  with zero port changes.
- `based_on_version=` — a composite string recording both consumed cleaned-version
  labels, `f"{experiment_1}@{frame1.source}|{experiment_2}@{frame2.source}"` (e.g.
  `cylinder_traits.csv@v3_cleaned|turface_traits.csv@v2_cleaned`). Documented as a
  deliberate encoding of a single-string field, not a data-model change; filenames and
  version labels never contain `@`/`|`, so this round-trips unambiguously for a human
  reader (it is not meant to be machine-parsed by any existing consumer of
  `based_on_version`).
- `source_csv=` — both consumed frames' selected trait columns, concatenated with a
  leading `_experiment` label column (`1`/`2`) into one temporary combined CSV, so
  `AnalysisDir.input_sha256` content-addresses **both** inputs' bytes in a single hash
  rather than covering only one side.

**Alternative considered and rejected:** extend `Provenance.based_on_version` and
`StoredRun`/`create_run(experiment=...)` to accept a list of experiment/version pairs.
This is the more "correct" long-term shape, but it touches the contract layer every
other tool depends on, for a single consumer — high blast radius for a capability that
does not yet have a second use case. `devendor-bloommcp-analysis` itself declared
"changing the contract layer, ports, or storage" a Non-Goal; this proposal holds that
line. If a second two-experiment tool is proposed later, revisit this decision then
with two real call sites to design against instead of one speculative one.

**Requires reviewer sign-off** — this is a deliberate string-encoding trade-off on a
shared field's meaning, in the same spirit as D3 in `devendor-bloommcp-analysis/design.md`
(a naming-convention bend that required explicit sign-off before landing).

### D2 — Genotype-means via upstream `calculate_genotype_means`, not a bespoke `.groupby().mean()`

Issue #489 suggests computing genotype-means "locally (`.groupby(genotype_col)[trait_cols].mean()`
— a simple pandas operation, no upstream dependency needed for this step)". That undercounts
the actual contract: `calculate_cross_experiment_correlations` reads
`exp1_means.loc[g, "n_samples"]` / `exp2_means.loc[g, "n_samples"]` internally to apply
its own `min_samples` filter (`cross_experiment_analysis.py:1013-1019`). A bare
`.groupby().mean()` has no `n_samples` column, so calling the delegate on it would raise
a `KeyError` inside upstream code — an opaque failure, not a clean `assumption_violated`.

Upstream already provides exactly the right shape:
`calculate_genotype_means(df, trait_cols, genotype_col) -> DataFrame` groups, means, and
appends `n_samples` in one call (`cross_experiment_analysis.py:702-721`) — also public,
also not in the issue's excluded list (only `load_and_align_experiments` is excluded).
Using it keeps the "delegate all math to upstream, no reimplementation" invariant every
other granular tool holds (see `pca_analysis`'s and `clustering`'s docstrings) and avoids
a bespoke aggregation whose column contract could silently drift from what the delegate
expects.

### D3 — `calculate_correlation_confidence_intervals` deferred, not worked around

`calculate_correlation_confidence_intervals(correlation_df, n_genotypes, confidence=0.95)`
applies one scalar `n_genotypes` to **every row** via `.apply(lambda r: calculate_correlation_ci(r, n_genotypes, confidence))`
(`cross_experiment_analysis.py:1596-1638`). But `correlation_df` already carries a
**per-row** `n_genotypes` column from `calculate_cross_experiment_correlations` — each
trait pair does its own NaN-alignment inside `_prepare_aligned_values`, so the number of
genotypes actually paired can legitimately differ row to row. Passing one global scalar
to a Fisher-z confidence-interval computation for every row would silently produce a
wrong CI width for any row whose real aligned N differs from the passed value — the same
class of bug bloommcp already found and documented in `clustering.py`'s
`_gmm_selected_scores` (upstream returning the wrong candidate's BIC/AIC on GMM
auto-select).

Two options: (a) call it once per distinct `n_genotypes` value present in the row and
merge — plausible but adds real complexity for a function this proposal is not
otherwise obligated to ship; (b) defer it entirely. Choosing **(b)**: confidence
intervals are not part of the issue's minimum ask ("delegate the actual correlation
math + significance testing"), and baking in an interpretation of a genuinely
ambiguous upstream signature now — before confirming with upstream whether the flat-`n`
behavior is intentional — risks the same "silently changes numbers" failure mode #489
itself was raised to avoid. Filed as a natural follow-up once resolved (either upstream
fixes/clarifies the signature, or bloommcp deliberately implements option (a)).

### D4 — Deterministic tool, no seed

The delegation chain (`calculate_genotype_means` → `calculate_cross_experiment_correlations`
→ `identify_significant_correlations` → `summarize_correlation_results`) is Pearson
correlation + Benjamini-Hochberg FDR — no `random_state` anywhere in the chain. The tool
declares no `seed` parameter and records `seed = None` in `Provenance`, matching
`pca_analysis`'s convention (not `clustering`'s stochastic kmeans/gmm convention).

### D5 — Required genotype role on both sides

`pca_analysis`/`clustering` treat `frame.genotype_col` as optional (used only for plot
coloring where present). This tool's correlation is computed **at the genotype-mean
level**, so a missing genotype role on either experiment means the tool cannot produce
a meaningful result at all — not a degraded one. Both `frame.genotype_col` values are
checked non-`None` before any computation; a `None` on either side raises
`BloomMCPError(code="assumption_violated")` naming which experiment lacks a resolvable
genotype column, with a remedy pointing at `qc_clean`'s `genotype_column` override.

### D6 — Degenerate vs. empty-but-valid results

- `calculate_cross_experiment_correlations` returning zero rows (no trait pair reached
  `min_samples` aligned genotypes) is treated as **degenerate input** —
  `BloomMCPError(code="assumption_violated")` with a remedy to lower `min_samples` or
  check genotype overlap between the two experiments, no run persisted. This mirrors
  `pca_analysis`/`clustering`'s treatment of a delegate-signaled degenerate fit.
- `identify_significant_correlations` returning zero rows (correlations exist, none
  clear `r_threshold`/`p_threshold`) is a **normal, non-error outcome** — the tool
  reports `n_significant = 0` and still persists an (empty-but-schema-consistent)
  `significant.csv`. Note upstream returns a bare `pd.DataFrame()` with **no columns**
  in this case (`cross_experiment_analysis.py:1571`), so the tool writes a fixed-header
  empty frame rather than the columnless one, keeping `significant.csv`'s shape
  consistent across calls regardless of how many rows survive.

### D7 — `summarize_correlation_results` through the existing JSON-serialization seam

`summarize_correlation_results` returns numpy scalar types (`.sum()`/`.max()`/`.nunique()`
results) and nested Python containers with numpy leaves. `devendor-bloommcp-analysis`
D1 already established the call-site pattern for this exact situation —
`sleap_roots_analyze.data_utils.convert_to_json_serializable` (imported via the
submodule path, not `__all__`) — for `convert_to_json_serializable`'s existing callers.
This tool reuses the identical import path and pattern rather than writing a new numpy
JSON encoder.

## Risks / Trade-offs

- **String-encoded composite fields are a readability/parseability trade-off**, not a
  type-safe one — mitigated by D1's "human-readable, not machine-parsed" framing and
  by not being the first bloommcp precedent for bending a single-string field's literal
  meaning (D3 of `devendor-bloommcp-analysis` already bent a naming convention with
  documented sign-off).
- **A second two-experiment tool would strain D1's encoding further** — mitigated by
  scoping D1 explicitly to "revisit with two real call sites," not designing an
  abstraction now against one.
- **Deferring `calculate_correlation_confidence_intervals` (D3) leaves a capability gap**
  relative to the old `run_cross_experiment_correlations` tool, which did not compute CIs
  either (only Pearson r/p, FDR-correction, and a summary) — so this is not a regression
  against the tool being replaced, only against the full upstream surface.

## Migration / Rollout

No data migration — a new tool, additive registration. No existing tool's behavior,
schema, or persisted-run shape changes. `test_devendor_invariants.py`'s retired-tool
list and drift guards are unaffected (this is a new tool, not a repoint of a retired
one).

## Open Questions

- Should `experiment_1`/`experiment_2` order be normalized (e.g. lexicographic) before
  building the composite `experiment=` key, so `(A, B)` and `(B, A)` land in the same
  storage prefix? Leaning **no** for v1 — `exp1_trait`/`exp2_trait` column naming in the
  persisted `correlations.csv` is order-dependent (asymmetric: exp1's traits are rows,
  exp2's are columns in spirit), so normalizing the storage key while leaving the
  content order-sensitive would be its own inconsistency. Flagging for reviewer input
  rather than deciding unilaterally.
- D1's reviewer sign-off — who signs off on the composite-string persistence encoding
  (mirrors the `devendor-bloommcp-analysis` D3 precedent of requiring an explicit
  approver for a convention bend)?
