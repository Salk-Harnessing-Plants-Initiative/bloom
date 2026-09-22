"""plot_correlation_matrix — correlation heatmap for trait relationships (#466).

Converged onto the ``@as_mcp_tool`` contract every other tool in this folder uses (Pydantic
I/O, structured ``BloomMCPError``, one stamped ``Provenance``, versioned ``ResultStore``
persistence) — the same read-only, pre-clean EDA pattern as ``qc_inspect``: reads the raw
frame via the :class:`ExperimentReader` port (no ``require_clean``), since a correlation view
is exactly what an agent uses *before* deciding ``qc_clean``'s thresholds.

Delegates the heatmap's actual rendering (the colored grid itself) to
``sleap_roots_analyze.visualization.create_correlation_heatmap``; this file owns no *plotting*
logic of its own — it does not compute or draw anything resembling a chart element. It does,
however, call ``Figure.text(...)`` directly on the delegate's returned ``Figure`` to draw the
``heatmap_caveat`` disclosure footnote (#466 review round 6 — "delegates rendering" should not
be read as "never touches the returned Figure object"; see the masking-mismatch paragraph
below for why that footnote exists and is tested separately from the delegate's own render).
The reported strong-correlation counts are a plain ``pandas`` summary of the same selection,
computed directly here (not delegated) — unchanged from the tool's pre-conversion behavior.

**Traits with no usable variance are excluded from the strong-correlation counts with no
error**, and that exclusion is now *enforced* rather than assumed. Realistic here specifically
because this tool reads **raw, uncleaned** data (no QC has dropped a degenerate trait yet —
see the raw-read decision below). ``zero_variance_traits`` names exactly which selected traits
are affected, so the counts are not silently misleading.

The guard is ``not (0 < std(skipna=True) < inf)``. Both bounds matter, and both were wrong
before the #784 review:

* The exclusion used to rest on "``pandas`` returns ``NaN``, and ``NaN > 0.7`` is ``False``, so
  such a pair cannot be counted". **That reasoning does not hold for a trait carrying an
  infinity.** ``pandas``' ``nancorr`` masks each pair with ``np.isfinite``, so it drops the
  offending row and returns a perfectly ordinary coefficient over the rows that remain — which
  then cleared the magnitude cutoff and was counted. The tool could therefore file a trait as
  uncorrelatable and publish a strong correlation for it in the same response. The counts and
  ``strong_correlation_pairs`` are now both masked by ``zero_variance_traits`` explicitly, at a
  single site, so the documented contract is the one the code implements.
* The upper bound ``< inf`` catches a column of finite but enormous values (``|x| >~ 1e154``)
  whose sum of squares overflows, making its std ``+inf``. Since ``inf > 0`` is ``True``, the
  old guard passed it through as healthy; ``pandas`` then returned ``NaN`` for its
  coefficients, and the pair landed in ``locally_constant_trait_pairs`` — telling the caller
  "no variance within the overlap" about a column whose problem is the exact opposite.

``zero_variance_traits``' description enumerates all five cases the guard catches, and names
the one it *cannot* catch: a genuinely varying trait whose variance underflows to exactly
``0.0`` is indistinguishable from a true constant and is reported as one.

**A pair with too few overlapping non-null observations is excluded the same way, via
``.corr(min_periods=...)``.** Raw, uncleaned data can have disjoint per-trait missingness, so
two traits can overlap in as few as 2 non-null rows — and 2 points are *always* perfectly
(anti)correlated, producing a spurious exact ±1.0 "strong correlation" from a near-empty
overlap. ``min_periods`` (``_MIN_CORR_OVERLAP``, this module's own constant — see
the threshold paragraph below) makes pandas return ``NaN`` instead of a numerically valid but
meaningless coefficient for any pair below it, so it is excluded from the counts exactly like
a zero-variance trait. ``low_overlap_trait_
pairs`` names exactly which pairs this affects (excluding any pair already explained by a
zero-variance trait, to avoid double-reporting the same ``NaN`` cell under two reasons).

**What that threshold does and does not buy you.** It is a *degeneracy* floor, not a
significance test, and the two should not be confused (#466 review round 7). Clearing it means
only that a coefficient is not the arithmetic artifact an n=2 or n=3 overlap guarantees; it does
NOT mean the coefficient is trustworthy. At n=10 a Pearson r still carries a very wide confidence
interval — r=0.7 at n=10 has a 95% CI of roughly [0.13, 0.92] (Fisher z), so a pair can clear
``min_periods``, be counted in ``strong_positive_correlations``, and still be entirely
consistent with a weak underlying relationship. ``strong_*_correlations`` is therefore a count
of coefficients past a fixed magnitude cutoff, not a count of established findings.

**So the evidence behind those counts is reported, not just the counts (#784).**
``strong_correlation_pairs`` carries each counted pair's ``r``, its pairwise ``overlap_n``, and
a Fisher-z 95% interval, ordered **ascending by overlap** and capped at
``_MAX_STRONG_PAIRS_REPORTED``. The ordering is what makes the cap safe: it truncates the
best-supported end, so entry ``[0]`` is always the least-supported pair in the whole selection.
Because a capped list is still a biased sample — 20 low-n pairs drawn from tens of thousands
read as if the whole population were thin — ``strong_pair_count`` and
``strong_pair_overlap_min``/``_median``/``_max`` are computed over **every** strong pair,
uncapped.

The interval is deliberately labelled with everything that makes it *weaker* than a reader will
assume, because "95%" is the most calibrated-sounding thing this tool emits. It is not a
significance test; it is not corrected for multiplicity (at ~846 traits there are 357,435
pairs, so "95%" is a per-pair statement); it assumes approximate bivariate normality that raw
trait data often violates; and two further limits are specific to how it is used here:

* **It is not valid for a pair selected *because* ``|r|`` cleared the cutoff.** Selecting on the
  same data that builds the interval biases it. Over 19,900 truly-uncorrelated pairs at n=10,
  coverage across all pairs was 94.7% as designed — but among the 499 that cleared ``|r| >
  0.7`` it was 0/499. Every pair in ``strong_correlation_pairs`` is selected that way by
  construction.
* **Rows are treated as independent.** Root-trait data usually is not (replicates share a
  genotype, scans share a plant), and ``overlap_n`` counts rows, not independent units. On this
  repo's own fixture shape, nominal-95% coverage falls to roughly 92% / 76% / 53% as the
  intra-class correlation rises through 0.2 / 0.5 / 0.9.

It is reported as ``None`` where the transform is undefined (``|r| = 1``, or an overlap below
the Fisher standard error's domain) rather than as a zero-width interval claiming certainty.

``_MIN_CORR_OVERLAP`` is **this module's own** constant, no longer an alias for
``_qc_shared._CANONICAL_MIN_SAMPLES_PER_TRAIT`` (#784). That one answers "enough samples to
trust a *trait*"; this one answers "enough *pairwise* overlap to not be an arithmetic
artifact". They agree at 10 by coincidence, and the alias meant a QC-side retune would silently
move this tool's counts, its flags, and the boundary between ``low_overlap_trait_pairs`` and
``locally_constant_trait_pairs``. Ownership is not derivation: 10 is inherited by value and
merely conservative for the degeneracy argument, which bottoms out nearer 4-5. Re-deriving it
would move the counts and break comparability with persisted runs, so it is deliberately not
done here.

**Every blank cell now has exactly one named reason (#785).** A pair that is globally
non-constant and clears ``min_periods`` overlap can still be *locally* constant within that
shared overlap — one trait happens to take the same value on exactly the rows where both are
non-null — producing a ``NaN`` cell that neither of the two lists above explains.
``locally_constant_trait_pairs`` is that third bucket, and with it every off-diagonal ``NaN``
falls into exactly one of the three, so the tool can always say *why* a cell is blank.

It is derived **by elimination** from the guarded matrix (``NaN`` ∧ upper-triangle ∧ not a
zero-variance row/column ∧ not below the overlap floor), not by recomputing a within-overlap
variance as #785 originally suggested. Totality then holds by construction rather than
depending on a fresh numerical tolerance agreeing with the determination pandas already made
when it returned the coefficient — and ``E[x²] - E[x]²`` suffers catastrophic cancellation
exactly where it would matter, near zero variance. Two consequences worth knowing:

* The subtraction uses the raw sub-threshold **mask**, not the published
  ``low_overlap_trait_pairs`` list. That list deliberately drops pairs involving a
  zero-variance trait, so subtracting it instead would let such a pair fall through into this
  bucket and break the exactly-one-bucket property.
* Because the bucket is a *remainder*, any ``NaN`` cause not covered by the other two buckets
  is absorbed here under a name asserting local constancy. The label is therefore the most
  likely explanation, not a verified one, and the field's description says so. What guards it
  is ``test_locally_constant_pairs_are_really_locally_constant``, which calls the tool and then
  re-derives the label independently — ``nunique()`` over each reported pair's shared finite
  rows — rather than re-deriving it from the same elimination the implementation used. (An
  earlier draft cited a 400-frame fuzz here. That fuzz partitioned by construction, so it
  could not fail; it has been replaced by the property test, which can.)

This bucket's population is independent of the other two — one trait that varies globally but
takes a single value wherever it is observed is locally constant against *every* partner — so
the list is capped and ``locally_constant_pair_count`` carries the uncapped total. It does
**not** feed ``heatmap_caveat``: see the masking-mismatch paragraph below.

**At least 2 resolved trait columns are required, and at least 2 of them must carry usable
(positive, finite) variance.** A correlation view of a single trait is not meaningful (there is no pair to
correlate); nor is one where every-but-one (or every) trait is constant/all-NaN, since every
cell would then be ``NaN`` — both rejected as ``invalid_input``/``assumption_violated`` before
any run is persisted, rather than silently committing a degenerate or all-``NaN`` result.

**The rendered PNG is NOT masked the same way the summary is — this is a known, disclosed gap,
not a silent one.** ``strong_positive_correlations``/``strong_negative_correlations`` and the
``zero_variance_traits``/``low_overlap_trait_pairs`` disclosure fields above are all computed
from this tool's own *guarded* ``.corr(min_periods=...)`` call. The persisted image, however, is
rendered by a separate, independent call to the vendored ``create_correlation_heatmap``, which
runs its own **unguarded** ``.corr()`` with no ``min_periods`` and no way to accept a
precomputed matrix, so the cell itself would still render as a solid, confidently-colored ±1.0
square with no fix applied here — genuinely fixing the *coloring* would mean either patching the
vendored delegate (outside this package) or re-implementing heatmap rendering in bloommcp
(against this file's own no-vendored-plotting-logic principle); tracked at
https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/747. Two things ARE done here,
cheaply and in-scope, so a caller who only ever looks at the saved PNG — never the JSON — still
gets a signal: (1) a warning footnote is drawn directly onto the already-rendered ``Figure``
before ``savefig`` whenever either disclosure list is non-empty (#466 review round 4 — the
first version of this fix left the image itself untouched, a JSON-only disclosure a PNG-only
consumer would never see); (2) ``heatmap_caveat`` is also stamped into the persisted run's
``params`` (mirroring ``resolved_trait_columns`` below), not just the live response — and,
alongside it, the FULL ``zero_variance_traits``/``low_overlap_trait_pairs`` lists, uncapped.
Stamping the caveat string alone was not enough to make "a later manifest read gets the same
signal a live call did" true: that string is capped at 10 names and then tells the reader to
"see zero_variance_traits/low_overlap_trait_pairs for the complete list", which a manifest-only
reader had no way to do past the cap (#466 review round 7). Both lists are now recoverable from
a stored run, so the claim holds at any number of flagged cells; (3) the footnote names the
actual flagged trait(s)/pair(s) (capped at 10), not just a count (#466 review round 6 — a bare
count told a
PNG-only viewer a problem existed with no way to tell *which* cell to distrust; since
``create_correlation_heatmap`` draws its axis tick labels from this same ``trait_cols`` list in
this same order, naming the flagged names directly lets that viewer cross-reference labels
they can already see on the image, with none of the "wrong cell" geometry risk a per-cell
hatch/marker would carry).

**``locally_constant_trait_pairs`` is deliberately NOT in that trigger (#785).** The honest
statement of why is narrower than it might look, and worth getting right (#784 review).

The caveat's text says the flagged cells "have too little real data behind them to trust as
drawn — the image still colors them like a genuine strong correlation." That sentence is
already inaccurate for one of the two buckets that *do* trigger it: a zero-variance trait's
row renders blank, not miscolored, exactly as a locally-constant cell would. So the criterion
cannot be "the text would be false", and it is not "the JSON and the image disagree" either —
they agree for zero-variance traits too. Both of those framings were tried and neither
survives its own statement.

The operative reason is simpler: **the text is the one every existing caller already
receives.** It is inaccurate for one bucket today, that inaccuracy was accepted deliberately
(#747: "even a blank row is easy to miss next to a colored one"), and widening the trigger to
a third bucket would either stretch the same inaccurate sentence over more cases or force a
rewrite of a string callers are already parsing — for a bucket whose cells the JSON now names
precisely. The new bucket gets its own field instead, which is strictly more informative than
being folded into a warning about miscoloring.

If #747 is ever fixed upstream, this whole trigger set should be revisited, since the caveat's
reason to exist disappears with it.

**Known test-coverage gap (#768, carried forward from PR #724 via staging):**
``tests/tools/test_viz_snapshot.py``'s pixel-diff regression check cannot reliably catch a
single-cell rendering defect in this heatmap — one real cell is a tiny fraction of the whole
image, small enough that even the most-detectable-possible miscoloring scores an RMS in the
same range as ordinary cross-platform rendering noise. See that test file's module docstring
and ``openspec/changes/add-bloommcp-plot-snapshot-tests/design.md`` (Decisions 2 & 7) for the
full measurement and why this is a structural limit, not a TODO. Note this compounds the
``heatmap_caveat`` disclosure above: the rendered PNG's untrustworthy cells are caught by
neither the vendored delegate's own masking (there is none, #747) nor the snapshot test.
What #784 contributes here, if anything: ``strong_correlation_pairs`` puts each counted pair's
``r`` and ``overlap_n`` into both the result and the manifest, which is the numeric data a
value-level assertion would otherwise have to recompute. It asserts nothing about the rendered
image — this change does not touch the render path — so it narrows what a per-cell check has
to do without being one. Deliberately written with no claim about whether #768 is still open:
PR #840 lands a per-cell oracle that closes it and rewrites the paragraph above, so a status
claim here would contradict that paragraph depending on which branch merged last.

Persists a versioned run under its own tool class ``correlation_matrix`` (not the shared,
unclaimed legacy ``viz`` slot — see ``openspec/changes/converge-bloommcp-viz-tools/design.md``
for why each converged tool mints its own class rather than interleaving version history with
its siblings).
"""

