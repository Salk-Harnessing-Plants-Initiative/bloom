## Context

`heritability_analysis` is the 9th granular `sleap-roots-analyze` consumer in
`sections/sleap_roots/analysis/`. Unlike the 8 that preceded it, it does not merely *add* a
surface — it **replaces** two currently-registered tools, which is what makes this change breaking
and what makes the plot/number-consistency guarantee the central design constraint rather than a
nicety.

Shape it inherits rather than invents: `pca_analysis.py` (the reference consumer — `require_clean`,
`_validate_trait_subset(require_certified=True)`, `snapshot_frame`/`create_run`/`commit`, plot keys
validated *before* `create_run`, figures closed in `finally`); `descriptive_stats.py` (the
reference for a *wide* per-trait result — the 50-entry cap, per-trait failure routing, non-finite
coercion that names the affected traits); `tools/_plots.py` (`validate_plot_keys` /
`generate_figures` / `close_figures`); and `_viz_shared.save_plot_or_plots` (the existing
precedent that an upstream plotter may return `list[Figure]`).

**Goals:** per-trait H² as *data* in a versioned run with provenance; one delegate call feeding
numbers *and* plots; close the `require_clean` gap; delete the duplicated block rather than fix it
twice.

**Non-goals:** see proposal.md's "Explicitly out of scope" — the single source of truth for
deferrals. Not restated here.

## Decisions

### D1 — One delegate call feeds both the numbers and the figures

`calculate_heritability_estimates(...)` is invoked **exactly once** per tool call. The returned
dict is the single source for the `HeritabilityResult` (inline rows + `heritability_result.json`),
`heritability.csv`, `create_heritability_plot`, and — via `compare_trait_heritabilities` —
`create_variance_decomposition_plot`.

The issue's oracle asks for consistency "structurally guaranteed by sharing one code path, not
asserted after the fact". That is satisfied by there being no second call site to disagree with. A
test still pins call-count `== 1` even with both plot keys requested, because a future refactor
could reintroduce one — the test guards the structure, it is not the guarantee.

**One documented exception: row order.** `create_heritability_plot` sorts by H² **descending**
before paginating (`visualization.py`), while the returned/persisted tables preserve the resolved
`trait_cols` order. On a wide experiment the inline top-50 and the 50 bars on `_page1.png` are
therefore *different trait sets*. This is a real trap for a change headlined "figures and numbers
cannot diverge", so it is stated in the tool's own description and pinned by a test rather than
left for a caller to discover.

*Alternative rejected:* keep two thin plot tools reading a persisted `heritability_result.json`.
It keeps two tool surfaces, needs a run-resolution rule, and still lets a caller plot v1's numbers
beside v2's.

### D2 — Consumer semantics: `require_clean=True`, `tool_error` on a missing clean

Matches `pca_analysis` / `clustering` exactly, including the error **code** (verified in source:
`pca_analysis.py` and `clustering.py` both raise `code="tool_error"` here; `remove_outliers.py`
uses `assumption_violated` for its own version of this guard, and is deliberately not the model).

This is the fix for the missing-`require_clean` defect: the delegate does
`subset = df[[trait, genotype_col]].dropna()` per trait. On raw data that silently changes `n` per
trait. On a certified-clean frame the selection is already NaN-free, so the `dropna()` is a genuine
no-op over the sample set `qc_clean` certified — the same argument `pca_analysis` makes.

### D3 — Genotype required; replicate optional (and this is load-bearing, not marginal)

