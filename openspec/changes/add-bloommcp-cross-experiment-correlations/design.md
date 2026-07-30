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
implementation sketch has one factual gap, corrected below) and a second, more
consequential upstream defect discovered while verifying `min_samples`' actual
behavior (D8).

This revision incorporates a 5-lens adversarial review (spec quality, code/architecture
feasibility, GitHub issue alignment, TDD/testing, scientific rigor) conducted before
implementation. Every BLOCKING and IMPORTANT finding from that review is resolved
below (D8–D13); SUGGESTION-tier findings are folded into the affected sections
directly (spec scenarios, tasks).

**Second revision, post-PR code review.** After implementation, two independent
5-agent PR reviews (posted within a second of each other) each verified the code
against real infrastructure rather than trusting prose, and found two genuine
BLOCKING defects the pre-implementation review couldn't have caught (one only
observable in live CI, one requiring a specific input the test fixtures never
exercised): the PR's own live smoke test was failing (D-fix below), and the
composite-key scheme from D1 silently truncated for any dotted experiment filename
(D1 updated below). Every BLOCKING/IMPORTANT finding from both reviews is resolved
in this revision — see D1's update, D14, and the "Testing infrastructure fix" note
under Migration/Rollout.

**Third revision, post-fix re-review.** A follow-up review of the fix commit itself
found that the D1 fix's *own* sanitizing approach (`_storage_safe_stem`, dots ->
underscores) reopened the identical composite-key collision class one level down —
confirmed by reproduction, not just asserted; see D1's further update. That review
also found: `based_on_version`'s builder still hardcoded bare `@`/`|` literals rather
than the named constants the reserved-character guard already used (now unified);
`_validate_experiment_name`'s error message never named which of `experiment_1`/
`experiment_2` it was validating (now takes an optional `label`, D14 updated); the
golden fixture's `min_samples_3_upstream_no_op` block was recorded but never actually
read by any test (D8 updated); and the "benefits every `RunLinks`-based tool" claim
about the live-smoke fix was itself untested beyond this one tool (Testing
infrastructure fix note updated, `test_pca_analysis_smoke` extended). Every
BLOCKING/IMPORTANT finding from this pass is resolved in this revision.

