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

## Goals / Non-Goals

**Goals**

- A caller reading a strong-correlation count can see the pairwise `n` (and a CI) behind the
  **weakest-supported** pairs in it, without re-querying the data.
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
  AND NOT explained by zero_variance_traits
  AND NOT explained by low_overlap_trait_pairs
```

Why this beats recomputing:

- **Exhaustive by construction.** The requirement #785 actually asks for is *"every NaN cell
  falls into exactly one named bucket."* A residual bucket satisfies that as an identity. A
  recomputed variance test only satisfies it if the recomputation's failure modes happen to
  coincide with pandas' — which is an assumption, not a guarantee.
- **Numerically robust.** The vectorized within-overlap variance is `E[x²] − E[x]²` via
  `(X²).T @ M` and `X.T @ M`, which suffers catastrophic cancellation exactly where it matters
  (variance near zero) and would need an arbitrary tolerance to call "constant". Pandas has
  already made that determination internally, consistently with the coefficient it returned.
- **Cheap.** See Risks below; it is a boolean-mask combination over an array we already hold.

*Honest caveat:* this bucket is slightly wider than its name. A pair whose overlap contains a
non-finite value (`±inf`) also yields `NaN` with sufficient overlap and globally non-constant
traits, and lands here too. Disclosed in the field description and the module docstring rather
than papered over. The name follows the issue's own suggestion for traceability, and
locally-constant is the dominant case in practice.

**Alternative considered:** three extra matmuls for within-overlap mean/second-moment. Measured
at 846 traits × 500 rows: 0.005 s — so this was **not** rejected on cost. It was rejected on
robustness and exhaustiveness.

### Decision 2 — `strong_correlation_pairs` is ordered by ascending `overlap_n` and capped

Uncapped is not an option. Measured at 846 traits × 500 rows on synthetic data with realistic
trait collinearity (root traits are heavily redundant — many are derived from the same
underlying measurement):

| latent factors | strong pairs (\|r\| > 0.7) | uncapped JSON |
| -------------- | -------------------------- | ------------- |
| 20             | 201                        | ~0.02 MB      |
| 5              | 44,132                     | ~5.1 MB       |
| 3              | 107,180                    | ~12.4 MB      |

A 12 MB MCP tool response is not a disclosure, it is a denial of service against the caller's
context. So the list is capped at `_MAX_STRONG_PAIRS_REPORTED = 50`.

**The ordering is what makes the cap safe.** Sorting *ascending by `overlap_n`* means the cap
truncates the best-supported end, never the worst. `strong_correlation_pairs[0]` is always the
single least-supported pair in the count — precisely the number #784 says a caller currently
cannot see. A cap on an arbitrarily-ordered (or `|r|`-ordered) list would have hidden exactly
the pairs the field exists to surface.

Ties are broken by descending `|r|` then by trait order, so the output is deterministic — the
tool declares no `random_state` and its snapshot tests depend on stable output.

**Asymmetry with `low_overlap_trait_pairs`/`zero_variance_traits`, which stay uncapped:** those
are the *exceptional* case (degenerate data), while strong pairs are the *routine* case in real
trait data, as the table shows. The existing uncapped lists also have an explicit contract —
`heatmap_caveat` caps its names at 10 and then tells the reader to consult the full lists, so
those lists must stay complete (#466 round 7). `strong_correlation_pairs` makes no such promise;
its field description states plainly that it is the weakest-evidence end of a larger set whose
true size is `strong_positive_correlations + strong_negative_correlations`.

### Decision 3 — Report a Fisher-z 95% CI per pair, not just `n`

#784 offers "per-pair n" or "a CI / n per reported strong pair". Both, because `n` alone still
requires the caller to know the Fisher transform to act on it, and the issue's own framing is a
CI ("r=0.7 at n=10 has a 95% CI of roughly [0.13, 0.92]"). The CI is what converts the
disclosure into a decision.

Computed as `tanh(arctanh(r) ± 1.96 / sqrt(n − 3))`. Two guards:

- `|r| ≥ 1` → `arctanh` is `±inf`. A perfectly collinear pair over ≥ 10 points is possible in
  real trait data (derived traits). Clamped to a degenerate `[r, r]` interval rather than
  emitting `inf`/`NaN` into JSON.
- `n ≤ 3` → the standard error is undefined. Unreachable while `_MIN_CORR_OVERLAP` is 10, but
  the constant is now this module's own to change (Decision 5), so `ci_low`/`ci_high` are
  `Optional[float]` and set to `None` there rather than relying on a distant invariant.

The CI is **not** a significance test and the field description says so — it is the interval the
point estimate is consistent with. This is the same distinction #466 round 7 drew for
`min_periods` itself.

### Decision 4 — `heatmap_caveat` is NOT extended to `locally_constant_trait_pairs`

`heatmap_caveat` has one specific job: warn that the persisted PNG is rendered by an
**unguarded** delegate, so a cell this tool excluded from its counts still renders as a solid,
confidently-colored square (#747). It fires exactly when the JSON and the image *disagree*.

A locally-constant pair is `NaN` in the vendored delegate's own independent `.corr()` too — #785
says so explicitly, and it follows from the fact that the degeneracy is in the data, not in the
guard. The image renders it blank, the JSON excludes it, and they agree. Firing the caveat there
would train callers to distrust an image that is, on this point, correct — and would dilute a
warning that currently means something precise.

Consequence: the existing "Rendered Heatmap Masking Mismatch Is Disclosed" requirement is
**unchanged** by this proposal, and so is `heatmap_caveat`'s value for every input. The new
bucket is disclosed through its own result field and the manifest, like the others.

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

The constant becomes a plain module-level `10` with its own rationale comment. **The value does
not change and no behavior changes**; a test pins the two constants as currently equal *and*
asserts the correlation tool no longer imports the QC one, so the decoupling can't silently
regress into an alias again.

### Decision 6 — #747 and #748 stay out of scope

- **#747** (mask the rendered heatmap per cell) needs a `min_periods`/precomputed-matrix
  parameter on `sleap_roots_analyze.visualization.create_correlation_heatmap` — a **different
  repository**, pinned here as `sleap-roots-analyze>=0.1.0a5`, so it is gated on an upstream
  release. Its only in-repo fallback is re-implementing heatmap rendering in bloommcp, which
  contradicts this file's own no-vendored-plotting-logic principle and needs its own design
  decision. Bundling it would block two additive, backward-compatible disclosure fields behind
  someone else's release cadence. Doing this change first is also strictly helpful to it: if
  #747 ever does take the bloommcp-side route, the guarded matrix and the complete per-pair
  statistics assembled here are exactly its input.
- **#748** (`plot_trait_histograms`/`plot_trait_boxplots` sample-size disclosure) shares the
  *principle* but no code — different tools, different delegates, different result models. Its
  only overlap is the `sections/sleap_roots/analysis/` folder.

## Risks / Trade-offs

- **Cylinder-scale cost.** Measured at 846 traits × 500 rows (15% missing):

  | step                              | cost    |
  | --------------------------------- | ------- |
  | `.corr(min_periods=…)` (existing) | 0.305 s |
  | `notna.T @ notna` (existing)      | 0.107 s |
  | residual-`NaN` bucket (**new**)   | 0.004 s |
  | strong-pair sort + cap (**new**)  | 0.002 s |

  → **~1.5% added to an existing 0.41 s**, no new O(n²) allocation beyond boolean masks over
  arrays already held. A test pins the wide-frame path so a future non-vectorized refactor is
  caught.

- **Response size.** Bounded by construction: the new strong-pair list is capped at 50 entries
  (~6 KB). `locally_constant_trait_pairs` is uncapped by consistency with its two sibling
  buckets; a frame degenerate enough to produce a huge one is already producing a huge
  `low_overlap_trait_pairs` today, so this change does not introduce that exposure. Noted, not
  fixed here — fixing it would mean changing an existing field's contract.

- **Residual bucket mislabels a non-finite overlap as "locally constant".** Accepted and
  disclosed (Decision 1). The alternative — a separate `non_finite_overlap_trait_pairs` bucket —
  would need the same numerically-fragile recomputation Decision 1 rejects, to split a case that
  `qc_inspect`'s `per_trait_inf_count` already reports on the tool an agent runs alongside this
  one.

- **Spec ordering.** The `bloommcp-viz-tools` capability is still pending archive under
  `converge-bloommcp-viz-tools`, so this change's MODIFIED delta targets a requirement not yet
  in `openspec/specs/`. Archiving out of order would apply the MODIFIED against a missing file.
  Mitigated by an explicit ordering task (`tasks.md` §5) rather than by dropping Decision 5.

## Migration Plan

None required. Every change is additive to `PlotCorrelationMatrixResult` and to manifest
`params`; no existing field changes value, type, or meaning, and `_MIN_CORR_OVERLAP` keeps its
value. Old manifests remain readable — the new `params` keys are simply absent from runs
persisted before this change, which the manifest readers already tolerate for optional keys.

## Open Questions

None blocking. One deferred judgment, recorded so it is not rediscovered: whether the ±0.7
magnitude cutoff should itself become configurable now that its evidential basis is reported
per pair. Out of scope — it would change existing counts, which this change deliberately does
not do.