from __future__ import annotations

import math
from shutil import rmtree
from statistics import NormalDist
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from sleap_roots_analyze.visualization import create_correlation_heatmap

from bloom_mcp.contract import BloomMCPError, Provenance, RunLinks, as_mcp_tool
from bloom_mcp.data_access import ExperimentReadError
from bloom_mcp.result_store import CommitFailedError, ManifestReadError
from bloom_mcp.tools import _ports
from bloom_mcp.tools._plots import FIGURE_REGISTRY_LOCK, call_with_figure_cleanup
from bloom_mcp.tools._qc_shared import (
    _finite_or_none,
    _validate_experiment_name,
)

from ._viz_shared import resolve_trait_columns

_TOOL_CLASS = "correlation_matrix"
_HEATMAP_PNG = "correlation_matrix.png"
# The minimum pairwise overlap .corr() requires before reporting a coefficient — below it,
# pandas returns NaN instead of a numerically valid but statistically meaningless value.
# A DEGENERACY floor, not a significance threshold: it rules out the n=2/n=3 arithmetic
# artifact, it does not make a surviving coefficient trustworthy (r at n=10 still has a very
# wide CI). See the module docstring's "What that threshold does and does not buy you".
#
# OWNED HERE, not aliased to _qc_shared._CANONICAL_MIN_SAMPLES_PER_TRAIT as it was before
# #784. That constant answers "enough samples to trust a TRAIT" — a per-column completeness
# convention qc_clean/qc_inspect apply when deciding what to drop. This one answers "enough
# PAIRWISE overlap that a coefficient is not an arithmetic artifact", a floor on a bivariate
# statistic. They agree at 10 by coincidence, not by derivation, and the alias meant a QC-side
# retune would silently move which pairs this tool counts, which it flags, and where the
# boundary between low_overlap_trait_pairs and locally_constant_trait_pairs sits.
#
# Ownership is not derivation: the degeneracy argument bottoms out at n=2/n=3 and would
# justify something nearer 4-5. 10 is inherited by value and merely conservative for the job.
# Re-deriving it (e.g. from the Fisher CI at this tool's own |r| > 0.7 cutoff) would move both
# counts and break comparability with persisted manifests — out of scope, tracked in the
# change's design.md Open Questions.
_MIN_CORR_OVERLAP = 10

