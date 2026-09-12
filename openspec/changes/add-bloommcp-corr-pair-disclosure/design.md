## Context

`plot_correlation_matrix` (#466, PR #683, merged into `staging` 2026-09-11) reads **raw,
uncleaned** data by design and already discloses two reasons a heatmap cell can be blank:
`zero_variance_traits` (the trait carries no usable variance at all) and
`low_overlap_trait_pairs` (the pair's non-null overlap fell below `min_periods`). Its module
docstring closes with two paragraphs that disclose remaining gaps and point at issues #784 and
#785. This change turns both of those paragraphs into shipped behavior.

Constraints carried over from #466 and not relitigated here:

- **Raw-read posture.** No QC has run; disjoint per-trait missingness is normal, not an edge
  case.
- **No vendored plotting logic.** This file delegates every chart element to
  `sleap_roots_analyze.visualization.create_correlation_heatmap`. Drawing a plain text
  disclosure onto the returned `Figure` is the one sanctioned exception (#466 round 6).
- **Cylinder scale is ~846 traits** → 357,435 off-diagonal pairs. The existing overlap
  computation is vectorized (`notna.T @ notna`) specifically because a Python double loop is
  prohibitive at that width. Anything added here must stay vectorized.

All measurements below are reproducible via `benchmarks/corr_pair_disclosure_bench.py` in this
change directory, run on macOS (darwin 25.5.0, Apple silicon) against the versions bloommcp's
`uv.lock` currently resolves: **pandas 3.0.2, numpy 2.4.4, Python 3.11**. The 500-row figure is
a deliberately conservative stand-in for a cylinder experiment's plant count; every step this
change adds is row-independent (it operates on `n_traits × n_traits` arrays), so more rows only
improve the ratio.

## Goals / Non-Goals

**Goals**

- A caller reading a strong-correlation count can see the pairwise `n` (and a CI) behind the
  **weakest-supported** pairs in it, and can tell whether those are representative, without
  re-querying the data.
- Every `NaN` off-diagonal cell falls into exactly one named disclosure bucket.
- The overlap threshold's rationale is owned by this tool, not inherited from an unrelated QC
  convention.
- No response-size or wall-clock regression at cylinder scale.

**Non-Goals**

- Masking the rendered PNG per cell (#747) — upstream/vendored, different risk profile.
- Sample-size disclosure for `plot_trait_histograms`/`plot_trait_boxplots` (#748) — different
  tools, different delegates, no shared code.
- Changing the ±0.7 magnitude cutoff, the `min_periods` *value*, or any existing field's
  value/semantics.

## Decisions

### Decision 1 — Derive `locally_constant_trait_pairs` by elimination, not by recomputing variance

#785 suggests "compute per-pair within-overlap variance alongside the existing `notna.T @ notna`
overlap counts". **Rejected in favor of a residual-bucket derivation.**

The guarded `corr` matrix is already computed. Its off-diagonal `NaN` cells have exactly three
causes, and two are already identified by name. So the third bucket is:

```
NaN in the guarded corr, in the upper triangle
  AND NOT in a zero-variance trait's row or column
  AND NOT below the overlap floor
```

**The subtraction must use the raw sub-threshold *mask*, not the published
`low_overlap_trait_pairs` list.** That list deliberately excludes pairs involving a zero-variance
trait, to avoid double-reporting the same cell (`plot_correlation_matrix.py:281-287`).
Subtracting the published list instead of `overlap_counts < _MIN_CORR_OVERLAP` would let a pair
that is *both* low-overlap and zero-variance fall through into this bucket. Subtracting the
zero-variance mask first makes the order irrelevant, but the mask-not-list rule is what keeps it
correct either way, so it is pinned in the spec and by a test.

Why elimination beats recomputing:

- **Exhaustive by construction.** The requirement #785 actually asks for is *"every NaN cell
  falls into exactly one named bucket."* A residual bucket satisfies that as an identity. A
  recomputed variance test only satisfies it if the recomputation's failure modes happen to
  coincide with pandas' — which is an assumption, not a guarantee.
- **Numerically robust.** The vectorized within-overlap variance is `E[x²] − E[x]²` via
  `(X²).T @ M` and `X.T @ M`, which suffers catastrophic cancellation exactly where it matters
  (variance near zero) and would need an arbitrary tolerance to call "constant". Pandas has
  already made that determination internally, consistently with the coefficient it returned.

**Verified, not assumed.** A 400-frame fuzz (`benchmarks/corr_pair_disclosure_bench.py
--fuzz`) over randomly degenerate 40×6 frames — mixing globally-constant, all-NaN,
single-non-null, non-finite, and locally-constant columns at random missingness rates — found
**0 unexplained and 0 double-counted `NaN` cells** across every frame, with 117 cells landing in
the residual bucket. The exhaustiveness claim is measured, not argued.

**Alternative considered:** three extra matmuls for within-overlap mean/second-moment. Measured
at 846 traits × 500 rows: 0.005 s — so this was **not** rejected on cost. It was rejected on
robustness and exhaustiveness.

**Residual risk, stated plainly:** the bucket is named for its dominant cause but is defined as
a remainder, so any *future* pandas `NaN` cause would be silently absorbed under a name
asserting local constancy. The fuzz above is the guard against that; it is a regression test,
not a proof. An earlier draft of this design claimed a non-finite overlap was a second known
inhabitant of this bucket — **that was wrong**, see Decision 7.

### Decision 2 — `strong_correlation_pairs` is ordered by ascending `overlap_n`, capped, and paired with uncapped summaries

Uncapped is not an option. Measured at 846 traits × 500 rows on synthetic data with realistic
trait collinearity (root traits are heavily redundant — many are derived from the same
underlying measurement):

| latent factors | strong pairs (\|r\| > 0.7) | uncapped JSON |
| -------------- | -------------------------- | ------------- |
| 20             | 163                        | ~0.02 MB      |
| 5              | 44,137                     | ~5.1 MB       |
| 3              | 107,183                    | ~12.4 MB      |

A 12 MB MCP tool response is not a disclosure, it is a denial of service against the caller's
context. So the list is capped at `_MAX_STRONG_PAIRS_REPORTED = 20`.

**Why 20 and not 50.** An earlier draft said 50. Implementation turned up a binding constraint
the draft had missed: `test_provenance_stamped_seed_none_and_links_returned` enforces this
file's "links, not blobs" contract by rejecting any single result field whose repr exceeds
5,000 chars, and 50 structured pairs (~116 B each) clears that on a 20-trait experiment. The
right response was to respect the existing contract rather than relax it — 20 also sits in
line with `heatmap_caveat`'s own 10-name cap, and the uncapped scalars below carry everything
the truncated tail would have.

**The ordering is what makes the cap safe.** Sorting *ascending by `overlap_n`* means the cap
truncates the best-supported end, never the worst. `strong_correlation_pairs[0]` is always the
single least-supported pair in the count — precisely the number #784 says a caller currently
cannot see. A cap on an arbitrarily-ordered (or `|r|`-ordered) list would have hidden exactly
the pairs the field exists to surface.

**But a capped list is still a biased sample**, and biased in a way that can mislead in the
opposite direction: 20 low-`n` pairs out of 44,137 look like the whole population is poorly
supported. #784's actual question — "do my 12 strong correlations rest on n=10 or n=1000?" — is
not answered by the capped list at cylinder scale. So the result also reports
`strong_pair_overlap_min`/`_median`/`_max`, computed over **every** strong pair, uncapped. Three
scalars, O(1) payload, and they close #784's first remedy bullet in exactly the case the cap
cannot.

Ties are broken by descending `|r|` then by trait order (`np.lexsort` over the three keys), so
the output is deterministic — the tool declares no `random_state` and its snapshot tests depend
on stable output.

**Asymmetry with `low_overlap_trait_pairs`/`zero_variance_traits`, which stay uncapped:** not
because degenerate data is rare — the Context above says the opposite — but because those two
lists carry an explicit completeness *contract*. `heatmap_caveat` caps its own names at 10 and
then tells the reader to consult those lists for the full set (#466 round 7), so truncating them
would make that instruction false. `strong_correlation_pairs` feeds no such promise.

### Decision 3 — Report a Fisher-z 95% CI per pair; report null where it is undefined

#784 offers "per-pair n" or "a CI / n per reported strong pair". Both, because `n` alone still
requires the caller to know the Fisher transform to act on it, and the issue's own framing is a
CI ("r=0.7 at n=10 has a 95% CI of roughly [0.13, 0.92]"). The CI is what converts the
disclosure into a decision.

Computed as `tanh(arctanh(r) ± z / sqrt(n − 3))`, with `z` the exact two-sided 95% normal
quantile (1.959963984540054, not the rounded 1.96), and both undefined cases reported as
`None`:

- `|r| ≥ 1` → `arctanh` is `±inf` and the interval collapses to `[r, r]`. A perfectly collinear
  pair over ≥ 10 points is possible in real trait data (derived traits). **Reported as `None`,
  not as `[1.0, 1.0]`** — a zero-width interval claims perfect precision, which is exactly the
  false confidence this field exists to puncture.
- `n ≤ 3` → the standard error is undefined. Unreachable while `_MIN_CORR_OVERLAP` is 10, but
  the constant is now this module's own to change (Decision 5), so the branch is implemented and
  tested rather than left to a distant invariant.

Both bounds are coerced through `_qc_shared._finite_or_none` — the helper `clustering` and
`descriptive_stats` already use for exactly this — so no `NaN`/`Infinity` token can reach the
result model or the manifest. This matters concretely: manifests are written by
`storage_backend._json_bytes` via `json.dumps(...)` with default `allow_nan=True`, which emits
bare `NaN`/`Infinity` and produces a file that strict JSON readers reject.

The CI is **not** a significance test, it assumes approximate bivariate normality that raw,
zero-inflated root-trait data often violates, and it carries no multiplicity correction — at 846
traits there are 357,435 pairs, so "95%" is a per-pair statement, not a family-wise one. All
three caveats go in the field description, because a CI presented without its assumptions can
mislead more than the bare count it replaces.

### Decision 4 — `heatmap_caveat` is NOT extended to `locally_constant_trait_pairs`

An earlier draft justified this with "the caveat fires exactly when the JSON and the image
disagree." **That is checkably false** and has been corrected: the caveat also fires for
`zero_variance_traits`, and a zero-variance trait's row is `NaN` in the delegate's unguarded
`.corr()` too — image blank, JSON excluded, they agree, caveat fires anyway
(`plot_correlation_matrix.py:305-323`). #747 anticipated that reasoning and rejected it
explicitly: *"even a blank row is easy to miss next to a colored one."*

The real, narrower reason to exclude the new bucket is the caveat's **text**, which reads: *"…
have too little real data behind them to trust as drawn — the image still colors them like a
genuine strong correlation."* That sentence is simply false of a locally-constant pair, which
renders blank. So the options are: widen the trigger and leave a warning that misdescribes the
new bucket; or widen the trigger and rewrite the text, changing the disclosure callers already
receive for the two existing buckets. Neither is worth it for a cell the image does not colour,
which this change discloses through its own field and the manifest.

Recorded coupling cost, since Decision 6 claims this change only helps #747: this exclusion is
argued from the caveat's current wording, which exists *because* #747 is open. If #747 lands,
`heatmap_caveat`'s whole trigger set — zero-variance included — should be revisited, and the
spec says so.

### Decision 5 — Decouple `_MIN_CORR_OVERLAP` from `_CANONICAL_MIN_SAMPLES_PER_TRAIT`, same value

Today `_MIN_CORR_OVERLAP = _CANONICAL_MIN_SAMPLES_PER_TRAIT` (10), imported from
`bloom_mcp.tools._qc_shared`. The two express different concepts:

- `_CANONICAL_MIN_SAMPLES_PER_TRAIT` — "enough samples to trust a **trait**", a per-column
  completeness convention `qc_clean`/`qc_inspect` apply when deciding what to drop.
- `_MIN_CORR_OVERLAP` — "enough **pairwise** overlap that a coefficient is not the arithmetic
  artifact an n=2/n=3 overlap guarantees", a degeneracy floor on a bivariate statistic.

They agree at 10 by coincidence, not by derivation. Keeping the alias means a future QC retune
silently moves which pairs this tool counts, which pairs it flags, and — after this change —
where the boundary between `low_overlap_trait_pairs` and `locally_constant_trait_pairs` sits.

**What this decision does not do, stated so it is not over-read:** it does not *derive* the
value. The degeneracy argument bottoms out at n=2/n=3 and justifies something like 4–5, not 10;
10 is inherited by value and merely conservative for the job. Ownership makes the number this
tool's to change, it does not make it principled. Re-deriving it is a behavior change — it would
move both counts, move pairs between buckets, and break comparability with already-persisted
manifests — so it is out of scope here and recorded in Open Questions instead.

The constant becomes a plain module-level `10` with that honest rationale in its comment. **The
value does not change and no behavior changes.** A test asserts the value is 10 and that the QC
name is no longer bound in this module's namespace, so the alias cannot silently return. The
test deliberately does **not** assert the two constants are still equal: that equality is the
coincidence being decoupled, and pinning it would turn the next legitimate QC retune into a
spec-level failure whose cheapest fix is to re-alias.

### Decision 6 — #747 and #748 stay out of scope; #768 is advanced but not claimed

- **#747** (mask the rendered heatmap per cell) needs a `min_periods`/precomputed-matrix
  parameter on `sleap_roots_analyze.visualization.create_correlation_heatmap` — a **different
  repository**, pinned here as `sleap-roots-analyze>=0.1.0a5` (`bloommcp/pyproject.toml:49`), so
  it is gated on an upstream release. Its only in-repo fallback is re-implementing heatmap
  rendering in bloommcp, which contradicts this file's own no-vendored-plotting-logic principle
  and needs its own design decision. Bundling it would block two additive, backward-compatible
  disclosure fields behind someone else's release cadence. Doing this change first is also
  helpful to it: if #747 ever does take the bloommcp-side route, the guarded matrix and the
  per-pair statistics assembled here are exactly its input. The cost is recorded in Decision 4.
- **#748** (`plot_trait_histograms`/`plot_trait_boxplots` sample-size disclosure) shares the
  *principle* but no code — different tools, different delegates, different result models.
- **#768** (the snapshot test cannot catch a single-cell heatmap defect) is not addressed here,
  but this change materially advances its remedy option 2 — "a numeric assertion on the
  correlation matrix values themselves, as a complement to the pixel-level check" — by putting
  per-pair `r` and `overlap_n` in both the result and the manifest. Named so the docstring
  paragraph about it stays coherent with its rewritten neighbours, not claimed as closed.

### Decision 7 — A non-finite trait belongs to `zero_variance_traits`, and that field must say so

An earlier draft of this design asserted that a pair whose shared overlap contains `±inf` would
also land in the new residual bucket, and used that as the "honest caveat" justifying its name.
**That was wrong, and the correction changes where the fix goes.** Measured against the pinned
pandas:

```
pd.Series([1.0, 2.0, 3.0, np.inf, 5.0]).std(skipna=True)  ->  nan
not (nan > 0)                                             ->  True
```

A column containing an infinity has a `NaN` standard deviation, so the existing guard at
`plot_correlation_matrix.py:260-262` already classifies it as zero-variance. Such a trait can
never satisfy "globally non-constant" and therefore can never reach the residual bucket — the
draft's clause was unsatisfiable, and it would have shipped as a normative `SHALL` with no test
behind it.

The real defect it was pointing at is one level over: `zero_variance_traits`' field description
enumerates exactly three cases it considers ("constant (std 0), entirely NaN (std NaN), and …
exactly one non-null value") and **omits the non-finite one**, so a scientist whose trait
contains an `inf` is told their data is constant. The grouping is right — an inf-carrying trait
is genuinely uncorrelatable — but the label misdescribes their data. Fixed where the problem
actually is: a MODIFIED requirement and an expanded field description naming the fourth case,
plus a test. The new bucket's honest caveat is narrowed accordingly (Decision 1, "Residual
risk").

## Risks / Trade-offs

- **Cylinder-scale cost.** Measured at 846 traits × 500 rows (15% missing):

  | step                              | cost    |
  | --------------------------------- | ------- |
  | `.corr(min_periods=…)` (existing) | 0.305 s |
  | `notna.T @ notna` (existing)      | 0.107 s |
  | residual-`NaN` bucket (**new**)   | 0.004 s |
  | strong-pair sort + cap (**new**)  | 0.002 s |

  → **~1.5% added to an existing 0.41 s**, no new O(n²) allocation beyond boolean masks over
  arrays already held (a `846²` bool array is 0.7 MB). Reproducible via the committed benchmark.

- **Response size.** Bounded by construction now that *both* new lists are capped at 20 entries
  each (~3 KB together) with uncapped scalars carrying the true magnitudes. An earlier draft
  shipped `locally_constant_trait_pairs` uncapped, arguing that a frame degenerate enough to
  fill it would already be filling `low_overlap_trait_pairs`. **That premise was false** — the
  buckets are independent by construction, since this one exists precisely for pairs that
  *clear* the overlap floor. Measured: a single "saturating" trait (globally non-constant, but
  taking one value on every row where it is observed) in a 300-trait frame produces **299**
  locally-constant pairs with `low_overlap_trait_pairs` empty and `zero_variance_traits` empty;
  at 846 traits that is 845 entries per such trait, and count traits that are zero for most
  plants are exactly this shape.

- **Spec ordering.** The `bloommcp-viz-tools` capability is still pending archive under
  `converge-bloommcp-viz-tools`, so this change's MODIFIED deltas target requirements not yet in
  `openspec/specs/`. `openspec validate --strict` passes anyway — the tooling does not check a
  MODIFIED target exists — so archiving out of order would silently drop the requirements this
  change builds on. The durable fix is to archive `converge-bloommcp-viz-tools` first (its PR
  merged 2026-09-11); `tasks.md` §5 records the ordering, and the note is mirrored into that
  change's own `tasks.md` so whoever archives it sees the dependency from that side too.

## Migration Plan

None required. Every change is additive to `PlotCorrelationMatrixResult` and to manifest
`params`; no existing field changes value, type, or meaning, and `_MIN_CORR_OVERLAP` keeps its
value. Old manifests remain readable — the new `params` keys are simply absent from runs
persisted before this change, which the manifest readers already tolerate for optional keys.

## Open Questions

Neither is blocking; both are recorded so they are not rediscovered as novel.

- **Should the overlap floor be re-derived rather than inherited?** Decision 5 takes ownership of
  the constant without re-deriving its value. A principled alternative is already in this
  change's vocabulary: pick the smallest `n` at which the Fisher-z CI at the tool's own
  `|r| > 0.7` cutoff clears some floor (e.g. CI low > 0.3 → n ≈ 21), tying the threshold to this
  tool's reporting cutoff instead of to QC's. That is a behavior change — it moves counts and
  invalidates comparisons against persisted runs — so it wants its own proposal and issue.
- **Should the ±0.7 magnitude cutoff become configurable** now that its evidential basis is
  reported per pair? Out of scope for the same reason: it would change existing counts.