Both retired tools gated on `if not genotype_col or not replicate_col`. Upstream documents
`replicate_col` as optional, with its values never entering the model (`value ~ 1 + (1|genotype)`)
and `required_cols = [genotype_col]` unless a truthy replicate name is passed
(talmolab/sleap-roots-analyze#142). Verified empirically: the same cleaned turface_19 frame
analyzed with `replicate_col="rep"` and with `replicate_col=None` yields **bit-identical** H² for
all 19 traits.

The loosening is not an edge-case courtesy. `SupabaseReader` hard-codes `replicate_col=None` on
every frame it produces (there is no replicate constant in that adapter at all), so requiring a
replicate column would make **every DB-backed experiment unanalyzable** by this tool. A reviewer
should treat D3 as a precondition of the tool working at all, not as an optional nicety. Corollary:
the replicate-equivalence test is only exercisable through `FakeReader`, never live.

The repo already settled the underlying question — `bloommcp/docs/data-access-roadmap.md` records
it as closed 2026-06-10 ("heritability groups by genotype, not replicate"). Cited once here rather
than re-argued in the spec.

A missing/empty `frame.genotype_col` → `assumption_violated` (nothing in the *request* is wrong;
the caller cannot fix it by changing a parameter), naming the roles the reader resolved.
`tool_error` stays reserved for the missing-cleaned-version precondition, which has a concrete
in-tool remedy.

### D4 — One `threshold`, forwarded everywhere

`threshold: float = 0.5` (`ge=0.0, le=1.0`) drives `from_heritability_dict` (each trait's
`passed_threshold`, and `n_above_threshold`), `create_heritability_plot(threshold=...)` (whose own
default is also `0.5`), and `create_variance_decomposition_plot(threshold=...)` — **explicitly
passed**, because that plotter's own default is `0.3`. Leaving it implicit would ship a figure
whose reference lines contradict the `passed_threshold` column in the same run's CSV.

Upstream's `from_heritability_dict` warns that `threshold` is recorded "on trust" — the source dict
does not carry it. Passing the tool's single parameter to all three consumers is what makes that
trust well-founded.

### D5 — Scrub on **missing or non-finite**, before building the result

Two distinct hazards, one mechanism.

**(a) Missing keys silently become zeros.** `HeritabilityResult.from_heritability_dict` does
`float(entry.get("var_genetic", 0.0))`, `float(entry.get("var_residual", 0.0))`,
`int(entry.get("n_genotypes", 0))`. Verified: an entry whose `var_genetic` was renamed upstream
produces `var_genetic == 0.0` **silently**. On the default `include_plots=False` path, D7's
variance-component guard never runs, so a renamed key would land as a plausible zero variance
component in `per_trait`, `heritability.csv`, and `heritability_result.json`. This is exactly what
`bloommcp-packaging`'s "a renamed or dropped key SHALL fail rather than silently defaulting to
zero" forbids — so **key presence is validated on every path, not only the plot path**.

**(b) Non-finite floats abort the whole run.** `HeritabilityResult.to_json()` defaults to
`allow_nan=False` and **raises `ValueError`** on any non-finite float. Catching that after the fact
would abort a run whose other 800 traits were fine.

Mechanism for both: before calling `from_heritability_dict`, each per-trait entry is checked for
`heritability` / `var_genetic` / `var_residual` / `n_genotypes` / `n_observations` / `model_type`
being **absent**, and for the numeric ones being **non-finite**. Either way the entry is replaced —
in a *copy* of the dict — with `{"error": ...}`, so the trait lands in
`HeritabilityResult.failed_traits` through the delegate's own routing, with no parallel
bookkeeping. Non-finite cases are additionally named in `nonfinite_traits` (mirroring
`descriptive_stats`' `nonfinite_stat_traits`). The original dict is not mutated, matching
`from_heritability_dict`'s own "does not mutate `d`" contract.

Consequences: `mean_h2` / `n_above_threshold` are finite by construction; `to_json()` cannot raise;
both plotters see the same scrubbed dict, so a figure cannot plot a bar the returned numbers
disowned (`create_heritability_plot` skips out-of-`[0,1]` values on its own, but
`create_variance_decomposition_plot` would happily render a NaN column).

**Correction to an earlier draft of this decision.** It claimed a NaN `heritability` survives the
delegate's clamp. It does not: `max(0, min(1, nan))` evaluates to **`1`** in Python (`min(1, nan)`
returns `1`, since every comparison with `nan` is False). So a non-finite `heritability` is
reachable only by monkeypatch — the test is still writable, but the *rationale* was wrong.

**The real hazard the clamp creates is worse — and is now *labeled* locally, though still not
fixed here.** With `var_genetic == 0.0` and `var_residual == 0.0` (both finite),
`heritability = var_genetic / (var_genetic + var_residual / mean_n_reps)` is
`0.0 / np.float64(0.0)` → `nan` (a RuntimeWarning, not a `ZeroDivisionError`, because
`mean_n_reps` is a numpy float) → clamped to **`1.0`**. A degenerate fit therefore emits a
*perfect* heritability with all-finite components, which no finiteness scrub can catch.

Correcting the value would mean re-deriving the delegate's arithmetic inside a thin wrapper, which
this tool does not do. But **classifying** it costs nothing: the scrub loop already reads
`var_genetic` and `var_residual` off every entry, so it also collects the traits where both are
exactly `0` into `zero_variance_traits` (a review finding — the earlier draft left a scientist
reading `mean_h2` with no signal at all). Those traits stay in `per_trait` and in the persisted
table, and still contribute to `mean_h2` / `n_above_threshold` — deliberately, so the returned
aggregates cannot drift from what a reader of `heritability_result.json` would compute — but they
are named, and the tool's description tells a caller to check the field before quoting either
aggregate.

Two things make this worth having rather than theoretical:

* **The same condition has a second, genuinely reachable branch.** Verified against the real
  delegate: a literally constant column takes its `no_variance` path, which reports
  `h2 = 0.0` with both components `0.0` and `model_type="no_variance"`. So the *same* non-finding
  ("no variance to partition") surfaces as `0.0` down one branch and `1.0` down the other. A
  caller cannot be expected to know which they got; hence one list covering both.
* **It is reachable through a real pipeline, not only a hand-crafted frame.** `qc_clean` strips
  zero-variance traits, but `remove_outliers` trims *rows* from an already-cleaned version, and a
  trait whose variance lived only in the trimmed samples is constant in what survives.

Exact `== 0`, not a tolerance: that is the condition making the denominator vanish. Verified that a
near-constant column fits at ~1e-19 rather than 0 and yields an ordinary quotient, so a tolerance
would relabel working estimates as non-findings.

The underlying clamp remains an upstream defect — recorded here and filed as a follow-up
(tasks.md §10.4), not worked around by second-guessing the delegate's own arithmetic.

### D6 — `generate_figures` learns `list[Figure]`

`create_heritability_plot` returns a single `Figure` at ≤ `traits_per_page` (50) traits and a
`list[Figure]` above it. turface_19's 19 traits never reach it; cylinder's 846 do — the same
threshold `_viz_shared.save_plot_or_plots` was added for in #483.

`generate_figures` currently assigns `figures[key] = fn()` and hands that to `apply_font_style` and
later `close_figures`, all of which assume a `Figure`; a list would make `close_figures` call
`plt.close(list)` and leak every page.

**Chosen:** expand a list return into `figures[f"{key}_page{i}"]` (1-indexed), leaving a scalar
return keyed by `key` exactly as today. `close_figures` and `apply_font_style` keep operating on a
flat `dict[str, Figure]`, and each page persists as `<key>_page<N>.png` — the shape
`save_plot_or_plots` already produces. Detection is `isinstance(result, list)`, deliberately not a
duck-typed `__iter__` check: the existing helper tests pass string sentinels (`lambda: "fig_a"`),
which an iterable check would silently shred into per-character pages.

**Record every page before styling any page.** The module's documented invariant is that a figure
is recorded into `figures` *before* `apply_font_style` touches it, so a styling failure still
leaves it reachable by the caller's `finally`. A naive per-page interleave
(`record N → style N → record N+1`) satisfies that only for pages ≤ N: if styling page 3 of 17
raises, pages 4–17 were already produced by `fn()`, are live in the pyplot registry, were never
recorded, and are unreachable. So: record all pages, then style them in a second pass. Currently
masked (styling is a no-op while both kwargs are `None`, and D9 defers style kwargs) — but D9's
follow-up is precisely the wiring that unmasks it. Cheap to get right now.

*Alternatives rejected:* forcing one page via `traits_per_page=len(traits)+1` (an unreadable
846-bar figure); calling the paginating plotter outside `generate_figures` (loses the shared
validation/cleanup path); storing the list under one key (pushes the special case into three call
sites instead of one).

**In-flight interaction with PR #726 (#721)** — larger than a textual conflict, and detailed in
tasks.md's rebase note. Three parts: (i) #726 wraps this same loop in a `FIGURE_REGISTRY_LOCK` and
an allocate-then-raise `plt.get_fignums()` diff — that diff becomes *more* valuable with lists (a
plotter that allocates 11 pages then raises on page 12 gets all 11 closed), and the merge is
"keep #726's wrapper, apply the expansion to `fn()`'s return inside it, record-then-style";
(ii) #726 also edits **both modules this change deletes**, producing modify/delete conflicts;
(iii) #726's lock docstring enumerates "the 5 legacy `plot_*` tools … must acquire this SAME lock"
by name, which this change falsifies — the new tool is covered because it goes through
`generate_figures`, which holds the lock, and that comment must be corrected by whichever lands
second.

Because #726 holds that lock across the whole `resolved_calls` loop, `compare_trait_heritabilities`
and D7's guard must run **before** `generate_figures`, with only the finished `comparison_df`
captured in the closure — otherwise that work blocks every concurrent figure-creating call in the
process. That ordering is required by D7's "no run committed on a bad frame" guarantee anyway. The
lock is also non-reentrant, so the new tool must not route figures through `_viz_shared`'s
save helpers (which the 3 surviving plot tools still use): `generate_figures` → a lock-acquiring
helper would self-deadlock.

### D7 — `compare_trait_heritabilities` is computed lazily, and its guard is kept

The variance-decomposition figure needs `compare_trait_heritabilities(df, traits, h2_results, ...)`.
It is computed **only** when `create_variance_decomposition_plot` is in the resolved plot set —
never on the default path, and not merely because `include_plots=True` selected the bar plot alone.

**Correction to an earlier draft:** that laziness was justified as avoiding a cost that "roughly
doubles" the heritability computation. That is wrong. `analyze_trait_variance`, which
`compare_trait_heritabilities` calls per trait, contains no mixed model at all — it is groupby
arithmetic. Measured on the real 846-trait cylinder frame: `calculate_heritability_estimates`
**10.77 s**, `compare_trait_heritabilities` **0.36 s** (~3%). Laziness is still correct — don't
compute what wasn't asked for — but it is a tidiness decision, not a performance one, and nothing
else in this proposal may lean on the disproved cost claim.

Two behaviors are carried over verbatim from the retired tool, because both were correct: rows
whose `heritability` is NaN are dropped before plotting; and a **scored** trait whose
`var_genetic`/`var_residual` is NaN raises rather than rendering a zero-filled bar. In the retired
tool that returned an error string; here it is `BloomMCPError(code="assumption_violated")` naming
the offending traits, raised before `create_run`, so **no run is committed**. This is the plot-path
half of `bloommcp-packaging`'s zero-fill obligation; D5 covers the default path.

If every trait fails and the comparison frame is empty, the decomposition figure is skipped and the
unscored trait names are reported — an empty figure is not a useful artifact, and a silent omission
would be worse, so the result names the reason. (`create_heritability_plot` handles its own
no-plottable-trait case by returning a placeholder figure; not special-cased here.)

### D8 — Tool class `heritability`, and its discovery gap

`"heritability"` is already in `manifest.CANONICAL_TOOL_CLASSES` but **not** in
`list_existing_analyses.TOOL_CLASSES` — the same gap bloom#669 just closed for `"pca"`, `"umap"`,
`"qc_inspect"`. Without adding it, runs persist correctly but are structurally undiscoverable
through `list_existing_analyses`, and a `list_runs` failure for the class is never surfaced. So it
is added to that tuple and to `_TOOL_CLASS_TO_PUBLIC_NAME` (`"heritability" ->
"heritability_analysis"`), per the bloom#664 pattern. No `manifest/__init__.py` edit is needed.

Unlike `"stats"` (reactivated from the retired `run_descriptive_stats_workflow`, which really did
write runs), no retired Phase-1 workflow ever persisted under `"heritability"` — the retired
heritability surface was the two *plot* tools, which wrote loose PNGs to `PLOTS_DIR` and no
manifest entry. So this tool starts a fresh version lineage. Task 1.4 verifies that against a dev
stack rather than assuming it; if no stack is available it stays an explicitly stated assumption
rather than a silent one.

### D9 — Font/style kwargs deferred, not half-wired

`apply_font_style` takes one `Figure`. With D6's expansion, wiring `plot_font_family` /
`plot_font_size` would be nearly mechanical — but neither the issue nor #661 asks for it here, and
#661's own spec enumerates the tools that carry those params. Adding a third would widen an
already-breaking change for no requested benefit. Deferred, with the enabling refactor landed and
its leak hazard (D6) fixed in advance.

## Risks / Trade-offs

- **Breaking two registered tool names.** Any saved prompt, script, or Claude Desktop flow calling
  them fails with an unknown-tool error. → The issue explicitly accepts this; the migration table
  ships in the tool's own description (where a failed caller looks next) and the connect guide; the
  replacement is strictly more capable. No deprecation shim — a shim would have to either require a
  clean (breaking anyway) or keep the raw-data path this change exists to close.
- **The replacement requires `qc_clean` first.** That is the point (D2); the error carries the
  remedy.
- **`_plots.py` is shared** with `pca_analysis`, `umap_analysis`, `clustering`. → The change is
  additive (a scalar return takes the identical path it does today); `test_plots_helpers.py` pins
  both branches plus the unchanged scalar key naming, and each consumer's existing plot tests are
  the backstop. Extracting it into its own PR (tasks.md C3) makes it independently reviewable and
  removes the #726 conflict.
- **Three in-flight PRs touch these files** (#724, #683, #726). → Sequencing recorded in
  proposal.md's Impact and tasks.md's rebase note, with #724's module-level imports of both retired
  modules called out as the one that fails *collection*, not just a test.
- **The golden is a characterization snapshot, not ground truth.** turface_19 has no externally
  validated per-trait H². → Labeled as such in the golden's `_source` and in
  `tests/fixtures/README.md`, consistent with every other heritability golden here. It gates drift,
  and it does gate this tool's own code path (selection, ordering, threshold classification,
  serialization, cap, failure routing) end to end. Tolerance follows `test_oracle.py`'s `_H2_TOL`
  (`1e-5`) with an absolute floor, because the fixture contains a trait at H² ≈ 7.7e-09 where no
  relative tolerance is meaningful.
- **A degenerate zero-variance fit reports H² = 1.0** (see D5's correction). Upstream defect,
  recorded and filed, not worked around here.

## Migration Plan

1. Land on `staging` behind normal PR review (no feature flag — the deployed tool surface is the
   release unit; bloommcp is not a published package, so there is no version/changelog gate).
2. The migration table's canonical homes are the tool's `tools/list` description and
   `bloommcp/docs/connecting-claude-code.md`; the PR body repeats it under `## Breaking Changes`.
3. `openspec archive add-bloommcp-heritability-analysis-tool` after deploy — **after**
   `devendor-bloommcp-analysis` and `fix-bloommcp-experiment-identifier-wording`, or after
   amending their deltas (tasks.md §7.13).

Rollback: revert the PR. Commits C4 (add) and C5 (retire) are a rollback pair — reverting C4 alone
would leave the retirement standing with no replacement. No persisted-data migration: the retired
tools never wrote to the result store; the only artifacts orphaned are loose PNGs under
`PLOTS_DIR`, referenced by no manifest.

## Open Questions

- None blocking. One deliberate deferral (D9), one behavior change called out for reviewer sign-off
  rather than assumed (D3), and one upstream defect recorded for a follow-up rather than
  worked around (D5's clamp correction).