# Caps on the two per-pair disclosure lists (#784/#785). At cylinder scale (~846 traits,
# 357,435 pairs) realistic trait collinearity produces >100,000 strong pairs — ~12 MB of JSON,
# which is not a disclosure but a denial of service against the caller's context. The strong
# list is ordered ascending by overlap so the cap truncates the BEST-supported end, and the
# uncapped strong_pair_overlap_min/median/max scalars carry the true distribution. The
# locally-constant list is capped for the same reason and carries its own uncapped count: one
# trait that is globally non-constant but takes a single value wherever it is observed is
# locally constant against EVERY partner, so that list's size is independent of the other two.
# 20, not a larger number: test_provenance_stamped_seed_none_and_links_returned enforces
# this file's "links, not blobs" contract by rejecting any single result field whose repr
# exceeds 5000 chars, and 50 structured pairs clears that on a 20-trait experiment. 20 is
# also in line with heatmap_caveat's own 10-name cap, and the weak tail is what matters —
# the uncapped scalars carry the rest of the distribution.
_MAX_STRONG_PAIRS_REPORTED = 20
_MAX_LOCALLY_CONSTANT_PAIRS_REPORTED = 20

# The magnitude cutoff behind strong_positive_correlations/strong_negative_correlations and
# the strong_correlation_pairs list that explains them. Owned as a constant for the same
# reason _MIN_CORR_OVERLAP is: it was previously written inline at each of the three sites
# that use it, so the counts and the list that claims to explain them could drift apart under
# an edit to one of them (#784 review). Every consumer below derives from this name.
_STRONG_R = 0.7

# The confidence level of the Fisher-z interval, and its two-sided normal quantile.
#
# inv_cdf rather than a hardcoded 1.959963984540054: that literal was the only place the
# level existed, so "95%" appeared in prose but nowhere as a value. Note for the next reader
# tempted to "improve" this with scipy.stats.norm.ppf — scipy is deliberately NOT a
# dependency of this package (pruned in #305); statistics.NormalDist is stdlib and, at
# 1.9599639845400534, slightly more precise than the literal it replaced.
_CI_LEVEL = 0.95
_Z_CRIT = NormalDist().inv_cdf(1.0 - (1.0 - _CI_LEVEL) / 2.0)