**Fourth revision, re-review of commit a5ec16d.** That re-review found a5ec16d's *own*
fix for the composite-key collision — rejecting a stem containing the separator
substring — was itself insufficient: the separator's internal repetition lets two
distinct, individually-valid stem pairs join to an identical composite string via a
boundary straddling the separator (confirmed by direct construction and a hand-run,
ad-hoc randomized check at design time — see the "Fifth revision" note below for why
that check needed to become a committed test, not just a claim). This was the third
consecutive round finding a variant of the same collision class from patching guard
conditions on a string-concatenation encoding rather than fixing the encoding
structurally; this revision (commit f199126) replaces the guard-based approach with a
length-prefixed, provably injective encoding (see D1's "Fourth guard needed"/"Final
fix"). That re-review also found: this tool's own OpenSpec `spec.md` had not been
updated after the a5ec16d fix and described the superseded sanitizing behavior as
current (now corrected), and the golden-fixture regression test added in a5ec16d
omitted a `p_value` assertion present in the same golden block it otherwise checks (now
added). Every BLOCKING/IMPORTANT finding from that re-review is resolved in this
revision.

**Fifth revision, re-review of commit f199126.** That re-review found four things: (1)
the `CrossExperimentCorrelationsParams.experiment_1`/`experiment_2` field descriptions
still claimed the stem "must not contain... `__x__`" — a restriction f199126 itself
removed, so the MCP tool's own schema overclaimed a constraint to callers/agents that no
longer existed in the code (now corrected); (2) the "500k-sample randomized stress test"
this document and the `f199126` commit message cited was never actually committed to
the test suite — only the 256-pair hardcoded round-trip table was (now closed with two
`hypothesis`-based property tests, `test_composite_key_injective_property` and
`test_composite_key_distinct_pairs_never_collide`, each running 1000 generated examples
per test run); (3) *this document's own f199126-era edit introduced a fresh instance of
the exact inconsistency it was fixing elsewhere* — it relabeled some findings as
belonging to a "third (PR) review pass" using two different, one-off ordinal counters
that silently disagreed with each other and with `tasks.md`'s own commit-anchored
section numbers (a "review pass" counter that starts at the pre-implementation review,
and a separately-incremented "PR review pass" counter that doesn't — both appeared in
this document, offset from each other by one, which is exactly the kind of fragile
scheme that produces this class of drift on every subsequent edit); and (4) `tasks.md`'s
test-count bookkeeping for f199126 didn't arithmetically reconcile. Given that ordinal
"Nth review pass" labels have now drifted incorrectly at least twice across four
revisions of this same document, this revision removes them everywhere in favor of
naming the actual commit being re-reviewed (`c649f9d`, `a5ec16d`, `f199126`) — a
reference that cannot silently drift out of sync the way a manually-incremented counter
can. Every finding from this re-review is resolved in this revision.

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
  or redundant-trait clustering — see proposal.md's deferred list and the scope
  decision immediately below.
- Re-deriving `pc_correlations`/`cross_platform_prediction` — issue #489's own
  exclusion, unchanged here.

### Scope decision: one tool, not a "small tool family"

Issue #489's Ask explicitly offers either "a granular cross-experiment-correlation
tool (**or small tool family**)". This proposal chooses **one tool**, covering
genotype-mean-level correlation + FDR significance + summary, and defers three
upstream-backed capabilities the issue also names: `calculate_per_trait_correlations`
(a different granularity — individual-sample-level, single trait pair, not
genotype-mean-level), `calculate_cross_experiment_correlations_extended` (multi-statistic
mean/median/std combinations, needs `calculate_genotype_statistics` first), and
`calculate_correlation_confidence_intervals` (deferred for a substantive reason — D9,
not scope discipline).

Rationale for choosing one tool now rather than a family: the three deferred
capabilities are each a distinct, independently-shippable unit of work (different
granularity, different upstream inputs, or blocked on an unresolved upstream question)
— bundling them into this proposal would couple their review and merge timing to this
one's, and this one already carries the two-experiment architectural question (D1)
that needs to land and prove itself before more surface builds on top of it. This
mirrors how `pca_analysis` shipped without plots first, with `include_plots` landing
in a later, separately-reviewed PR (#426) rather than in the same change.

This is a scope decision, not silent scope-cutting: each deferred capability is named
explicitly (here and in proposal.md) rather than left implicit, so it is not lost.
Reviewer input welcome on whether any of the three should be pulled into this same
change instead of following it.

## Decisions

### D1 — Two experiments, one single-experiment-shaped persisted run

The `experiment`/`based_on_version`/`source_csv` fields stay single-string/single-path
as declared on the port; this tool encodes both experiments into them rather than
changing the shared types:

- `experiment=` — a synthetic composite key, built by `_composite_experiment_key(stem_1,
  stem_2)` (a length-prefixed, provably injective join — see below for why; the field
  name it lived under changed twice across review rounds, most recently
  `_storage_safe_stem`, now removed), passed to `create_run`. `AnalysisDir` only ever
  uses this value as `Path(...).stem` for a storage-prefix string
  (`bloommcp/src/bloom_mcp/manifest/analysis_dir.py:33`) — it is never validated
  against a real raw filename, so a synthetic key produces a normal, readable prefix
  (`correlation_10_turface_19__x__cylinder/` — see D9 for why the tool-class segment
  reads `correlation`, not `cross_experiment_correlation`) with zero port changes.
- `based_on_version=` — a composite string recording both consumed cleaned-version
  labels, exactly `f"{experiment_1}@{frame1.source}|{experiment_2}@{frame2.source}"`
  (e.g. `cylinder_traits.csv@v3_cleaned|turface_traits.csv@v2_cleaned`). Documented as
  a deliberate encoding of a single-string field, not a data-model change; it is not
  meant to be machine-parsed by any existing consumer of `based_on_version`.
- `source_csv=` — both consumed frames' selected trait columns, concatenated with a
  leading `_experiment` label column (`1`/`2`) into one temporary combined CSV, so
  `AnalysisDir.input_sha256` content-addresses **both** inputs' bytes in a single hash
  rather than covering only one side.
- **Guard (added during the pre-implementation review):** the encoding above assumed filenames/
  version labels never contain `@` or `|`, but the existing experiment-name guard
  (`_qc_shared._validate_experiment_name`) only rejects path separators and `..` — not
  `@`/`|`. A real filename containing either character would silently corrupt the
  composite round-trip. The tool therefore explicitly rejects `experiment_1`/
  `experiment_2` containing `@` or `|` with `BloomMCPError(invalid_input)` before
  building either composite string — a small, tool-local check (not a change to the
  shared `_validate_experiment_name`, since this constraint is specific to this
  tool's encoding scheme, not a general experiment-name rule every tool should hold).
- **Second, more serious guard needed (found in PR review, confirmed by
  reproduction): the `@`/`|` guard alone was not sufficient.** The originally-shipped
  composite key, `f"{Path(experiment_1).stem}__x__{Path(experiment_2).stem}"`, is
  vulnerable to `AnalysisDir`'s own re-applied `Path(...).stem` whenever *either
  original stem itself* contains a dot — a case the `@`/`|` guard does nothing about,
  and no test fixture used a dotted stem, so it shipped unnoticed. Reproduced directly:
  `experiment_1="my.experiment.v2.csv"` has `Path(...).stem == "my.experiment.v2"`;
  joining with `experiment_2="cylinder.csv"`'s stem gives the composite
  `"my.experiment.v2__x__cylinder"`; `AnalysisDir` then re-applies
  `Path("my.experiment.v2__x__cylinder").stem`, which strips at the *last* dot in that
  composite string — `"my.experiment"` — silently discarding `experiment_2`'s name and
  the `__x__` separator entirely. Two different `(experiment_1, experiment_2)` pairs
  that happen to produce the same truncated prefix would then collide in the same
  storage directory — silent data mixing between unrelated runs, not a crash.
  `FakeResultStore`'s own simplified stem helper (`name[:-4] if name.endswith(".csv")
  else name`) does not reproduce `AnalysisDir`'s real `Path.stem` re-truncation
  behavior, so no unit test running only against the fake store could have caught
  this — confirmed by adding a test that exercises the real `AnalysisDir` class
  directly (`test_analysis_dir_does_not_truncate_dotted_composite_key`).

  **First fix attempted (superseded — see below):** each stem was passed through
  `_storage_safe_stem(name) -> Path(name).stem.replace(".", "_")` before joining. Since
  the resulting composite string was then dot-free, `AnalysisDir`'s re-applied
  `Path(...).stem` was a no-op on it — but this was a *sanitization*, not a rejection,
  and sanitization is lossy.

  **Third guard needed (found when re-reviewing commit c649f9d, confirmed by
  reproduction): the sanitizing fix reopened the identical collision class one level
  down.**
  `_storage_safe_stem("my.experiment.csv")` and `_storage_safe_stem("my_experiment.csv")`
  both produce the identical stem `"my_experiment"` — a dot-vs-underscore filename
  variant is exactly the kind of naming difference a scientist re-exporting or
  versioning a dataset would plausibly produce (e.g. `"cylinder.v2.csv"` alongside a
  hand-renamed `"cylinder_v2.csv"`). Two calls using either filename would then
  silently collide on the same storage key — the same silent-data-mixing failure mode
  this fix was written to close, just over a narrower input space. A lossy
  substitution can narrow a collision class but cannot close it; only a fix that
  actually forecloses the *possibility* of two different composites reducing to the
  same encoded string does that.

  **Second fix attempted (superseded — see below):** `_storage_safe_stem` was removed,
  and `_reject_unsafe_composite_stem` instead rejected — before any composite string was
  built — an `experiment_1`/`experiment_2` whose derived stem (`Path(name).stem`)
  contained either a `.` (the original truncation vector) or the literal
  `_COMPOSITE_SEPARATOR` substring (`"__x__"`) itself, on the theory that a stem
  containing the separator was the only way it could appear in the joined string. That
  let the composite `experiment=` key be built directly from the un-sanitized
  `Path(experiment_1).stem`/`Path(experiment_2).stem`, with no lossy transform at all —
  and closed the reproduction case from that same re-review of c649f9d. `based_on_version`'s own
  `@`/`|` literals were also centralized into the same `_VERSION_SEPARATOR`/
  `_PAIR_SEPARATOR` constants the reserved-character guard uses (found in review: the
  guard and the builder had drifted — the guard used named constants, the builder still
  hardcoded bare `@`/`|` literals), so the two can no longer silently diverge.

  **Fourth guard needed (found when re-reviewing commit a5ec16d, confirmed by direct
  construction and an ad-hoc 500k-sample randomized stress test run by hand while
  designing this fix — see below for why that check is now a committed hypothesis
  property test, not just a design-time claim): rejecting a stem that *contains* the
  separator substring is not sufficient to keep the separator out of the *joined*
  string.** `_COMPOSITE_SEPARATOR` (`"__x__"`) is self-overlapping — its own
  prefix (`"__x"`) and suffix (`"x__"`) can be reconstructed from characters straddling
  the stem/separator boundary even when neither individual stem contains the full
  5-character substring. Concretely: `stem_1="A"`, `stem_2="x__B"` and `stem_1="A__x"`,
  `stem_2="B"` both pass the second-fix guard (neither stem contains `"__x__"`) yet both
  join to the identical string `"A__x__x__B"`. The hand-run stress test found thousands
  of such collisions in seconds, confirming this is a real, easily-reachable bug class —
  not a contrived corner case. No guard on either stem's *content* can close this,
  because the ambiguity is a property of the *join itself*, not of either stem alone; a
  third round of "reject this substring too" would only narrow the class again (as the
  second fix narrowed the first), not close it structurally.

  **Final fix:** `_reject_unsafe_composite_stem`'s separator-substring branch is removed
  (a stem containing `"__x__"` is now permitted — it is no longer collision-prone). Only
  the dot-rejection remains as a guard (renamed `_reject_dotted_stem`); the composite key
  itself is now built by `_composite_experiment_key(stem_1, stem_2)`, which prefixes
  `stem_1` with its own length before joining:
  `f"{len(stem_1)}_{stem_1}{_COMPOSITE_SEPARATOR}{stem_2}"`. Given the resulting string,
  `len(stem_1)` is always recoverable as the value of its maximal leading run of digit
  characters, which necessarily terminates at the first `"_"` (digits never contain
  `"_"`, and nothing precedes the numeral) — this pins the exact boundary between
  `stem_1` and everything after it regardless of what either stem contains, making the
  encoding genuinely injective rather than merely "no known collision found yet." This
  is the standard length-prefixed ("netstring") technique for exactly this class of
  delimiter ambiguity, and is why the prior two fixes were the wrong *kind* of fix: they
  treated a structural encoding problem as an input-validation problem, so each one
  could only narrow the reachable collision space, never close it. Verified in
  `test_cross_experiment_correlations_tool.py`: `test_boundary_straddling_stems_no_longer_collide`
  reproduces the exact adversarial pair above and asserts the two composites now differ;
  `test_composite_key_round_trips_for_any_stem_pair` proves a left-inverse exists over a
  fixed table of named edge cases (digits, underscores, the separator substring itself,
  empty stems). **Found when re-reviewing commit f199126:** that table (256 hand-picked
  pairs) was the *only* committed coverage — the "500k-sample randomized stress test"
  this section and the `f199126` commit message described was an ad-hoc check run by
  hand at design time, never committed as a test, so it could not catch a future
  regression. `test_composite_key_injective_property` and
  `test_composite_key_distinct_pairs_never_collide` (both `hypothesis`-based, 1000
  generated examples each per run) now give the real, repeatable version of that
  guarantee the design-time claim asserted — a strictly stronger guarantee than
  spot-checking a handful of hand-picked pairs, which is the testing gap that let three
  rounds of this bug ship in the first place.
- **Self-correlation guard (found in PR review):** `experiment_1 == experiment_2` was
  neither rejected nor tested — a plausible copy-paste mistake that would otherwise
  silently compute and persist a meaningless self-vs-self correlation matrix under a
  `"foo__x__foo"` storage key. Now rejected with `BloomMCPError(invalid_input)` before
  any I/O.
- **Argument-order sensitivity is now surfaced in the schema (found in PR review):**
  `(experiment_1=A, experiment_2=B)` and `(experiment_1=B, experiment_2=A)` produce two
  distinct composite keys and two independent, un-cross-referenced persisted runs —
  already an accepted Open Question below, but previously undiscoverable from the
  tool's own schema. Both `experiment_1`/`experiment_2` field descriptions now state
  this explicitly.

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
(a naming-convention bend that required explicit sign-off before landing). The exact
format above is now pinned verbatim in `spec.md`'s persistence requirement (not left to a
paraphrase), so what is being signed off on is unambiguous. The scheme's most serious
known risk — silent collision between two distinct experiment-name pairs — went through
three narrowing-but-not-closing guard attempts (reject/sanitize a dot; reject a
separator-substring) before landing on a structural fix: `_composite_experiment_key`'s
length-prefixed encoding is provably injective for arbitrary stem content, not merely
"no known collision found yet," and is covered by a test reproducing the exact
adversarial pair found when re-reviewing commit a5ec16d, a fixed table of named edge
cases, and two `hypothesis` property tests generating 1000 random stem pairs per run
(not just a handful of hand-picked cases) — see D1's "Fourth guard needed"/"Final fix"
above. This substantively de-risks what a human reviewer is being asked to approve,
though the sign-off itself is still outstanding (tasks.md 1.3).

### D2 — Genotype-means via upstream `calculate_genotype_means`, not a bespoke `.groupby().mean()`

Issue #489 suggests computing genotype-means "locally (`.groupby(genotype_col)[trait_cols].mean()`
— a simple pandas operation, no upstream dependency needed for this step)". That undercounts
the actual contract: `calculate_cross_experiment_correlations` reads
`exp1_means.loc[g, "n_samples"]` / `exp2_means.loc[g, "n_samples"]` internally
(`cross_experiment_analysis.py:1013-1019`). A bare `.groupby().mean()` has no
`n_samples` column, so calling the delegate on it would raise a `KeyError` inside
upstream code — an opaque failure, not a clean `assumption_violated`.

Upstream already provides exactly the right shape:
`calculate_genotype_means(df, trait_cols, genotype_col) -> DataFrame` groups, means, and
appends `n_samples` in one call (`cross_experiment_analysis.py:702-721`) — also public,
also not in the issue's excluded list (only `load_and_align_experiments` is excluded).
Using it keeps the "delegate all math to upstream, no reimplementation" invariant every
other granular tool holds (see `pca_analysis`'s and `clustering`'s docstrings) and avoids
a bespoke aggregation whose column contract could silently drift from what the delegate
expects. (Verified during review: this correction is legitimate, not a missed reading —
issue #489's own "confirmed public" function list never mentions
`calculate_genotype_means`.)

### D3 — `calculate_correlation_confidence_intervals` deferred, not worked around

`calculate_correlation_confidence_intervals(correlation_df, n_genotypes, confidence=0.95)`
applies one scalar `n_genotypes` to **every row** via `.apply(lambda r: calculate_correlation_ci(r, n_genotypes, confidence))`
(`cross_experiment_analysis.py:1596-1638`). But `correlation_df` already carries a
**per-row** `n_genotypes` column from `calculate_cross_experiment_correlations` — each
trait pair does its own NaN-alignment inside `_prepare_aligned_values`, so the number of
genotypes actually paired can legitimately differ row to row. Passing one global scalar
to a Fisher-z confidence-interval computation for every row would silently produce a
wrong CI width for any row whose real aligned N differs from the passed value.

Two options: (a) call it once per distinct `n_genotypes` value present in the row and
merge — plausible but adds real complexity for a function this proposal is not
otherwise obligated to ship; (b) defer it entirely. Choosing **(b)**: confidence
intervals are not part of the issue's minimum ask ("delegate the actual correlation
math + significance testing"), and baking in an interpretation of a genuinely
ambiguous upstream signature now — before confirming with upstream whether the flat-`n`
behavior is intentional — risks the same "silently changes numbers" failure mode #489
itself was raised to avoid. Filed as a natural follow-up once resolved (either upstream
fixes/clarifies the signature, or bloommcp deliberately implements option (a)).
Verified during review (via `git show 1ef181a^:...correlation_tools.py`): the retired
`run_cross_experiment_correlations` tool didn't compute CIs either — only Pearson r/p,
FDR-correction, and a summary — so deferring this is not a regression against the tool
being replaced, only against the fuller upstream surface.

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
  `min_samples` aligned genotypes, post-D8 pre-filter) is treated as **degenerate
  input** — `BloomMCPError(code="assumption_violated")` with a remedy to lower
  `min_samples` or check genotype overlap between the two experiments, no run
  persisted. This mirrors `pca_analysis`/`clustering`'s treatment of a
  delegate-signaled degenerate fit. (Before D8's fix, "lower `min_samples`" would have
  been a misleading remedy — see D8.)
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

### D8 — `min_samples` is a confirmed no-op in the delegate; bloommcp pre-filters before delegating

**Found during review, independently re-verified against the installed upstream
source (`sleap_roots_analyze==0.1.0a5`).** `calculate_cross_experiment_correlations`
computes a `min_samples`-filtered `valid_genotypes` list and even prints it
(`cross_experiment_analysis.py:1011-1020`) — but the per-trait-pair loop immediately
below calls `_prepare_aligned_values(exp1_means, exp2_means, trait1, trait2,
min_samples=0)` with a **hardcoded `0`** (line 1028), never referencing
`valid_genotypes` again. The only genotype floor that actually applies is
`_prepare_aligned_values`'s own hardcoded `< 3` check (line 76). So passing
`min_samples=10` to the delegate today has **zero effect** on which genotypes
participate — a genotype with a single replicate is treated identically to one with
twenty. D2's citation of these exact lines (justifying why `n_samples` must be built
via `calculate_genotype_means`) never itself noticed the filter built from that column
is discarded downstream — this is the same class of "trust but verify the delegate"
lesson bloommcp already learned once, documented in `clustering.py`'s
`_gmm_selected_scores` (upstream returning the wrong candidate's BIC/AIC on GMM
auto-select).

**Consequence if unaddressed:** the tool's own documented remedy for a degenerate
result ("lower `min_samples`", D6) would be scientifically misleading — lowering it
changes nothing, since it was never enforced in the first place.

**Fix:** bloommcp pre-filters both genotype-means tables to `n_samples >=
params.min_samples` **before** calling the delegate:

```python
exp1_means = calculate_genotype_means(frame1.df, trait_cols_1, frame1.genotype_col)
exp2_means = calculate_genotype_means(frame2.df, trait_cols_2, frame2.genotype_col)
exp1_means = exp1_means[exp1_means["n_samples"] >= params.min_samples]
exp2_means = exp2_means[exp2_means["n_samples"] >= params.min_samples]
corr_df = calculate_cross_experiment_correlations(
    exp1_means, exp2_means, trait_cols_1, trait_cols_2, min_samples=params.min_samples
)
```

`min_samples` is still forwarded to the delegate call unchanged (harmless — it stays
inert there) rather than passing a magic `0`, so the call remains correct and
idempotent even if upstream later fixes the internal filter (both filters would then
agree; today only bloommcp's pre-filter does any work). This is a genuine,
call-site-level workaround, not a documentation-only caveat — matching the bar D3
already sets ("document, or work around — don't silently trust") rather than a lower
one. **Filed upstream** as
[talmolab/sleap-roots-analyze#205](https://github.com/talmolab/sleap-roots-analyze/issues/205)
(tracked separately from this OpenSpec change; that report also raises D3's
`calculate_correlation_confidence_intervals` question in the same thread).

**Regression coverage gap found when re-reviewing commit c649f9d:** the golden fixture
(`turface_cylinder_cross_experiment_correlation_golden.json`) was generated recording a
`min_samples_3_upstream_no_op` block — the no-op reproduced directly against the real
turface_19/cylinder genotype-means, not a synthetic fixture — specifically so this
no-op's continued existence could be pinned against real data. No test actually read
that block back: `test_upstream_min_samples_no_op_still_present` pins the no-op only
against a synthetic 3-genotype fixture. `test_upstream_min_samples_no_op_still_present_on_real_fixture_pair`
(added in a5ec16d) now exercises the same raw-delegate call against the real fixture
pair and asserts against the golden's `n_genotypes`/`correlation` values, so that
recorded field is no longer orphaned. **Found when re-reviewing commit a5ec16d:** that
new test still omitted the golden block's own `p_value` field despite asserting the
other two values right next to it — a silent regression in `p_value` specifically would
have gone uncaught. Added in f199126.

### D9 — Reuse the reserved `correlation` tool_class slot; no discovery-list changes needed

**Found during review**, and resolved for free: `manifest.CANONICAL_TOOL_CLASSES`
(`bloommcp/src/bloom_mcp/manifest/__init__.py:26-36`) and
`list_existing_analyses.TOOL_CLASSES`
(`bloommcp/src/bloom_mcp/sections/core/list_existing_analyses.py:16-24`) **both
already contain `"correlation"`**, reserved and kept intact since the pre-#438 legacy
correlation tools were retired, specifically so historical runs would still read back
(see that file's "do NOT prune retired classes" comment). This tool reuses that slot
as its `tool_class` rather than inventing a new `"cross_experiment_correlation"` entry
— exactly the precedent `descriptive_stats` already set for the retired `"stats"`
slot (`descriptive_stats.py:53-60`, "reused, not new"). Consequence: **no changes to
either discovery list are needed** — runs persist under `tool_class="correlation"` and
are immediately visible via `list_existing_analyses` with zero registration work,
closing what would otherwise have been a silent discoverability gap (a synthetic
composite `experiment` key is never a real `list_experiments()` entry, so an
unregistered tool_class would have been invisible with no error).

The MCP tool's *name* stays `cross_experiment_correlations` (plural, matching the
module/function name and the issue's own naming) — only the persisted `tool_class`
segment is `correlation` (matching the storage-prefix convention every other tool
already uses: short, not the full tool name — `pca`, `qc`, `umap`, `stats`).

### D10 — `_validate_trait_subset` patched to name the experiment in every branch

**Found during review.** `_validate_trait_subset` (`bloommcp/src/bloom_mcp/tools/_qc_shared.py`)
already takes an `experiment: str` parameter and interpolates it into the "outside
certified set" error message (line 136), but **not** into the empty-list (line 116),
duplicate (line 126), or non-numeric (line 149) messages. Calling it twice — once per
experiment, as this tool does — would produce identically-worded errors for those
three failure modes regardless of which experiment triggered them, which is not
"naming the offending experiment" as this proposal's own acceptance test requires.

**Fix:** a small, backward-compatible patch to `_qc_shared.py` — interpolate
`{experiment!r}` into the empty-list, duplicate, and non-numeric messages too (message
text only; no signature change, since `experiment` is already a required positional
parameter every existing call site already passes). This is a shared helper also used
by `pca_analysis`, `clustering`, and `descriptive_stats` — the improvement benefits all
of them (clearer error messages), not just the new tool, and carries no behavior change
(same exception type, same code, same remedy — only the message string gains an
experiment identifier in three more branches).

### D11 — Finite-value defense-in-depth before genotype-mean aggregation

**Found during review.** `pca_analysis.py:252-264` and `clustering.py:328-340` both add
an explicit `np.isfinite()` check on selected trait columns before delegating,
specifically because `require_clean=True` alone isn't trusted as a guarantee. This
proposal's original draft omitted the equivalent check before `calculate_genotype_means`
— `.mean()` silently skips NaN (understating the true per-genotype replicate count
against what's reported as `n_samples`) and silently propagates `±inf` into a genotype
mean, poisoning the correlation math without ever raising. Fix: add the same
`np.isfinite(selected.to_numpy(dtype=float)).all()` check on each experiment's selected
trait columns, independently, before calling `calculate_genotype_means` — a non-finite
value on either side raises `BloomMCPError(code="assumption_violated")` naming which
experiment, with the same "re-run qc_clean" remedy `pca_analysis`/`clustering` use.

### D12 — Persist both genotype-means tables for traceability

**Found during review.** Upstream itself discards per-trait-pair genotype identity —
`_prepare_aligned_values` returns the aligned genotype list but the call site inside
`calculate_cross_experiment_correlations` assigns it to `_` (line 1032) and never
surfaces it. So even in principle, no wrapper can recover *which specific genotypes*
fed a given trait pair's correlation without reimplementing that private, underscore-
prefixed alignment function — which conflicts with this tool's entire "delegate, don't
reimplement" premise, and `_prepare_aligned_values`'s leading underscore is itself a
"do not depend on this" signal from upstream.

The proportionate middle ground: persist both experiments' full genotype-means tables
(`genotype_means_1.csv`, `genotype_means_2.csv` — each carrying every certified trait's
per-genotype mean **and** the `n_samples` column `calculate_genotype_means` produces,
post-D8-filter) as additional artifacts alongside `correlations.csv`/`significant.csv`/
`summary.json`. This doesn't reconstruct the *exact* per-pair NaN-filtered subset, but
gives a scientist auditing a surprising correlation enough to manually cross-reference
which genotypes, and with how many replicates, were available for either trait —
materially better than the count-only traceability the original draft offered, without
reimplementing upstream's internal alignment.

### D13 — FDR multiplicity-family caveat is a documentation obligation, not a runtime behavior

**Found during review.** Benjamini-Hochberg FDR correction (delegated to
`identify_significant_correlations`) is only valid over the family of tests it was
actually computed across. If a caller reruns the tool with a narrower
`trait_columns_1`/`trait_columns_2` subset, the test family shrinks and the
FDR-corrected `p_value_corrected` values for the surviving pairs will **not** match
their values from a full run — a caller could reasonably (and wrongly) expect a
"subset" rerun to reproduce the full run's corrected p-values for the pairs it shares.
There is no runtime fix for this (the tool has no way to know a caller's cross-call
expectations) — it is a documentation obligation: the `trait_columns_1`/
`trait_columns_2` Pydantic field descriptions must state this caveat explicitly (see
tasks.md 3.1), so it is visible in the tool's schema wherever an agent or human reads
parameter docs, not buried only in this design doc.

### D14 — Explicit path-traversal guard, not incidental safety

**Found during PR review.** This tool never called the existing
`_qc_shared._validate_experiment_name` guard; safety was incidental — it happened to
hold only because the tool always calls `load_experiment(..., require_clean=True)`,
whose real resolution path keys off `Path(filename).stem`/`.name` rather than the raw
string, not because anything raised on a path-unsafe input. `pca_analysis`/`clustering`
share this exact gap (pre-existing, not introduced here — out of scope to fix in this
PR, a candidate follow-up to centralize). Given this tool doubles the untrusted-filename
surface (two names instead of one), it now calls `_validate_experiment_name` explicitly
on both `experiment_1` and `experiment_2` as defense-in-depth, rather than relying on
the incidental protection alone.

**Found when re-reviewing commit c649f9d:** `_validate_experiment_name`'s error message never
named which field it was validating — harmless for its original single-experiment
callers (`qc_inspect`), but this tool calls it twice, so a caller had no way to tell
whether `experiment_1` or `experiment_2` was rejected. `_validate_experiment_name` now
takes an optional `label: str = "experiment"` parameter (default preserves every
existing single-experiment caller's behavior unchanged); this tool passes
`"experiment_1"`/`"experiment_2"` explicitly, so the message names the offending field
the same way every other guard in this tool already does.

## Risks / Trade-offs

- **String-encoded composite fields are a readability/parseability trade-off**, not a
  type-safe one — mitigated by D1's "human-readable, not machine-parsed" framing, the
  explicit `@`/`|` guard, and by not being the first bloommcp precedent for bending a
  single-string field's literal meaning (D3 of `devendor-bloommcp-analysis` already bent
  a naming convention with documented sign-off).
- **A second two-experiment tool would strain D1's encoding further** — mitigated by
  scoping D1 explicitly to "revisit with two real call sites," not designing an
  abstraction now against one.
- **A confirmed upstream `min_samples` no-op (D8)** — mitigated by a bloommcp-side
  pre-filter and an upstream bug report; the workaround is idempotent against a future
  upstream fix.
- **Deferring `calculate_correlation_confidence_intervals` (D3) leaves a capability gap**
  relative to the full upstream surface, but not relative to the tool being replaced
  (verified: it didn't compute CIs either).
- **A constant (zero-variance) genotype-mean trait may produce a `NaN` correlation row**
  that survives into `correlations.csv` rather than being filtered or raising — this
  matches the actual call path's behavior (the private `_calculate_correlations`
  helper `calculate_cross_experiment_correlations` uses internally calls raw
  `scipy.stats.pearsonr`/`spearmanr` with no zero-variance guard, emitting
  `ConstantInputWarning` and returning `NaN`; corrected citation — an earlier draft of
  this doc pointed at the public `calculate_correlations` wrapper, which has its own
  explicit zero-variance check and is not actually on this tool's call path) and is
  accepted as a pass-through here rather than a bloommcp-side rejection, since the row
  is transparently NaN (not silently dropped or misrepresented) and a stricter guard
  would diverge from what the delegate itself considers valid output. Exercised by
  `test_constant_genotype_mean_trait_yields_nan_correlation_not_a_crash` — the NaN row
  is confirmed excluded from `n_significant`/`n_highly_significant` (NaN comparisons
  are always `False`), not silently corrupting either count.

## Migration / Rollout

No data migration — a new tool, additive registration. No existing tool's behavior,
schema, or persisted-run shape changes. Reusing the `correlation` tool_class (D9) means
`list_existing_analyses` already lists that slot today (for historical, pre-#438 runs,
currently always empty in a fresh environment) — this tool simply makes it a live write
target again, exactly as `descriptive_stats` already did for `stats`.
`test_devendor_invariants.py`'s retired-tool list and drift guards are unaffected (this
is a new tool, not a repoint of a retired one).

**Testing infrastructure fix (found via this PR's own CI, not a design decision about
this tool).** This tool's live smoke test (`test_cross_experiment_correlations_smoke.py`)
failed in CI with `result["outputs"]` coming back an empty `{}`, even though
`store.commit()` had genuinely succeeded (every other assertion in the same test
passed). Root-caused to `bloommcp/tests/smoke/conftest.py`'s `_call_tool_sync` reading
`result.data` — fastmcp's client-side reconstruction of the server's JSON into a
dynamic type derived from the tool's output schema — rather than
`result.structured_content` (the server's actual JSON, no reconstruction). The confirmed
symptom: fastmcp's `json_schema_to_type` reconstructs the nested `outputs: dict[str,
str]` field (a plain-dict schema with no declared `properties`, only
`additionalProperties`) into a fieldless placeholder type instead of a real `dict[str,
str]`, so every key is silently dropped on the client side (the exact internal
schema-routing path wasn't traced further than that — this is the confirmed symptom,
not a fully pinned root cause; an earlier draft of this note additionally claimed the
top-level result schema and the nested `outputs` schema "collide" on an identical
auto-generated type name, which overstated a mechanism this investigation didn't
actually verify — corrected when re-reviewing commit c649f9d). Confirmed directly
against the live container for the long-shipped `pca_analysis` tool too
(`structured_content` correct, `.data.outputs` empty) — this was a latent bug in
*every* `RunLinks`-based tool's live-smoke coverage, invisible until this PR became the
first smoke test to assert on `outputs` at all. Fixed at the shared `conftest.py` level
(one line), benefiting every smoke test in the package, not patched around locally in
this tool's own test file. That "benefits every `RunLinks`-based tool" claim was itself
untested beyond this one tool until re-reviewing commit c649f9d flagged it (found in
review): nothing else asserted on `outputs` to pin the shared fix going forward.
`test_pca_analysis_smoke`
now also asserts `set(result["outputs"]) == {...}`, so a second, independent tool
regression-tests the shared `conftest.py` fix, not just #489's own tool.

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
  approver for a convention bend)? The exact format is now pinned in spec.md, and the
  scheme's most serious known risk (dotted-filename truncation) is fixed and tested
  against the real `AnalysisDir`, so this is a concrete, substantially de-risked
  artifact to review — still an outstanding human sign-off, not something this PR can
  resolve on its own (tasks.md 1.3).
- ~~Should D3's deferred `calculate_correlation_confidence_intervals` question be raised
  with upstream alongside the D8 bug report?~~ Resolved: both raised in the same thread,
  [talmolab/sleap-roots-analyze#205](https://github.com/talmolab/sleap-roots-analyze/issues/205).
- The recorded golden fixture (`turface_cylinder_cross_experiment_correlation_golden.json`)
  is not bit-for-bit reproducible across regenerations of
  `scripts/gen_cross_experiment_correlation_golden.py` — re-running it produced
  correlation values differing at ~1e-16 (BLAS/threading float noise), functionally
  irrelevant given every comparison test's `abs=1e-6` tolerance, but worth knowing
  before assuming a diff in the checked-in JSON on a future regeneration signals a
  real regression rather than routine noise (found in PR review).
