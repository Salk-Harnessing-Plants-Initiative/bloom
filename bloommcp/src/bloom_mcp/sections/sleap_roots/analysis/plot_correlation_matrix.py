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

**Zero-variance / all-NaN traits are excluded from the strong-correlation counts with no
error** — ``pandas``' Pearson correlation is ``NaN`` for a constant or all-NaN column, and
``NaN > 0.7`` is ``False``, so such a trait's pairs silently don't count toward either
``strong_positive_correlations``/``strong_negative_correlations``. Realistic here specifically
because this tool reads **raw, uncleaned** data (no QC has dropped a zero-variance trait yet —
see the raw-read decision below). ``zero_variance_traits`` in the result names exactly which
selected traits this affects, so the counts are not silently misleading. Note the guard is
``not (std(skipna=True) > 0)``, which also catches a trait carrying ``+inf``/``-inf`` (its std
is ``NaN``) — genuinely uncorrelatable, so the grouping is right, but the field's description
names that fourth case explicitly rather than letting "zero variance" misdescribe the data
(#784/#785 review).

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
read as if the whole population were thin — ``strong_pair_overlap_min``/``_median``/``_max``
are computed over **every** strong pair, uncapped. The interval is not a significance test, is
not corrected for multiplicity (at ~846 traits there are 357,435 pairs, so "95%" is a per-pair
statement), and assumes approximate bivariate normality that raw trait data often violates; it
is reported as ``None`` where the transform is undefined (``|r| = 1``, or an overlap below the
Fisher standard error's domain) rather than as a zero-width interval claiming certainty.

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
* Because the bucket is a *remainder*, any future pandas ``NaN`` cause would be absorbed here
  under a name asserting local constancy. The change's ``benchmarks/`` fuzz (400 randomly
  degenerate frames, 0 unexplained and 0 double-counted cells) is the guard against that.

This bucket's population is independent of the other two — one trait that varies globally but
takes a single value wherever it is observed is locally constant against *every* partner — so
the list is capped and ``locally_constant_pair_count`` carries the uncapped total. It does
**not** feed ``heatmap_caveat``: see the masking-mismatch paragraph below.

**At least 2 resolved trait columns are required, and at least 2 of them must carry non-zero
variance.** A correlation view of a single trait is not meaningful (there is no pair to
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

**``locally_constant_trait_pairs`` is deliberately NOT in that trigger (#785).** The caveat's
text says the flagged cells "have too little real data behind them to trust as drawn — the
image still colors them like a genuine strong correlation." That sentence is simply false of a
locally-constant pair: the delegate's own unguarded ``.corr()`` returns the same ``NaN``, so
the cell renders *blank*, not miscolored. Widening the trigger would either leave a warning
that misdescribes the new bucket, or force a rewrite of the text every existing caller already
receives for the other two. Note this is an argument from the caveat's current wording, not
from "the JSON and the image disagree" — they also agree for a zero-variance trait, which
*does* trigger the caveat, deliberately (#747: "even a blank row is easy to miss next to a
colored one"). If #747 is ever fixed upstream, this whole trigger set should be revisited,
since the caveat's reason to exist disappears with it.

**Known test-coverage gap (#768, carried forward from PR #724 via staging):**
``tests/tools/test_viz_snapshot.py``'s pixel-diff regression check cannot reliably catch a
single-cell rendering defect in this heatmap — one real cell is a tiny fraction of the whole
image, small enough that even the most-detectable-possible miscoloring scores an RMS in the
same range as ordinary cross-platform rendering noise. See that test file's module docstring
and ``openspec/changes/add-bloommcp-plot-snapshot-tests/design.md`` (Decisions 2 & 7) for the
full measurement and why this is a structural limit, not a TODO. Note this compounds the
``heatmap_caveat`` disclosure above: the rendered PNG's untrustworthy cells are caught by
neither the vendored delegate's own masking (there is none, #747) nor the snapshot test.
That issue's own remedy option 2 — a numeric assertion on the correlation values themselves,
complementing the pixel check — is made materially easier by ``strong_correlation_pairs``
(per-pair ``r`` and ``overlap_n``, in both the result and the manifest), though #768 remains
open: nothing here asserts anything about the rendered image.

Persists a versioned run under its own tool class ``correlation_matrix`` (not the shared,
unclaimed legacy ``viz`` slot — see ``openspec/changes/converge-bloommcp-viz-tools/design.md``
for why each converged tool mints its own class rather than interleaving version history with
its siblings).
"""

from __future__ import annotations

import math
from shutil import rmtree
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

# Two-sided 95% normal quantile, for the Fisher z interval below.
_Z_95 = 1.959963984540054


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

    Both bounds go through ``_finite_or_none`` (the helper ``clustering``/``descriptive_stats``
    already use) so no ``NaN``/``Infinity`` token can reach the result model or the manifest:
    manifests are serialized by ``storage_backend._json_bytes`` via ``json.dumps`` with the
    default ``allow_nan=True``, which would emit a bare token strict JSON readers reject.
    """
    if n <= 3 or not math.isfinite(r) or abs(r) >= 1.0:
        return None, None
    z = math.atanh(r)
    se = _Z_95 / math.sqrt(n - 3)
    return _finite_or_none(math.tanh(z - se)), _finite_or_none(math.tanh(z + se))


class StrongCorrelationPair(BaseModel):
    """One trait pair behind ``strong_positive_correlations``/``strong_negative_correlations``,
    with the evidence supporting it (#784)."""

    traits: list[str] = Field(
        description="The two trait columns, in resolved_trait_columns order."
    )
    r: float = Field(
        description="Pearson correlation over the pair's shared non-null rows."
    )
    overlap_n: int = Field(
        description="Number of rows where BOTH traits are non-null — the sample this "
        "coefficient actually rests on, which can be far smaller than the experiment's row "
        "count when raw data has disjoint per-trait missingness."
    )
    ci_low: Optional[float] = Field(
        default=None,
        description="Lower bound of the two-sided 95% Fisher-z confidence interval for r. "
        "NOT a significance test, and NOT corrected for multiplicity: at ~846 traits there "
        "are 357,435 pairs, so '95%' is a per-pair statement, not a family-wise one. The "
        "interval also assumes approximately bivariate-normal data, which raw (zero-inflated, "
        "skewed, often bounded) root-trait data frequently violates — read it as the range "
        "the point estimate is consistent with, not as a guarantee. None when the transform "
        "is undefined (|r| = 1, or an overlap too small for its standard error) — reported as "
        "null rather than a zero-width interval implying perfect precision.",
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
        description="Off-diagonal trait pairs with Pearson correlation > 0.7."
    )
    strong_negative_correlations: int = Field(
        description="Off-diagonal trait pairs with Pearson correlation < -0.7."
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
        "list is truncated, strong_pair_overlap_min/median/max still describe every strong "
        "pair, not just the reported sample.",
    )
    strong_pair_overlap_min: Optional[int] = Field(
        default=None,
        description="Smallest pairwise overlap across ALL strong pairs (uncapped), not just "
        "the reported sample. None when there are no strong pairs.",
    )
    strong_pair_overlap_median: Optional[float] = Field(
        default=None,
        description="Median pairwise overlap across ALL strong pairs (uncapped). Reported "
        "because a capped list is a biased sample: 50 low-n pairs drawn from tens of "
        "thousands read as if the whole population were poorly supported, when they may be "
        "its thin tail. None when there are no strong pairs.",
    )
    strong_pair_overlap_max: Optional[int] = Field(
        default=None,
        description="Largest pairwise overlap across ALL strong pairs (uncapped). None when "
        "there are no strong pairs.",
    )
    zero_variance_traits: list[str] = Field(
        default_factory=list,
        description="Selected traits whose correlation against anything is unconditionally "
        "NaN because the trait itself carries no usable variance. Three cases land here, "
        "via `not (std(skipna=True) > 0)`: constant (std 0), entirely NaN (std NaN), and — "
        "less obviously — exactly one non-null value, whose sample std is NaN rather than 0 "
        "because ddof=1 needs two observations (#466 review round 7: the field previously "
        "described only the first two). A FOURTH case lands here too (#784/#785 review): a "
        "trait carrying a non-finite value (+inf/-inf), whose std is NaN for that reason "
        "rather than for lack of variation. It is named here because it is genuinely "
        "uncorrelatable, but calling it 'zero variance' without qualification would "
        "misdescribe the data — check for infinities, not just constancy, when a trait you "
        "expected to vary appears in this list. All four are genuinely uncorrelatable, so the "
        "grouping is intentional, not an accident of the NaN check. Pearson correlation "
        "against such a trait is NaN, counting toward neither strong_positive_correlations "
        "nor strong_negative_correlations — empty when none were affected.",
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
        description="The third and final reason a heatmap cell can be blank (#785): the pair's "
        "correlation is NaN even though BOTH traits vary globally AND the pair clears the "
        "minimum-overlap floor — one trait happens to take the same value on exactly the rows "
        "where both are non-null, so there is no variance within the shared overlap. Together "
        "with zero_variance_traits and low_overlap_trait_pairs this completes the taxonomy: "
        "every off-diagonal NaN falls into exactly one of the three, so the tool can always "
        "say WHY a cell is blank. Derived by elimination from the guarded correlation matrix "
        "rather than by recomputing a within-overlap variance, so totality holds by "
        "construction — with the caveat that any future pandas NaN cause would be absorbed "
        "here under a name asserting local constancy. Unlike the other two lists this one does "
        "NOT feed heatmap_caveat: such a cell is NaN in the rendering delegate's own "
        f"computation too, so it renders blank rather than miscolored. Capped at "
        f"{_MAX_LOCALLY_CONSTANT_PAIRS_REPORTED}; see locally_constant_pair_count for the "
        "true total.",
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

    zero_variance_traits = [
        c for c in trait_cols if not (frame.df[c].std(skipna=True) > 0)
    ]
    zero_variance_set = set(zero_variance_traits)
    if len(trait_cols) - len(zero_variance_set) < 2:
        raise BloomMCPError(
            code="assumption_violated",
            message=f"plot_correlation_matrix requires at least 2 non-constant trait "
            f"columns; {params.experiment!r} resolved {trait_cols!r}, of which "
            f"{sorted(zero_variance_set)!r} are constant or entirely NaN.",
            remedy="Select at least 2 trait columns with non-zero variance, or use a "
            "different experiment.",
        )

    corr = frame.df[trait_cols].corr(min_periods=_MIN_CORR_OVERLAP)

    # Vectorized pairwise overlap counts (notna^T @ notna) — a python double loop over
    # trait_cols x trait_cols would be O(n^2) even just to build this, prohibitive at
    # cylinder's ~846-trait scale; only the (typically small) flagged-pair list below is.
    notna = frame.df[trait_cols].notna().to_numpy(dtype=int)
    overlap_counts = notna.T @ notna
    low_overlap_mask = np.triu(overlap_counts < _MIN_CORR_OVERLAP, k=1)
    low_overlap_trait_pairs = [
        [trait_cols[i], trait_cols[j]]
        for i, j in zip(*np.where(low_overlap_mask))
        if trait_cols[i] not in zero_variance_set
        and trait_cols[j] not in zero_variance_set
    ]

    upper = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
    high_pos = int((upper > 0.7).sum().sum())
    high_neg = int((upper < -0.7).sum().sum())

    # ── #784: the evidence behind those two counts ──────────────────────────────────
    # Everything here reuses arrays already built above (corr, overlap_counts) — no second
    # pass over the frame. Kept vectorized end to end: at cylinder width a Python loop over
    # even the FLAGGED pairs is prohibitive, since realistic trait collinearity puts tens of
    # thousands of pairs past the magnitude cutoff.
    corr_values = corr.to_numpy()
    upper_mask = np.triu(np.ones(corr_values.shape, dtype=bool), k=1)
    strong_mask = (np.abs(corr_values) > 0.7) & upper_mask
    strong_i, strong_j = np.where(strong_mask)
    strong_overlaps = overlap_counts[strong_i, strong_j]

    if strong_i.size:
        overlap_min = int(strong_overlaps.min())
        overlap_median = float(np.median(strong_overlaps))
        overlap_max = int(strong_overlaps.max())
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
    # property the field claims.
    zero_variance_mask = np.zeros(corr_values.shape, dtype=bool)
    if zero_variance_set:
        zv_indices = [i for i, c in enumerate(trait_cols) if c in zero_variance_set]
        zero_variance_mask[zv_indices, :] = True
        zero_variance_mask[:, zv_indices] = True
    locally_constant_mask = (
        np.isnan(corr_values)
        & upper_mask
        & ~zero_variance_mask
        & ~(overlap_counts < _MIN_CORR_OVERLAP)
    )
    lc_i, lc_j = np.where(locally_constant_mask)
    locally_constant_pair_count = int(lc_i.size)
    locally_constant_trait_pairs = [
        [trait_cols[int(i)], trait_cols[int(j)]]
        for i, j in zip(
            lc_i[:_MAX_LOCALLY_CONSTANT_PAIRS_REPORTED],
            lc_j[:_MAX_LOCALLY_CONSTANT_PAIRS_REPORTED],
        )
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
                # live caller got. The two lists are capped, so their uncapped magnitudes
                # (the overlap summaries and the locally-constant count) are stamped
                # alongside them rather than being derivable only from the live response.
                "strong_correlation_pairs": [
                    pair.model_dump() for pair in strong_correlation_pairs
                ],
                "strong_pair_overlap_min": overlap_min,
                "strong_pair_overlap_median": overlap_median,
                "strong_pair_overlap_max": overlap_max,
                "locally_constant_trait_pairs": locally_constant_trait_pairs,
                "locally_constant_pair_count": locally_constant_pair_count,
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