def _fisher_ci(r: float, n: int) -> tuple[Optional[float], Optional[float]]:
    """Two-sided 95% Fisher-z interval for a Pearson ``r`` over ``n`` paired observations.

    ``None`` for both bounds wherever the transform is undefined, rather than a fabricated
    interval (#784):

    * ``|r| >= 1`` — ``arctanh`` is ``+/-inf`` and the interval collapses to ``[r, r]``.
      Reporting that zero-width interval would claim perfect precision, which is exactly the
      false confidence this field exists to puncture. Reachable in real trait data, where
      derived traits can be exactly collinear over a full overlap.
    * ``n <= 3`` — the standard error ``1/sqrt(n - 3)`` is undefined. Unreachable while
      ``_MIN_CORR_OVERLAP`` is 10, but that constant is this module's own to change now, so
      the branch is implemented rather than left resting on a distant invariant.

    **That guard is what keeps a non-finite token out of the manifest** — not the
    ``_finite_or_none`` wrappers on the return, which given the guard can never actually fire
    (``tanh`` of a finite argument is finite, and ``math.atanh(+/-1)`` raises rather than
    returning an infinity). The wrappers are belt-and-braces on the arithmetic, kept because
    they cost nothing; the load-bearing check is the ``if`` above, so do not weaken it on the
    theory that the wrappers will catch the result (#784 review). This matters because
    manifests are serialized by ``storage_backend._json_bytes`` via ``json.dumps`` with the
    default ``allow_nan=True``, which emits bare ``NaN``/``Infinity`` tokens that strict JSON
    readers reject.
    """
    if n <= 3 or not math.isfinite(r) or abs(r) >= 1.0:
        return None, None
    z = math.atanh(r)
    # The interval HALF-WIDTH on the z scale, not the standard error: the SE is
    # 1/sqrt(n - 3) and this is that multiplied by the critical value. Named for what it
    # holds, so nobody "fixes" it by multiplying by _Z_CRIT a second time (#784 review).
    half_width = _Z_CRIT / math.sqrt(n - 3)
    return (
        _finite_or_none(math.tanh(z - half_width)),
        _finite_or_none(math.tanh(z + half_width)),
    )


class StrongCorrelationPair(BaseModel):
    """One trait pair behind ``strong_positive_correlations``/``strong_negative_correlations``,
    with the evidence supporting it (#784)."""

    traits: list[str] = Field(
        min_length=2,
        max_length=2,
        description="The two trait columns, in resolved_trait_columns order.",
    )
    r: float = Field(
        description="Pearson correlation over the pair's shared FINITE rows (pandas masks "
        "each pair with isfinite, so a row carrying NaN or an infinity in either trait is "
        "dropped). Mathematically in [-1, 1]; no validator pins that bound, because a "
        "float-epsilon overshoot should not turn a cosmetic rounding artifact into a tool "
        "crash on real data."
    )
    overlap_n: int = Field(
        description="Number of rows where BOTH traits are FINITE — the sample this "
        "coefficient actually rests on, which can be far smaller than the experiment's row "
        "count when raw data has disjoint per-trait missingness. Counted with isfinite, not "
        "notna, to match the mask pandas itself applies (#784 review: a notna count "
        "over-reported by one per inf-carrying row, and fed that inflated n to the interval "
        "below, narrowing it past what the data supports)."
    )
    ci_low: Optional[float] = Field(
        default=None,
        description="Lower bound of the two-sided 95% Fisher-z confidence interval for r. "
        "Four limits, all of which widen it in practice: (1) NOT a significance test. "
        "(2) NOT corrected for multiplicity — at ~846 traits there are 357,435 pairs, so "
        "'95%' is a per-pair statement, not a family-wise one. (3) The interval is NOT valid "
        "for a pair SELECTED because |r| exceeded this tool's cutoff. Selecting on the same "
        "data used to build the interval biases it: simulated over 19,900 truly-uncorrelated "
        "pairs, coverage across all pairs was 94.8% as designed, but among the 518 that "
        "cleared the cutoff it was 0%. Every pair in strong_correlation_pairs is selected "
        "that way by construction, so treat these bounds as descriptive of the point "
        "estimate, never as a test that the correlation is real. (4) Rows are treated as "
        "INDEPENDENT observations. Root-trait data usually is not: replicates share a "
        "genotype, scans share a plant, angles share a scan. overlap_n counts ROWS, not "
        "independent units, so the interval is too narrow whenever the real unit of "
        "replication is coarser than the row. Simulated on this repo's own fixture shape "
        "(19 genotypes x 8 replicates = 152 rows, true rho=0), nominal-95% coverage falls to "
        "roughly 92% / 76% / 53% at intra-class correlations of 0.2 / 0.5 / 0.9 — the size "
        "of the effect is set by how much of a trait's variance sits between groups rather "
        "than within them, so no single number applies and only the direction is robust. At "
        "r=0.5, n=152 gives [0.370, 0.611] while the same r at the genotype-level n=19 gives "
        "[0.059, 0.778], three times wider. The interval also "
        "assumes approximately bivariate-normal data, which raw (zero-inflated, skewed, often "
        "bounded) root-trait data frequently violates. Read it as the range the point "
        "estimate is consistent with under assumptions this data probably breaks, not as a "
        "guarantee. None when the transform is undefined (|r| = 1, or an overlap too small "
        "for its standard error) — reported as null rather than a zero-width interval "
        "implying perfect precision.",
    )
    ci_high: Optional[float] = Field(
        default=None,
        description="Upper bound of the same interval; None under the same conditions as "
        "ci_low.",
    )


class PlotCorrelationMatrixParams(BaseModel):
    """Inputs for ``plot_correlation_matrix``. No ``seed`` — rendering is deterministic."""

    # extra="forbid": an unknown field isn't currently exploitable (it would be dropped
    # before persistence either way), but silently accepting it masks a caller typo
    # (#466 review round 5, matching the recommendation already made on sibling PR #726).
    model_config = ConfigDict(extra="forbid")

    experiment: str = Field(
        ..., description="Experiment identifier from list_available_experiments."
    )
    trait_columns: Optional[list[str]] = Field(
        default=None,
        description="Subset of trait columns to correlate; omit to use all detected traits. "
        "An explicit empty list is rejected rather than treated as 'all traits'.",
    )
    user_label: Optional[str] = Field(
        default=None,
        description="Optional slug appended to the version directory name.",
    )


class PlotCorrelationMatrixResult(RunLinks):
    """A small summary + links to the persisted correlation-heatmap run."""

    experiment: str
    source: str
    n_traits_plotted: int = Field(
        description="Number of trait columns correlated and drawn on the heatmap — "
        "always len(resolved_trait_columns). Named to match plot_trait_histograms/"
        "plot_trait_boxplots rather than qc_inspect's n_traits, so the 3 tools #466 "
        "converges onto one contract report the same concept under one name "
        "(#466 review round 7)."
    )
    strong_positive_correlations: int = Field(
        description=f"Off-diagonal trait pairs with Pearson correlation > {_STRONG_R}. "
        "Excludes any pair involving a trait named in zero_variance_traits, and any pair "
        "below the minimum overlap (those are NaN and cannot clear the cutoff). A count of "
        "coefficients past a fixed magnitude, NOT a count of established findings — see "
        "strong_correlation_pairs for the sample size and interval behind each one."
    )
    strong_negative_correlations: int = Field(
        description=f"Off-diagonal trait pairs with Pearson correlation < -{_STRONG_R}. "
        "Same exclusions and same caveat as strong_positive_correlations."
    )
    strong_correlation_pairs: list[StrongCorrelationPair] = Field(
        default_factory=list,
        description="The pairs behind the two counts above, each with the overlap and "
        "confidence interval supporting it — so a caller can tell whether a count of 12 "
        "rests on n=10 or n=1000 (#784). Ordered ASCENDING by overlap_n (weakest evidence "
        f"first) and capped at {_MAX_STRONG_PAIRS_REPORTED}: the ascending order is what "
        "makes the cap safe, since it truncates the best-supported end, so entry [0] is "
        "always the least-supported pair in the whole selection. Ties resolve by descending "
        "|r|, then trait order. The counts above remain the authoritative totals; when this "
        "list is truncated, strong_pair_count and strong_pair_overlap_min/median/max still "
        "describe every strong pair, not just the reported sample.",
    )
    strong_pair_count: int = Field(
        default=0,
        description="Number of strong pairs found, UNCAPPED — the true size of the list "
        "above before truncation, and equal to strong_positive_correlations + "
        "strong_negative_correlations. Reported explicitly so truncation is detectable "
        "without re-deriving it, and so a manifest reader (where the list is stamped "
        "capped) has the same signal a live caller does.",
    )
    strong_pair_overlap_min: Optional[int] = Field(
        default=None,
        description="Smallest pairwise overlap across ALL strong pairs (uncapped), not just "
        "the reported sample. None when there are no strong pairs.",
    )
    strong_pair_overlap_median: Optional[float] = Field(
        default=None,
        description="Median pairwise overlap across ALL strong pairs (uncapped). Reported "
        f"because a capped list is a biased sample: {_MAX_STRONG_PAIRS_REPORTED} low-n "
        "pairs drawn from tens of thousands read as if the whole population were poorly "
        "supported, when they may be its thin tail. None when there are no strong pairs.",
    )
    strong_pair_overlap_max: Optional[int] = Field(
        default=None,
        description="Largest pairwise overlap across ALL strong pairs (uncapped). None when "
        "there are no strong pairs.",
    )
    zero_variance_traits: list[str] = Field(
        default_factory=list,
        description="Selected traits this tool will not report a correlation for, because "
        "the trait's own standard deviation is not a usable positive finite number. Tested "
        "as `not (0 < std(skipna=True) < inf)`, which catches five cases: constant (std 0); "
        "entirely NaN (std NaN); exactly one non-null value, whose sample std is NaN rather "
        "than 0 because ddof=1 needs two observations (#466 review round 7); a trait "
        "carrying a non-finite value (+inf/-inf), whose std is NaN for that reason rather "
        "than for lack of variation; and a trait of finite but enormous values (|x| >~ 1e154) "
        "whose sum of squares overflows to std=+inf (#784/#785 review — previously waved "
        "through, since `inf > 0` is True, and then mislabelled as locally constant). "
        "'Zero variance' names the common case, not all five: when a trait you expected to "
        "vary appears here, check for infinities and for magnitudes near the float64 ceiling, "
        "not just constancy. Traits named here are excluded from "
        "strong_positive_correlations/strong_negative_correlations and from "
        "strong_correlation_pairs. KNOWN LIMIT, not fixed here: the mirror-image case — a "
        "genuinely varying trait whose values are so small (|x| <~ 1e-160) that its variance "
        "underflows to exactly 0.0 — is indistinguishable from a true constant by this test "
        "and is reported here as one. Rescale such a trait before correlating it. Empty when "
        "no trait was affected.",
    )
    low_overlap_trait_pairs: list[list[str]] = Field(
        default_factory=list,
        description="Trait pairs whose overlapping non-null observations fell below the "
        "minimum this tool requires to report a correlation coefficient — raw data can have "
        "disjoint missingness, and a near-empty overlap (as few as 2 points) can otherwise "
        "produce a spurious exact +/-1.0 'strong correlation'. Excludes any pair already "
        "explained by zero_variance_traits. Empty when every pair had enough overlap.",
    )
    locally_constant_trait_pairs: list[list[str]] = Field(
        default_factory=list,
        description="The third and final reason a heatmap cell can be blank (#785): the "
        "pair's correlation is NaN even though BOTH traits vary globally AND the pair clears "
        "the minimum-overlap floor. The usual cause is local constancy — one trait takes the "
        "same value on exactly the rows where both are non-null, so there is no variance "
        "within the shared overlap — but read this as 'no usable within-overlap variance OR a "
        "numerically degenerate coefficient', because the bucket is a REMAINDER: it is "
        "derived by elimination from the guarded correlation matrix rather than by "
        "recomputing a within-overlap variance. That is deliberate (a recomputation needs a "
        "tolerance to call 'constant' and could contradict the determination pandas already "
        "made returning this very coefficient), and it makes totality hold by construction — "
        "together with zero_variance_traits and low_overlap_trait_pairs, every off-diagonal "
        "NaN falls into exactly one of the three. The cost is that any NaN cause not covered "
        "by the other two buckets is absorbed here, so the label is the most likely "
        "explanation rather than a verified one. Unlike the other two lists this one does NOT "
        "feed heatmap_caveat: such a cell is NaN in the rendering delegate's own computation "
        f"too, so it renders blank rather than miscolored. Capped at "
        f"{_MAX_LOCALLY_CONSTANT_PAIRS_REPORTED} — and unlike strong_correlation_pairs, whose "
        "ascending-overlap order makes its cap truncate the best-supported end, this bucket "
        "has no evidence gradient to order by, so the cap is a deterministic but ARBITRARY "
        "slice in resolved_trait_columns order. See locally_constant_pair_count for the true "
        "total, and the run manifest for the complete uncapped list.",
    )
    locally_constant_pair_count: int = Field(
        default=0,
        description="Number of locally-constant pairs found, UNCAPPED — the true size of the "
        "list above before truncation. Reported separately because this bucket's population "
        "is independent of the other two: a single trait that varies globally but takes one "
        "value wherever it is observed is locally constant against every partner, so at ~846 "
        "traits one such trait alone yields 845 pairs while both sibling lists stay empty.",
    )
    heatmap_caveat: Optional[str] = Field(
        default=None,
        description="Populated only when zero_variance_traits or low_overlap_trait_pairs is "
        "non-empty: some cell(s) in the rendered heatmap are not backed by enough real data to "
        "trust, but the image still colors them as if they were a genuine strong correlation. "
        "Names the affected trait(s)/pair(s) directly (capped at 10, '+N more' beyond that) so "
        "a PNG-only viewer can match them against the image's own axis labels — not just a "
        "count. The same text is also drawn as a footnote directly on the saved PNG and stamped "
        "into the persisted run's params, as are the full uncapped zero_variance_traits/"
        "low_overlap_trait_pairs lists, so the cap is never the only record of what was "
        "flagged. Cross-check those two lists for the complete, uncapped set before trusting "
        "a highlighted cell in the image.",
    )
    resolved_trait_columns: list[str] = Field(
        description="The exact trait columns used to render/persist this run, in selection "
        "order — recorded even when trait_columns was omitted (auto-detected), so a later "
        "reader of this run's manifest can tell exactly which traits produced it without "
        "re-deriving auto-detection against data that may have drifted since.",
    )


@as_mcp_tool(
    input_model=PlotCorrelationMatrixParams,
    output_model=PlotCorrelationMatrixResult,
    errors=(ExperimentReadError, CommitFailedError, ManifestReadError),
)
def plot_correlation_matrix(
    params: PlotCorrelationMatrixParams, *, provenance: Provenance
) -> PlotCorrelationMatrixResult:
    """Render a correlation heatmap for ``experiment``'s **raw, uncleaned** data via
    ``create_correlation_heatmap`` and persist it. No QC cleaning has been applied — this is
    a pre-clean EDA view, the same category as ``qc_inspect``."""
    reader = _ports.reader()
    store = _ports.store()

    _validate_experiment_name(params.experiment)

    frame = reader.load_experiment(params.experiment, version="raw")
    trait_cols = resolve_trait_columns(frame, params.trait_columns, params.experiment)
    if len(trait_cols) < 2:
        raise BloomMCPError(
            code="invalid_input",
            message=f"plot_correlation_matrix requires at least 2 trait columns to "
            f"correlate; {params.experiment!r} resolved only {trait_cols!r}.",
            remedy="Select at least 2 trait columns, or omit trait_columns if the "
            "experiment has more than one detected trait.",
        )

    # `not (0 < std < inf)` rather than `not (std > 0)` (#784 review). The upper bound is
    # load-bearing: a column of finite-but-enormous values (|x| >~ 1e154, a sentinel or a
    # unit-conversion blowup in raw data) overflows the sum of squares, so its std is +inf,
    # and `inf > 0` is True — the old guard waved it through as a healthy trait. pandas then
    # returns NaN for its coefficients, which landed the pair in locally_constant_trait_pairs
    # and told the caller "no variance within the overlap" about a column whose problem is
    # the exact opposite. Filing it here instead is honest: the coefficient is numerically
    # unusable either way, and this is the bucket that says so.
    _trait_std = frame.df[trait_cols].std(skipna=True)
    zero_variance_traits = [c for c in trait_cols if not (0 < _trait_std[c] < np.inf)]
    zero_variance_set = set(zero_variance_traits)
    if len(trait_cols) - len(zero_variance_set) < 2:
        raise BloomMCPError(
            code="assumption_violated",
            message=f"plot_correlation_matrix requires at least 2 trait columns with "
            f"usable variance; {params.experiment!r} resolved {trait_cols!r}, of which "
            f"{sorted(zero_variance_set)!r} are constant, entirely NaN, carry non-finite "
            f"values, or have a variance that overflows/underflows float64.",
            remedy="Select at least 2 trait columns with finite non-zero variance, or use "
            "a different experiment.",
        )

    corr = frame.df[trait_cols].corr(min_periods=_MIN_CORR_OVERLAP)

    # Vectorized pairwise overlap counts — a python double loop over trait_cols x trait_cols
    # would be O(n^2) even just to build this, prohibitive at cylinder's ~846-trait scale.
    #
    # NOTE (pre-existing, not widened here, flagged in the #784 review): the
    # low_overlap_trait_pairs comprehension below IS an uncapped Python-level loop over every
    # sub-threshold upper-triangle pair, and the list is returned inline AND stamped uncapped
    # — up to ~357k two-element lists at cylinder width. A comment here used to call that
    # "typically small", which the same payload argument that justifies this change's new caps
    # contradicts. Capping it is a behavior change to an existing field and is deliberately
    # NOT done in this change; tracked with the rest of the disclosure-payload tier in #837.
    #
    # isfinite, NOT notna (#784 review). pandas' nancorr masks each pair with np.isfinite,
    # so it drops a row carrying an inf and computes the coefficient over the rows that
    # remain; a notna-based count therefore over-reports the sample a coefficient rests on
    # and would feed _fisher_ci an inflated n.
    #
    # Honest scope: with the zero-variance guard above in place this is DEFENSE IN DEPTH,
    # not an observable fix. Every non-finite-carrying column has a NaN (or infinite) std,
    # so it is excluded from the counts, the pair list and the locally-constant bucket
    # alike, and for every surviving pair the two masks agree — verified exhaustively over
    # the non-finite column shapes in test_no_non_finite_column_escapes_the_variance_guard,
    # which is the tripwire if that guard is ever relaxed. It is written this way anyway so
    # overlap_counts means what its name says independently of which columns upstream
    # happens to drop; the symptom the review reproduced (overlap_n = 20 against a real
    # overlap of 19) reached a caller only via a pair that the guard now excludes outright.
    finite = np.isfinite(
        frame.df[trait_cols].to_numpy(dtype="float64", na_value=np.nan)
    ).astype(np.int64)
    overlap_counts = finite.T @ finite
    low_overlap_mask = np.triu(overlap_counts < _MIN_CORR_OVERLAP, k=1)
    low_overlap_trait_pairs = [
        [trait_cols[i], trait_cols[j]]
        for i, j in zip(*np.where(low_overlap_mask))
        if trait_cols[i] not in zero_variance_set
        and trait_cols[j] not in zero_variance_set
    ]

    corr_values = corr.to_numpy()
    upper_mask = np.triu(np.ones(corr_values.shape, dtype=bool), k=1)

    # Built here rather than beside the locally-constant bucket below, because the strong
    # counts and the strong-pair list need it too — see the next comment.
    zero_variance_mask = np.zeros(corr_values.shape, dtype=bool)
    if zero_variance_set:
        zv_indices = [i for i, c in enumerate(trait_cols) if c in zero_variance_set]
        zero_variance_mask[zv_indices, :] = True
        zero_variance_mask[:, zv_indices] = True

    # ── #784: the evidence behind the two counts, and the counts themselves ─────────
    # Everything here reuses arrays already built above (corr, overlap_counts) — no second
    # pass over the frame.
    #
    # `& ~zero_variance_mask` is a fix, not a refinement (#784 review). This module's own
    # docstring promised that a trait in zero_variance_traits "counts toward neither
    # strong_positive_correlations nor strong_negative_correlations", and for an inf-carrying
    # column that was false: pandas drops the inf row, returns a real coefficient over the
    # rest, and the old unmasked `upper > 0.7` counted it. The tool therefore filed a trait
    # as uncorrelatable and published a strong correlation for it in the same response.
    # Excluding the mask from the counts AND the list restores the documented contract at
    # its single source.
    #
    # Both counts now derive from the same mask as the list that explains them, so the two
    # can no longer disagree, and the separate `corr.where(...)` 846x846 float64 temporary
    # the counts used to build is gone.
    # One mask per sign, and the pair list is their UNION — not a separate |r| comparison
    # that happens to use the same constant. The counts and the list that explains them are
    # now the same arithmetic, so no edit can make them disagree (#784 review: the cutoff was
    # written inline three times, and a `>` -> `>=` slip in the list's own comparison changed
    # what it reported without changing the counts).
    #
    # The `>` boundary itself is deliberately not pinned by a test: it is observable only for
    # a coefficient bit-exactly equal to the cutoff, which is not constructible in float64
    # here (a search over perturbed integer vectors bottoms out at 0.7000000000000001). What
    # a test CAN pin, and does, is that strong_pair_count equals the two counts summed — and
    # sharing one mask is what makes that hold by construction rather than by coincidence.
    _scored = upper_mask & ~zero_variance_mask
    positive_mask = (corr_values > _STRONG_R) & _scored
    negative_mask = (corr_values < -_STRONG_R) & _scored
    high_pos = int(positive_mask.sum())
    high_neg = int(negative_mask.sum())

    strong_mask = positive_mask | negative_mask
    strong_i, strong_j = np.where(strong_mask)
    strong_overlaps = overlap_counts[strong_i, strong_j]

    if strong_i.size:
        strong_pair_count = int(strong_i.size)
        overlap_min = int(strong_overlaps.min())
        overlap_median = float(np.median(strong_overlaps))
        overlap_max = int(strong_overlaps.max())
        # Selection and summarising stay vectorized (at cylinder width realistic collinearity
        # puts tens of thousands of pairs past the cutoff, so a Python pass over all of them
        # would be the expensive thing); the loop below runs over the SLICED order and is
        # therefore bounded by _MAX_STRONG_PAIRS_REPORTED, not by the pair count.
        #
        # np.lexsort applies its LAST key first, so this reads bottom-up: overlap ascending
        # (weakest evidence first, which is what makes the cap safe), ties by descending |r|,
        # then by trait order so the output is deterministic for snapshot consumers.
        order = np.lexsort(
            (
                strong_j,
                strong_i,
                -np.abs(corr_values[strong_i, strong_j]),
                strong_overlaps,
            )
        )[:_MAX_STRONG_PAIRS_REPORTED]
        strong_correlation_pairs = []
        for k in order:
            i, j = int(strong_i[k]), int(strong_j[k])
            r = float(corr_values[i, j])
            n_overlap = int(overlap_counts[i, j])
            ci_low, ci_high = _fisher_ci(r, n_overlap)
            strong_correlation_pairs.append(
                StrongCorrelationPair(
                    traits=[trait_cols[i], trait_cols[j]],
                    r=r,
                    overlap_n=n_overlap,
                    ci_low=ci_low,
                    ci_high=ci_high,
                )
            )
    else:
        strong_pair_count = 0
        overlap_min = overlap_median = overlap_max = None
        strong_correlation_pairs = []

    # ── #785: the third and final blank-cell bucket, by elimination ─────────────────
    # A NaN off-diagonal cell has exactly three causes; two are already named above, so the
    # third is the remainder. Derived rather than recomputed (see the module docstring): a
    # within-overlap variance would need a tolerance to call "constant" and could disagree
    # with the determination pandas already made when it returned this very coefficient.
    #
    # Subtracts the raw sub-threshold MASK, not low_overlap_trait_pairs: that published list
    # deliberately drops pairs involving a zero-variance trait, so subtracting it instead
    # would let such a pair fall through into this bucket and break the exactly-one-bucket
    # property the field claims. (zero_variance_mask is built once above, where the strong
    # counts also need it.)
    locally_constant_mask = (
        np.isnan(corr_values)
        & upper_mask
        & ~zero_variance_mask
        & ~(overlap_counts < _MIN_CORR_OVERLAP)
    )
    lc_i, lc_j = np.where(locally_constant_mask)
    locally_constant_pair_count = int(lc_i.size)
    # np.where returns row-major order, i.e. pairs sorted by the first trait's position in
    # resolved_trait_columns, then the second's. Unlike strong_correlation_pairs there is no
    # evidence gradient here to order by — every pair in this bucket is equally NaN — so the
    # cap cannot be made "safe" the way the sibling's ascending-overlap order makes its cap
    # safe. It is a deterministic but ARBITRARY slice, the field description says so, and
    # locally_constant_pair_count carries the true total (#784/#785 review).
    _lc_capped = list(
        zip(
            lc_i[:_MAX_LOCALLY_CONSTANT_PAIRS_REPORTED],
            lc_j[:_MAX_LOCALLY_CONSTANT_PAIRS_REPORTED],
        )
    )
    locally_constant_trait_pairs = [
        [trait_cols[int(i)], trait_cols[int(j)]] for i, j in _lc_capped
    ]
    # The manifest tier gets the truth, however long — this file's established contract (see
    # the stamping block below and its test). Cheap: two names per pair.
    locally_constant_trait_pairs_full = [
        [trait_cols[int(i)], trait_cols[int(j)]] for i, j in zip(lc_i, lc_j)
    ]

    # Names the actual flagged trait(s)/pair(s), not just a count (#466 review round 6):
    # create_correlation_heatmap draws its axis tick labels from this same trait_cols list,
    # in this same order, so a PNG-only viewer can cross-reference a name here against a
    # label they can already see on the image — closing the "told a problem exists, no way
    # to tell which cell" gap a bare count left open. Capped so a wide (cylinder-scale)
    # selection with many flagged pairs doesn't produce an unreadably long footnote; the
    # full, uncapped lists are always in zero_variance_traits/low_overlap_trait_pairs.
    _flagged_names = list(zero_variance_traits) + [
        f"{a}×{b}" for a, b in low_overlap_trait_pairs
    ]
    _MAX_CAVEAT_NAMES = 10
    if _flagged_names:
        _shown = _flagged_names[:_MAX_CAVEAT_NAMES]
        _remainder = len(_flagged_names) - len(_shown)
        _names_text = ", ".join(_shown) + (
            f", +{_remainder} more" if _remainder else ""
        )
        heatmap_caveat = (
            f"Cell(s) involving {_names_text} have too little real data behind them "
            f"to trust as drawn — the image still colors them like a genuine strong "
            f"correlation. Match these names against the image's own axis labels; see "
            f"zero_variance_traits/low_overlap_trait_pairs for the complete list."
        )
    else:
        heatmap_caveat = None

    prov = provenance.model_copy(
        update={
            "based_on_version": frame.source,
            "params": {
                **provenance.params,
                "resolved_trait_columns": trait_cols,
                "heatmap_caveat": heatmap_caveat,
                # The FULL, uncapped lists, not just the capped caveat string: the
                # caveat text itself tells the reader to "see zero_variance_traits/
                # low_overlap_trait_pairs for the complete list", which a manifest-only
                # reader could not do while those lived solely in the live response.
                # Stamping them is what makes this module's "a later manifest read gets
                # the same signal a live call did" claim actually true past 10 flagged
                # cells (#466 review round 7).
                "zero_variance_traits": zero_variance_traits,
                "low_overlap_trait_pairs": low_overlap_trait_pairs,
                # #784/#785: a later manifest reader must recover the same evidence a
                # live caller got.
                #
                # locally_constant_trait_pairs is stamped UNCAPPED, like the two lists
                # above it — same contract, same reason, and it is a list of names, so the
                # cost is two strings per pair (#784 review: stamping it capped inverted
                # this file's own test-enforced precedent and left pairs 21..N recoverable
                # from nowhere).
                #
                # This is the THIRD uncapped name-list in this manifest, so it doubles down
                # on a pre-existing unbounded-payload pattern rather than introducing one.
                # Bounding the manifest tier as a whole — all three lists together, not this
                # one in isolation — is tracked in #837 alongside the result-side ceiling;
                # doing it here would silently change what a stored run records for two
                # fields that predate this change (#833 review round 2).
                #
                # strong_correlation_pairs is stamped CAPPED, and that asymmetry is
                # deliberate: each entry is a four-field structured record, not a name, so
                # at cylinder width the uncapped list is a multi-megabyte duplicate of the
                # correlation matrix itself rather than a disclosure. What makes the cap
                # honest is that its uncapped MAGNITUDES are stamped beside it —
                # strong_pair_count plus the three overlap summaries — so a manifest-only
                # reader can always tell the list is truncated and by how much, which is the
                # property the uncapped name-lists exist to provide. (Detecting truncation
                # from the counts alone was impossible before: they were not stamped.)
                "strong_correlation_pairs": [
                    pair.model_dump() for pair in strong_correlation_pairs
                ],
                "strong_pair_count": strong_pair_count,
                "strong_positive_correlations": high_pos,
                "strong_negative_correlations": high_neg,
                "strong_pair_overlap_min": overlap_min,
                "strong_pair_overlap_median": overlap_median,
                "strong_pair_overlap_max": overlap_max,
                "locally_constant_trait_pairs": locally_constant_trait_pairs_full,
                "locally_constant_pair_count": locally_constant_pair_count,
                # The parameters the numbers above were produced UNDER (#784 review). The
                # constants block argues at length that owning _MIN_CORR_OVERLAP matters
                # because a retune would move the counts and break comparability with
                # persisted manifests — which is only true if the manifest records what it
                # was. Two runs under different floors or cutoffs were previously
                # indistinguishable after the fact; these five scalars close that.
                "min_corr_overlap": _MIN_CORR_OVERLAP,
                "strong_r_cutoff": _STRONG_R,
                "ci_level": _CI_LEVEL,
                "max_strong_pairs_reported": _MAX_STRONG_PAIRS_REPORTED,
                "max_locally_constant_pairs_reported": (
                    _MAX_LOCALLY_CONSTANT_PAIRS_REPORTED
                ),
            },
        }
    )
    run = store.create_run(
        experiment=params.experiment,
        tool_class=_TOOL_CLASS,
        provenance=prov,
        user_label=params.user_label,
        source_csv=_ports.raw_source_for(params.experiment),
        source=frame.resolved_source,
    )
    fig = None
    try:
        # call_with_figure_cleanup (#721, landed via PR #726) holds FIGURE_REGISTRY_LOCK for
        # the delegate call — allocating a figure mutates the shared global matplotlib
        # registry, which a concurrent figure-creating call elsewhere in the process could
        # otherwise interleave with (see the lock's own comment in bloom_mcp.tools._plots)
        # — and, if the delegate raises after allocating, closes what it allocated before
        # re-raising, still under the lock. Single-figure delegate, so the cleanup half
        # only matters if create_correlation_heatmap allocates and then raises mid-render.
        fig = call_with_figure_cleanup(
            lambda: create_correlation_heatmap(frame.df, trait_cols)
        )
        if heatmap_caveat is not None:
            # Cheap, in-scope: a footnote drawn directly onto the already-rendered Figure,
            # not a per-cell hatch/marker — the latter would require reverse-engineering the
            # vendored delegate's cell geometry (row/column orientation, any axis flip), and
            # getting that wrong would mislabel a DIFFERENT cell as flagged, which is worse
            # than no annotation. A caller who only ever opens the saved PNG (never the JSON
            # response) still gets the warning this way (#466 review round 4).
            fig.text(
                0.5,
                -0.02,
                f"⚠ {heatmap_caveat}",
                ha="center",
                va="top",
                fontsize=8,
                color="darkred",
                wrap=True,
                transform=fig.transFigure,
            )
        fig.savefig(run.staging_dir / _HEATMAP_PNG, dpi=150, bbox_inches="tight")
        stored = store.commit(run, {_HEATMAP_PNG: _HEATMAP_PNG})
    except Exception:
        rmtree(run.staging_dir, ignore_errors=True)
        raise
    finally:
        if fig is not None:
            # Held for the SAME reason as the creation call above, and this is not
            # belt-and-braces: plt.close -> Gcf.destroy_fig scans `Gcf.figs.values()`
            # to find the manager owning this figure, and that scan is unsynchronized.
            # A concurrent locked create (Gcf.set_active -> `figs[num] = manager` +
            # move_to_end) mutating the dict mid-scan raises RuntimeError("OrderedDict
            # mutated during iteration"). Creation-only locking therefore does NOT close
            # the race it claims to (#466 review round 7).
            with FIGURE_REGISTRY_LOCK:
                plt.close(fig)

    return PlotCorrelationMatrixResult(
        experiment=params.experiment,
        source=frame.source,
        n_traits_plotted=len(trait_cols),
        strong_positive_correlations=high_pos,
        strong_negative_correlations=high_neg,
        strong_correlation_pairs=strong_correlation_pairs,
        strong_pair_count=strong_pair_count,
        strong_pair_overlap_min=overlap_min,
        strong_pair_overlap_median=overlap_median,
        strong_pair_overlap_max=overlap_max,
        zero_variance_traits=zero_variance_traits,
        low_overlap_trait_pairs=low_overlap_trait_pairs,
        locally_constant_trait_pairs=locally_constant_trait_pairs,
        locally_constant_pair_count=locally_constant_pair_count,
        heatmap_caveat=heatmap_caveat,
        resolved_trait_columns=trait_cols,
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
        output_links=stored.output_links,
    )
