"""plot_trait_boxplots — boxplots of trait values grouped by genotype (#466).

Converged onto the ``@as_mcp_tool`` contract every other tool in this folder uses (Pydantic
I/O, structured ``BloomMCPError``, one stamped ``Provenance``, versioned ``ResultStore``
persistence) — the same read-only, pre-clean EDA pattern as ``qc_inspect``: reads the raw
frame via the :class:`ExperimentReader` port (no ``require_clean``).

Delegates rendering to ``sleap_roots_analyze.visualization.create_trait_boxplots_by_genotype``
(or its ``_batched`` counterpart above ``_viz_shared.TRAIT_BATCH_THRESHOLD`` traits); this file
owns no plotting logic of its own. A batched render persists one committed output per page —
mirroring ``pca_analysis``'s ``include_plots`` multi-figure handling — rather than a single
figure.

Requires an auto-detected genotype column on the read frame (no override parameter — this is a
wrapper-layer convergence, not new capability); a frame with none detected raises a structured
error rather than returning a message string, same as today's behavior otherwise.

Persists a versioned run under its own tool class ``trait_boxplots`` (not the shared, unclaimed
legacy ``viz`` slot — see ``openspec/changes/converge-bloommcp-viz-tools/design.md`` for why).

**Two disclosure gaps closed in #466's review** (mirrors ``plot_trait_histograms``):
``resolved_trait_columns`` records the exact trait columns used — including when
``trait_columns`` was omitted and auto-detection resolved them — both in the result and stamped
into the persisted run's ``params``, so a later reader of the manifest doesn't have to
re-run (data-dependent) auto-detection against data that may have drifted; and ``page_traits``
names which traits landed on which page of a batched (paginated) render, previously only
discoverable by opening an image and reading its axis labels.

**Every box now discloses how much data is behind it (#748).** A box plot carries no inherent
sample-size signal — one built from 2 points is pixel-identical to one built from 200 — and the
delegate titles each subplot with the trait name alone, unlike ``create_trait_histograms``, which
titles each panel ``f"{trait}\n(n={count})"``. Three surfaces close that:

* **On the image, per box.** Each genotype tick label gains its own ``(n=…)``, matched **by
  text** against the computed counts — never by assuming an axis, a subplot's identity, or where
  the orientation switch sits. Three guards have to agree before any tick is rewritten (a
  ``FixedLocator`` on the axis, a label set exactly equal to that trait's plotted genotypes, and
  exactly one matching axis); see :func:`_annotate_genotype_ticks` for what each one rules out.
  Failure is per-panel and reported: every panel that can be matched is annotated, and
  ``box_labels_annotated`` says whether the figure is *fully* labelled.
* **On the image, per figure.** ``sample_size_note`` is drawn below the axes on **every** render,
  not only flagged ones — a note that appeared only when something tripped a threshold would
  leave every other image as uninformative as before, and would make its own absence carry a
  sufficiency claim the floor explicitly disclaims (which is also why it carries an
  unconditional tail saying what clearing the floor does *not* establish). It names every
  denominator — and names them separately, since the boxes-with-finite-data population and the
  boxes-drawn population diverge as soon as a cell holds only ``inf`` — identifies itself as
  page-scoped on a paginated render, and escalates to a ``⚠`` clause naming the flagged cells
  **as a fraction** of the total, so a reader can calibrate severity rather than just presence.
  The figure is grown and its axes translated up to make room: the note is unbounded in height,
  and drawn at a fixed offset it landed on the bottom row of boxes.
* **In the result and the manifest.** Four mutually exclusive buckets — ``no_data_traits``,
  ``absent_genotype_groups``, ``non_finite_groups``, ``small_sample_groups`` — each capped
  worst-first in its own terms (ascending finite count for thin boxes, *descending* inf count
  for non-finite ones) with an uncapped count, plus the uncapped
  ``box_n_min``/``_median``/``_max`` and the complete ``group_sample_sizes.csv``. Because the
  buckets are exclusive, ``thin_box_count`` is reported separately: a box that is both thin and
  ``inf``-bearing appears only under non-finite, so the small-sample count is not the count of
  thin boxes.

**Counting is on the FINITE count, deliberately.** ``pandas`` ``count()`` treats ``+/-inf`` as
present (the trap ``qc_inspect`` documents for its own missingness fields), so a cell of 12
infinities would otherwise clear the floor and be described as a healthy box while matplotlib
draws it with ``NaN`` quartiles. Every threshold comparison uses ``n_finite``; ``n_non_finite``
is reported as a subset of ``n_plotted``, not an addition to it.

**A non-finite value is disclosed, not rejected** — the opposite of ``plot_trait_histograms``,
whose delegate cannot bin a non-finite range at all and which therefore refuses such a selection
outright. Here the figure stays useful for every unaffected group, so the run completes and the
affected traits/cells are named. Worth knowing what such a box looks like: on ``[1, 2, inf, 4,
5]`` the delegate draws ``q1=2.0``, ``median=4.0`` (the finite median is 3.0) and ``q3=NaN`` — a
confidently placed median in the wrong place with an open top, not something that reads as
corrupt data.

**Absent groups vanish, which is why they get their own bucket.** The delegate takes its tick
labels from what survives ``dropna()``, so a genotype with no non-null value for a trait leaves
no tick, no box and no gap — a reader can at least *see* a suspicious box, but cannot see an
absence. A trait dead for *every* genotype collapses to one ``no_data_traits`` entry instead of
one absent entry per genotype: at 19 genotypes a single dead trait would otherwise fill the
20-slot cap by itself and evict every genuinely informative absence in the run.

**``tight_layout`` is called on unbatched renders only.** The batched delegate already calls it;
paying for it again on each of cylinder's 53 pages costs +21% per page (measured) and would push
the cylinder smoke run past its client timeout. On the unbatched path the delegate deliberately
leaves it to the caller, and skipping it both collides the lengthened rotated tick labels with
the next row's subplot titles and leaves a large dead bottom margin.

**Still not disclosed:** a zero-variance (constant) trait renders as a degenerate box with no
flag. That is a legitimate, if uninformative, plot, and unlike the sample-size gap it is visible
in the drawn box itself — but it is a real residual, tracked separately rather than left
unrecorded.
"""

from __future__ import annotations

from shutil import rmtree
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FixedLocator
from pydantic import BaseModel, ConfigDict, Field
from sleap_roots_analyze.visualization import (
    create_trait_boxplots_by_genotype,
    create_trait_boxplots_by_genotype_batched,
)

from bloom_mcp.contract import BloomMCPError, Provenance, RunLinks, as_mcp_tool
from bloom_mcp.data_access import ExperimentReadError
from bloom_mcp.result_store import CommitFailedError, ManifestReadError
from bloom_mcp.tools import _ports
from bloom_mcp.tools._plots import FIGURE_REGISTRY_LOCK, call_with_figure_cleanup
from bloom_mcp.tools._qc_shared import _validate_experiment_name

from ._viz_shared import (
    MAX_FLAGGED_REPORTED,
    MIN_PLOTTED_SAMPLES,
    TRAIT_BATCH_THRESHOLD,
    draw_disclosure_note as _draw_sample_size_note,
    flagged_names as _flagged_names,
    group_sample_size_table,
    native,
    resolve_trait_columns,
)

_TOOL_CLASS = "trait_boxplots"
_PNG_STEM = "trait_boxplots"
# The complete per-(trait, genotype) sample-size table, committed as its own output (#748).
# A download, not a response field: at cylinder width (846 traits x ~19 genotypes = 16,074
# cells) the table is ~3 MB. Mirrors qc_inspect's nan_samples.csv.
_SAMPLE_SIZES_CSV = "group_sample_sizes.csv"
# create_trait_boxplots_by_genotype_batched's own internal page size — independent of
# TRAIT_BATCH_THRESHOLD (which only decides WHETHER to batch). Not overridden by this
# tool's call, so it is safe to use for computing which trait landed on which page;
# test_plot_trait_boxplots_tool.py pins this against the live delegate signature so a
# future sleap-roots-analyze bump that changes it is caught, not silently desynced.
_DELEGATE_BATCH_SIZE = 16


def _sample_size_note(
    page_table,
    page_no_data_traits,
    n_page_traits,
    n_total_traits,
    n_groups,
    rows_missing_genotype,
    scoped,
):
    """Build the sample-size note drawn below the axes (#748).

    Always produced, not only when something is flagged: a note that appeared only on flagged
    runs would leave every other image exactly as uninformative as it is today, and would make
    its own absence carry a sufficiency claim ``MIN_PLOTTED_SAMPLES`` explicitly disclaims.
    For the same reason the note carries an unconditional tail about what the floor does NOT
    establish — without it a reader can still take "no warning" for "sample sizes adequate".

    **Every denominator is named, and the two populations are never conflated.** The head
    summarizes boxes with finite data; the warning clause counts against boxes actually drawn.
    Those differ whenever a cell holds only ``+/-inf``, so both are labelled in the text rather
    than left as two bare "box(es)" counts a reader would assume share a denominator.

    **Page-scoped renders say so in the text.** Restricting a batched page's statistics to its
    own traits while leaving the sentence unqualified just moves the same misreading from the
    numbers to the label: a reader of page 37 of 53 would take it for the whole run.

    **"n rows per box", not "n".** For cylinder data one plant contributes several scans and a
    scan several images, so an unqualified n=8 invites being read as 8 plants. The unit is the
    frame's own rows; ``n_rows_read``/``rows_missing_genotype`` in the result do the rest of the
    reconciliation.
    """
    scope = " (this page)" if scoped else ""
    summarized = page_table[page_table["n_finite"] > 0]
    n_drawn = int((page_table["n_plotted"] > 0).sum())

    if summarized.empty:
        # Distinguish "nothing was drawn" from "boxes were drawn but none rests on a finite
        # value" -- an all-inf selection draws boxes, so claiming none were drawn would be
        # false exactly where the disclosure matters.
        drew = f"{n_drawn} box(es) were drawn but none rests on a finite value"
        head = (
            f"n rows per box{scope}: no summary available — "
            + (drew if n_drawn else "no box was drawn")
            + f" ({n_groups} genotype group(s) x {n_page_traits} of {n_total_traits} trait(s))."
        )
    else:
        head = (
            f"n rows per box{scope}: min={int(summarized['n_finite'].min())}, "
            f"median={float(summarized['n_finite'].median()):g}, "
            f"max={int(summarized['n_finite'].max())} across {len(summarized)} box(es) with "
            f"finite data — {n_groups} genotype group(s) x {n_page_traits} of "
            f"{n_total_traits} trait(s)."
        )

    absent = page_table[
        (page_table["n_plotted"] == 0)
        & (~page_table["trait"].isin(page_no_data_traits))
    ]
    non_finite = page_table[page_table["n_non_finite"] > 0]
    small = page_table[
        (page_table["n_plotted"] > 0)
        & (page_table["n_non_finite"] == 0)
        & (page_table["n_finite"] < MIN_PLOTTED_SAMPLES)
    ]

    clauses = []
    if not small.empty:
        rows = small.sort_values(
            ["n_finite", "trait", "genotype"], kind="stable"
        ).to_dict("records")
        # Reported as a FRACTION, not just a count: at cylinder scale some page will almost
        # always carry a thin box, so a bare marker stops carrying information. "846 of
        # 16,074" lets a reader calibrate severity; a warning symbol alone does not.
        clauses.append(
            f"{len(small)} of {n_drawn} drawn box(es) below n={MIN_PLOTTED_SAMPLES}: "
            + _flagged_names(
                rows,
                lambda r: f"{r['trait']} x {r['genotype']} (n={int(r['n_finite'])})",
            )
        )
    if not absent.empty:
        rows = absent.sort_values(["trait", "genotype"], kind="stable").to_dict(
            "records"
        )
        clauses.append(
            f"{len(absent)} group(s) absent (no box drawn): "
            + _flagged_names(rows, lambda r: f"{r['trait']} x {r['genotype']}")
        )
    if page_no_data_traits:
        clauses.append(
            f"{len(page_no_data_traits)} trait(s) with no data at all: "
            + _flagged_names(list(page_no_data_traits), lambda t: str(t))
        )
    if not non_finite.empty:
        # Worst-first by non-finite count, matching the bucket's own ordering, and each name
        # carries its FINITE n: such a cell is excluded from the "below n=5" clause above (the
        # buckets are mutually exclusive), so without the n here a box resting on 4 finite
        # values would be flagged only as "non-finite" and its thinness would go unstated.
        rows = non_finite.sort_values(
            ["n_non_finite", "trait", "genotype"],
            ascending=[False, True, True],
            kind="stable",
        ).to_dict("records")
        clauses.append(
            f"{len(non_finite)} box(es) carry non-finite values (their quartiles are NaN): "
            + _flagged_names(
                rows,
                lambda r: (
                    f"{r['trait']} x {r['genotype']} "
                    f"({int(r['n_non_finite'])} inf, n={int(r['n_finite'])})"
                ),
            )
        )
    if rows_missing_genotype:
        # These rows are in no box at all. Without them the note reads as a complete
        # accounting of the frame, which is the same class of hidden exclusion this
        # disclosure exists to close.
        clauses.append(
            f"{rows_missing_genotype} row(s) excluded from every box (null genotype)"
        )

    tail = (
        f" A box at or above n={MIN_PLOTTED_SAMPLES} is not thereby reliable: the floor only "
        "means its quartiles are observations rather than interpolations, and flier dots stay "
        "artifact-prone well above it."
    )
    if not clauses:
        return head + tail
    return (
        f"{head} ⚠ "
        + "; ".join(clauses)
        + f". See {_SAMPLE_SIZES_CSV} for every group."
        + tail
    )


def _annotate_genotype_ticks(fig, finite_counts, plotted_genotypes, no_data_traits):
    """Append each box's own ``(n=…)`` to its genotype tick label; report whether it worked.

    The delegate labels these ticks with the genotype **values themselves** in both
    orientations (vertical: x-ticks via ``df_plot.boxplot(by=...)``; horizontal above 8
    genotypes: y-ticks via an explicit ``tick_labels=genotype_order``), so the genotype axis is
    identified from the figure rather than assumed. Three conditions have to hold together
    before a tick is rewritten, and each rules out a specific way of annotating the WRONG box —
    which is worse than not annotating at all:

    1. **The axis must carry a ``FixedLocator``.** Matplotlib uses one for a categorical axis
       and an ``AutoLocator`` for a continuous value scale. Without this check a numeric
       genotype column (``GENOTYPE_PATTERNS`` matches on column *name*, with no dtype check, so
       an integer accession column is ordinary real data) lets the value axis' own tick text
       pass a loose match and the sample sizes land on the trait scale. Matplotlib hands you
       this guard for free — it is the source of the "set_ticklabels() should only be used with
       a fixed number of ticks" warning.
    2. **The label set must EQUAL the genotypes actually plotted for that trait**, not merely be
       a subset of every known genotype. A subset test passes for any axis whose ticks happen to
       be a handful of the genotype names — including, with integer genotypes, the value scale.
    3. **Exactly one axis may match.** If both do, which one carries the genotypes is genuinely
       ambiguous, so neither is touched.

    A panel the delegate rendered as "No data" (a real trait title over default numeric ticks,
    because the trait is null for every genotype) is **skipped**, not counted as a failure: there
    is no box there to label. Without that exemption one dead trait anywhere in a run would
    report the whole figure as unannotated.

    Failure is per-figure and never partial-and-silent: a panel that cannot be matched leaves
    ``all_matched`` False, but every panel that CAN be matched is still annotated, and the
    caller's ``box_labels_annotated`` reports whether the figure is fully labelled. An earlier
    version returned at the first unmatched panel, which both abandoned later panels and left
    already-rewritten earlier ones in place — so the reported flag depended on trait ordering
    and a half-annotated figure claimed to be unannotated.

    The count shown is the **finite** one, so an all-``inf`` group reads ``(n=0)`` next to a box
    matplotlib still drew — the honest signal, since its quartiles are ``NaN``.
    """
    annotated_any = False
    all_matched = True
    for ax in fig.axes:
        if not ax.get_visible():
            continue
        title = ax.get_title().split("\n")[0]
        if not title:
            continue
        if title in no_data_traits:
            continue
        expected = plotted_genotypes.get(title)
        if not expected:
            # A titled, non-dead panel whose trait we have no counts for: the delegate's title
            # is not the trait name we think it is. Annotating would put every box at (n=0).
            all_matched = False
            continue
        candidates = []
        for axis in (ax.xaxis, ax.yaxis):
            if not isinstance(axis.get_major_locator(), FixedLocator):
                continue
            labels = [t.get_text() for t in axis.get_ticklabels()]
            # Duplicate tick text means two DISTINCT groupby keys stringified the same way
            # (integer 1 and string "1" are different groups but the same label), so the
            # label -> count lookup is ambiguous and would confidently attach one group's
            # count to the other's box. Set equality alone does not catch it: both sides
            # collapse identically. Skip rather than guess.
            if len(set(labels)) != len(labels):
                continue
            if labels and set(labels) == expected:
                candidates.append((axis, labels))
        if len(candidates) != 1:
            all_matched = False
            continue
        axis, labels = candidates[0]
        axis.set_ticklabels(
            [f"{label} (n={finite_counts[(title, label)]})" for label in labels],
            # parse_math=False for the same reason the note carries it: a genotype named
            # e.g. "$a__b$" is self-contained mathtext, and matplotlib raises at savefig --
            # after create_run -- rather than rendering it literally. Verified: all three of
            # "$\frac{1}$", "$a__b$" and "$\badcmd$" raise unguarded and render guarded.
            parse_math=False,
        )
        annotated_any = True
    return annotated_any and all_matched


class PlotTraitBoxplotsParams(BaseModel):
    """Inputs for ``plot_trait_boxplots``. No ``seed`` — rendering is deterministic."""

    # extra="forbid": an unknown field isn't currently exploitable (it would be dropped
    # before persistence either way), but silently accepting it masks a caller typo
    # (#466 review round 5, matching the recommendation already made on sibling PR #726).
    model_config = ConfigDict(extra="forbid")

    experiment: str = Field(
        ..., description="Experiment identifier from list_available_experiments."
    )
    trait_columns: Optional[list[str]] = Field(
        default=None,
        description="Subset of trait columns to plot; omit to use all detected traits. "
        "An explicit empty list is rejected rather than treated as 'all traits'.",
    )
    user_label: Optional[str] = Field(
        default=None,
        description="Optional slug appended to the version directory name.",
    )


class GroupSampleSize(BaseModel):
    """One (trait, genotype) box's sample-size disclosure (#748)."""

    trait: str
    genotype: str
    n: int = Field(
        description="FINITE observations backing this box. Deliberately not pandas' count(), "
        "which includes +/-inf: a cell of 12 infinities would otherwise read as a healthy "
        "box while matplotlib draws it with NaN quartiles. Zero with a non-zero n_non_finite "
        "means matplotlib drew a box that rests on no usable value."
    )
    n_non_finite: int = Field(
        default=0,
        description="+/-inf values in this cell. A SUBSET of the non-null count, not an "
        "addition to it. Any non-zero value corrupts the drawn box: the median shifts to a "
        "value no observation supports while the upper quartile and whisker become NaN.",
    )


class PlotTraitBoxplotsResult(RunLinks):
    """A small summary + links to the persisted boxplot run."""

    experiment: str
    source: str
    genotype_column: str
    n_traits_plotted: int
    n_rows_read: int = Field(
        description="Rows in the raw frame. NOT the same denominator plot_trait_histograms "
        "uses for the same experiment: a histogram bins every row, while a box excludes rows "
        "whose genotype value is null. The two tools' counts differ by exactly "
        "rows_missing_genotype — that is the reconciliation, not data loss."
    )
    n_genotype_groups: int = Field(
        description="Distinct non-null genotype values. Zero is possible and does not fail "
        "the run: an all-null genotype column still renders."
    )
    rows_missing_genotype: int = Field(
        description="Rows excluded from EVERY box because the row's own genotype value is "
        "null. The delegate drops them silently before taking its tick labels."
    )
    n_boxes_drawn: int = Field(
        description="(trait, genotype) cells the delegate draws a tick for, i.e. those with "
        "at least one non-null value."
    )
    n_boxes_summarized: int = Field(
        description="Cells behind box_n_min/_median/_max: those with at least one FINITE "
        "observation. Reported so that population is recoverable and never confused with the "
        "genotype count or the drawn count."
    )
    box_n_min: Optional[int] = Field(
        default=None,
        description="Smallest finite observation count across every summarized box "
        "(uncapped, not just the reported sample). Absent cells are EXCLUDED: no box is drawn "
        "for them, and including them reports 'median=0' on a run where every drawn box is "
        "healthy — measured on a sparse frame whose every box has n=6. They are carried "
        "completely by absent_genotype_groups instead. None when no box has finite data.",
    )
    box_n_median: Optional[float] = Field(
        default=None,
        description="Median over the same population; fractional when it is even-sized. "
        "Reported because a capped list is a biased sample: 20 thin boxes drawn from 16,074 "
        "read as if the whole run were thin. None when no box has finite data.",
    )
    box_n_max: Optional[int] = Field(
        default=None, description="Largest count over the same population."
    )
    no_data_traits: list[str] = Field(
        default_factory=list,
        description="Traits with no non-null value for ANY genotype — the delegate renders a "
        "single literal 'No data' panel for each. Reported at trait granularity rather than "
        "as one absent entry per genotype: at 19 genotypes a single dead trait would "
        f"otherwise emit 19 entries, exhausting the {MAX_FLAGGED_REPORTED}-slot cap by itself "
        "and evicting every genuinely informative absence in the run. Capped, with "
        "no_data_trait_count carrying the true total.",
    )
    no_data_trait_count: int = Field(
        default=0, description="Uncapped no_data_traits total."
    )
    absent_genotype_groups: list[GroupSampleSize] = Field(
        default_factory=list,
        description="Cells with NO non-null observation, for a trait that has data elsewhere: "
        "the delegate drops the group before taking its tick labels, so it leaves no tick, no "
        "box and no gap — a reader cannot see that the genotype was ever there. Its own "
        "bucket rather than the zero tail of small_sample_groups, because a reader can at "
        "least see a suspicious box; they cannot see an absence. Ordered by (trait, genotype) "
        "— every entry ties at zero, so there is nothing else to order by and an unspecified "
        f"tie-break would make the persisted list irreproducible. Capped at "
        f"{MAX_FLAGGED_REPORTED}.",
    )
    absent_genotype_group_count: int = Field(
        default=0, description="Uncapped absent-cell total."
    )
    non_finite_groups: list[GroupSampleSize] = Field(
        default_factory=list,
        description="Cells carrying at least one +/-inf. Such a box is drawn but corrupted — "
        "its median shifts to a value no observation supports and its upper quartile/whisker "
        "are NaN — so it does not read as broken data. Ordered by (trait, genotype), capped "
        f"at {MAX_FLAGGED_REPORTED}.",
    )
    non_finite_group_count: int = Field(
        default=0, description="Uncapped non-finite-cell total."
    )
    non_finite_trait_count: int = Field(
        default=0,
        description="Uncapped number of traits carrying any +/-inf — the true size of "
        "non_finite_traits before truncation.",
    )
    thin_box_count: int = Field(
        default=0,
        description="Every drawn box below the floor, whatever ELSE is wrong with it — "
        "including the inf-carrying boxes small_sample_groups excludes to keep the buckets "
        "mutually exclusive. Reported because that exclusion otherwise loses the thinness "
        "signal: a cell with 4 finite values and 2 infs appears only in non_finite_groups, so "
        "a caller gating on small_sample_group_count == 0 would conclude 'no thin boxes' while "
        "box_n_min reads 4. Gate on this field for that question; use the buckets to find out "
        "WHY a given box is flagged.",
    )
    non_finite_traits: list[str] = Field(
        default_factory=list,
        description="Traits carrying any +/-inf, at trait granularity (capped; see "
        "non_finite_trait_count). Unlike "
        "plot_trait_histograms — whose delegate cannot bin a non-finite range and which "
        "therefore rejects such a selection outright — this tool renders and discloses: the "
        "figure stays useful for every unaffected group.",
    )
    small_sample_groups: list[GroupSampleSize] = Field(
        default_factory=list,
        description="Boxes with fewer finite observations than _viz_shared."
        "MIN_PLOTTED_SAMPLES, more than zero, AND carrying no +/-inf — an inf-bearing cell is "
        "reported in non_finite_groups instead, so that every cell lands in exactly one "
        "bucket. That exclusion means this is NOT the count of thin boxes: use thin_box_count "
        "for that. The core of #748: a box built from 2 points "
        "is pixel-identical to one built from 200. Ordered ASCENDING by count, then by "
        "(trait, genotype): ascending order is what makes the cap safe, since it truncates "
        "the best-supported end, and the tie-break is explicit because ties are the normal "
        "case in a replicated design (fifteen of turface_19's nineteen genotypes tie at 8) — "
        "without one, which entries survive the cap would not be reproducible between runs "
        f"over the same data. Capped at {MAX_FLAGGED_REPORTED}; see small_sample_group_count "
        "for the true total. The floor is a DEGENERACY bound, not a sufficiency threshold: a "
        "box just above it is not thereby trustworthy, and its flier dots stay artifact-prone "
        "well above it.",
    )
    small_sample_group_count: int = Field(
        default=0, description="Uncapped small-box total."
    )
    max_nan_fraction: Optional[float] = Field(
        default=None,
        description="Largest missing fraction across all cells. Reported because the flagged "
        "lists key on absolute count: a cell with 200 non-null rows out of 20,000 clears the "
        "floor and would otherwise be named nowhere in the response.",
    )
    max_nan_fraction_group: Optional[list[str]] = Field(
        default=None,
        description="The [trait, genotype] carrying max_nan_fraction (first by name on a tie).",
    )
    box_labels_annotated: bool = Field(
        default=False,
        description="True when every rendered panel's genotype tick labels were rewritten to "
        "carry their own (n=...). False means the delegate's tick labels were not recognizable "
        "genotype values, so the labels were left untouched rather than risk mislabelling a "
        "box — the drawn note is then the image's only sample-size signal.",
    )
    sample_size_note: str = Field(
        default="",
        description="The run-wide sample-size note. On a single-page render this is exactly "
        "the text drawn on the image; on a paginated render each page carries its own "
        "page-scoped text instead (those are stamped into the persisted run's "
        "params['page_sample_size_notes'], since a run-wide string matches no page).",
    )
    batched: bool = Field(
        description="True once the selection exceeds TRAIT_BATCH_THRESHOLD traits, in which "
        "case the render is paginated (see n_pages)."
    )
    n_pages: int = Field(
        description="Number of committed output pages (1 when not batched)."
    )
    resolved_trait_columns: list[str] = Field(
        description="The exact trait columns used to render/persist this run, in selection "
        "order — recorded even when trait_columns was omitted (auto-detected).",
    )
    page_traits: dict[str, list[str]] = Field(
        description="Maps each committed FIGURE filename to the trait columns rendered on "
        "that page (a single entry, covering every resolved_trait_columns, when not batched) "
        "— otherwise only discoverable by opening the image and reading its axis labels. "
        "This is NOT a mapping over every key of `outputs`: a run also commits "
        "group_sample_sizes.csv, which is a table rather than a page and deliberately has no "
        "entry here, so iterating `outputs` and indexing this field raises KeyError. Iterate "
        "this field directly when you want the pages.",
    )


@as_mcp_tool(
    input_model=PlotTraitBoxplotsParams,
    output_model=PlotTraitBoxplotsResult,
    errors=(ExperimentReadError, CommitFailedError, ManifestReadError),
)
def plot_trait_boxplots(
    params: PlotTraitBoxplotsParams, *, provenance: Provenance
) -> PlotTraitBoxplotsResult:
    """Render boxplots-by-genotype for ``experiment``'s **raw, uncleaned** traits and persist
    them. No QC cleaning has been applied — this is a pre-clean EDA view, the same category as
    ``qc_inspect``."""
    reader = _ports.reader()
    store = _ports.store()

    _validate_experiment_name(params.experiment)

    frame = reader.load_experiment(params.experiment, version="raw")

    if frame.genotype_col is None:
        raise BloomMCPError(
            code="assumption_violated",
            message=f"No genotype column detected in {params.experiment!r}. Cannot group by "
            f"genotype.",
            remedy="Ensure the experiment has a detectable genotype column, or use a "
            "different experiment.",
        )

    trait_cols = resolve_trait_columns(frame, params.trait_columns, params.experiment)
    batched = len(trait_cols) > TRAIT_BATCH_THRESHOLD

    # ── #748: everything below is computed HERE, before create_run ──────────────────
    # Provenance is stamped at run creation and commit cannot amend it, so anything
    # computed during the render loop is too late to reach the manifest. That includes the
    # per-page note texts, which is why page composition is derived from the same slicing
    # formula page_traits already uses rather than from the returned figures.
    sample_sizes = group_sample_size_table(frame.df, trait_cols, frame.genotype_col)
    # Genotypes reach the result, the CSV and the tick-label match as strings, so coerce
    # once here rather than at three call sites with three chances to disagree.
    if not sample_sizes.empty:
        sample_sizes = sample_sizes.assign(
            genotype=sample_sizes["genotype"].astype(str)
        )
    n_rows_read = len(frame.df)
    rows_missing_genotype = int(frame.df[frame.genotype_col].isna().sum())
    n_genotype_groups = (
        int(sample_sizes["genotype"].nunique()) if not sample_sizes.empty else 0
    )

    # Buckets, in precedence order, every one keyed on the FINITE count (design.md
    # Decision 5). A trait dead everywhere collapses to no_data_traits and emits no
    # per-group rows, so one dead trait cannot exhaust the absent bucket's cap.
    if sample_sizes.empty:
        trait_totals = None
        no_data_traits: list[str] = []
        absent = non_finite = small = sample_sizes
        summarized = sample_sizes
    else:
        trait_totals = sample_sizes.groupby("trait")["n_plotted"].sum()
        no_data_traits = sorted(str(t) for t in trait_totals[trait_totals == 0].index)
        live = sample_sizes[~sample_sizes["trait"].isin(no_data_traits)]
        absent = live[live["n_plotted"] == 0].sort_values(
            ["trait", "genotype"], kind="stable"
        )
        # Worst-first, like every other capped bucket: sorting by name would let a cell with
        # one inf survive the cap while one with 900 is truncated away.
        non_finite = live[live["n_non_finite"] > 0].sort_values(
            ["n_non_finite", "trait", "genotype"],
            ascending=[False, True, True],
            kind="stable",
        )
        small = live[
            (live["n_plotted"] > 0)
            & (live["n_non_finite"] == 0)
            & (live["n_finite"] < MIN_PLOTTED_SAMPLES)
        ].sort_values(["n_finite", "trait", "genotype"], kind="stable")
        summarized = sample_sizes[sample_sizes["n_finite"] > 0]

    def _entries(table):
        return [
            GroupSampleSize(
                trait=str(row["trait"]),
                genotype=str(row["genotype"]),
                n=int(row["n_finite"]),
                n_non_finite=int(row["n_non_finite"]),
            )
            for row in table.head(MAX_FLAGGED_REPORTED).to_dict("records")
        ]

    non_finite_traits = (
        sorted({str(t) for t in non_finite["trait"]}) if len(non_finite) else []
    )
    # Computed over cells that actually have finite data, the same exclusion Decision 7
    # applies to the scalars. An absent cell sits at nan_fraction 1.0 by construction, so
    # including them hands this field to a cell already fully reported in
    # absent_genotype_groups -- masking exactly the case the field exists for: a box that
    # CLEARS the count floor while most of its column is missing.
    with_data = summarized
    if len(with_data):
        worst = with_data.sort_values(
            ["nan_fraction", "trait", "genotype"],
            ascending=[False, True, True],
            kind="stable",
        ).iloc[0]
        max_nan_fraction = round(float(worst["nan_fraction"]), 4)
        max_nan_fraction_group = [str(worst["trait"]), str(worst["genotype"])]
    else:
        max_nan_fraction = None
        max_nan_fraction_group = None

    # Optional throughout, None on an empty population: an all-null genotype column is
    # reachable today and RENDERS SUCCESSFULLY, so min()/median() over the resulting empty
    # groupby must not raise or emit NaN (design.md Decision 8).
    disclosure = {
        "n_rows_read": n_rows_read,
        "n_genotype_groups": n_genotype_groups,
        "rows_missing_genotype": rows_missing_genotype,
        "n_boxes_drawn": (
            int((sample_sizes["n_plotted"] > 0).sum()) if len(sample_sizes) else 0
        ),
        "n_boxes_summarized": int(len(summarized)),
        "box_n_min": native(summarized["n_finite"].min()) if len(summarized) else None,
        "box_n_median": (
            native(summarized["n_finite"].median()) if len(summarized) else None
        ),
        "box_n_max": native(summarized["n_finite"].max()) if len(summarized) else None,
        "no_data_trait_count": len(no_data_traits),
        "non_finite_trait_count": len(non_finite_traits),
        # Deliberately NOT bucket-derived: small_sample_groups excludes inf-carrying cells to
        # keep the buckets mutually exclusive, so a cell with 4 finite values and 2 infs is
        # reported only as non-finite and a caller gating on small_sample_group_count == 0
        # would conclude "no thin boxes" while box_n_min reads 4. This counts every drawn box
        # below the floor whatever else is wrong with it.
        "thin_box_count": (
            int(
                (
                    (sample_sizes["n_plotted"] > 0)
                    & (sample_sizes["n_finite"] < MIN_PLOTTED_SAMPLES)
                ).sum()
            )
            if len(sample_sizes)
            else 0
        ),
        "absent_genotype_group_count": int(len(absent)),
        "non_finite_group_count": int(len(non_finite)),
        "small_sample_group_count": int(len(small)),
        "max_nan_fraction": max_nan_fraction,
        "max_nan_fraction_group": max_nan_fraction_group,
    }
    buckets = {
        "no_data_traits": no_data_traits[:MAX_FLAGGED_REPORTED],
        "absent_genotype_groups": _entries(absent),
        "non_finite_groups": _entries(non_finite),
        "small_sample_groups": _entries(small),
        "non_finite_traits": non_finite_traits[:MAX_FLAGGED_REPORTED],
    }

    def _page_slice(index):
        start = index * _DELEGATE_BATCH_SIZE
        return (
            trait_cols[start : start + _DELEGATE_BATCH_SIZE]
            if batched
            else list(trait_cols)
        )

    def _note_for(page_cols, scoped):
        page_table = (
            sample_sizes[sample_sizes["trait"].isin(page_cols)]
            if len(sample_sizes)
            else sample_sizes
        )
        return _sample_size_note(
            page_table,
            [t for t in no_data_traits if t in page_cols],
            len(page_cols),
            len(trait_cols),
            n_genotype_groups,
            rows_missing_genotype,
            scoped,
        )

    sample_size_note = _note_for(trait_cols, scoped=False)
    n_expected_pages = -(-len(trait_cols) // _DELEGATE_BATCH_SIZE) if batched else 1
    page_notes = {
        (
            f"{_PNG_STEM}.png" if not batched else f"{_PNG_STEM}_page{i + 1}.png"
        ): _note_for(_page_slice(i), scoped=batched)
        for i in range(n_expected_pages)
    }
    finite_counts = (
        {
            (str(r["trait"]), str(r["genotype"])): int(r["n_finite"])
            for r in sample_sizes.to_dict("records")
        }
        if len(sample_sizes)
        else {}
    )
    # Per trait, the genotypes the delegate actually draws a box for -- it drops empty groups
    # before taking its tick labels, so this is the exact label set the annotator matches
    # against (set EQUALITY, not a subset test -- see _annotate_genotype_ticks).
    plotted_genotypes: dict[str, set[str]] = {}
    if len(sample_sizes):
        for row in sample_sizes[sample_sizes["n_plotted"] > 0].to_dict("records"):
            plotted_genotypes.setdefault(str(row["trait"]), set()).add(
                str(row["genotype"])
            )

    prov = provenance.model_copy(
        update={
            "based_on_version": frame.source,
            "params": {
                **provenance.params,
                "resolved_trait_columns": trait_cols,
                # genotype_column is auto-detected and data-dependent: without it in the
                # manifest, every genotype label in this disclosure is unreproducible from
                # a stored run — the same argument that put resolved_trait_columns here.
                "genotype_column": frame.genotype_col,
                # native(...) throughout: np.int64 raises PydanticSerializationError at the
                # manifest write (design.md Decision 9).
                **disclosure,
                **{
                    key: [
                        e.model_dump() if hasattr(e, "model_dump") else e for e in value
                    ]
                    for key, value in buckets.items()
                },
                "sample_size_note": sample_size_note,
                # The PER-PAGE notes are deliberately NOT stamped, reversing an earlier
                # decision in this change on measured grounds: at cylinder width that is 53
                # pages x ~2.5 KB of text appended to `params` on every version, in a
                # manifest.json that create_run re-validates in full on every subsequent run
                # for this tool+experiment -- a cost that compounds with run count. Each page's
                # note is a pure function of page_traits[page], the committed CSV,
                # MIN_PLOTTED_SAMPLES and MAX_NOTE_NAMES, all of which the manifest or its
                # outputs already carry, so stamping the rendered strings duplicates recoverable
                # data rather than preserving anything.
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
    figures: list = []
    try:
        # call_with_figure_cleanup (#721, landed via PR #726) holds FIGURE_REGISTRY_LOCK for
        # the delegate call — allocating figures mutates the shared global matplotlib
        # registry, which a concurrent figure-creating call elsewhere in the process could
        # otherwise interleave with (see the lock's own comment in bloom_mcp.tools._plots)
        # — and, if the delegate raises after allocating, closes what it allocated before
        # re-raising, still under the lock. That second half is what closes #725 for
        # this tool: a *_batched delegate failing on page N has already registered pages
        # 1..N-1, which `figures` (still []) could never reach in `finally` below. The
        # list() stays inside the callable so the batched generator is consumed under the
        # lock, not lazily afterwards.
        def _render() -> list:
            if batched:
                return list(
                    create_trait_boxplots_by_genotype_batched(
                        frame.df, trait_cols, genotype_col=frame.genotype_col
                    )
                )
            return [
                create_trait_boxplots_by_genotype(
                    frame.df, trait_cols, genotype_col=frame.genotype_col
                )
            ]

        figures = call_with_figure_cleanup(_render)
        if len(figures) != n_expected_pages:
            # The per-page notes and page_traits are both derived from the same slicing
            # formula BEFORE rendering (they have to be: params are stamped at create_run).
            # If the delegate's page count ever drifts from _DELEGATE_BATCH_SIZE, fail here
            # rather than draw a note whose statistics describe a different set of traits.
            raise BloomMCPError(
                code="internal_error",
                message=f"Renderer returned {len(figures)} page(s) for "
                f"{len(trait_cols)} trait(s); expected {n_expected_pages}.",
                remedy="The rendering delegate's batch size appears to have changed. "
                "Report this as a bug.",
            )

        outputs: dict[str, str] = {}
        page_traits: dict[str, list[str]] = {}
        annotated = bool(figures)
        for i, fig in enumerate(figures, start=1):
            name = f"{_PNG_STEM}.png" if not batched else f"{_PNG_STEM}_page{i}.png"
            page_cols = _page_slice(i - 1)
            # #748, in order: per-box counts onto the tick labels, then lay the figure out,
            # then the note underneath. tight_layout is called ONLY when unbatched -- the
            # batched delegate already calls it itself (visualization.py), and paying for it
            # again on each of cylinder's 53 pages costs +21% per page (measured), which is
            # what would push the cylinder smoke run past its client timeout. On the
            # unbatched path the delegate deliberately leaves it to the caller, and skipping
            # it both collides the lengthened rotated tick labels with the next row's titles
            # and leaves ~2.7in of dead margin the note would then sit below.
            annotated = (
                _annotate_genotype_ticks(
                    fig, finite_counts, plotted_genotypes, no_data_traits
                )
                and annotated
            )
            if not batched:
                # rect reserves the bottom strip for the note, mirroring how the batched
                # delegate reserves its own top strip for a suptitle.
                fig.tight_layout(rect=[0, 0.03, 1, 1])
            # Indexed, not .get(...)-with-a-fallback: falling back to the run-wide note would
            # draw statistics over all 846 traits onto a 16-trait page without the
            # "(this page)" qualifier -- exactly the misreading the page scoping exists to
            # prevent. The page count is asserted above, so a missing key is a real bug.
            note = page_notes[name]
            _draw_sample_size_note(fig, note, flagged="⚠" in note)
            fig.savefig(run.staging_dir / name, dpi=150, bbox_inches="tight")
            outputs[name] = name
            page_traits[name] = page_cols
        # Committed once per RUN, not once per page, and deliberately absent from
        # page_traits/n_pages: it is a table, not a rendered page (#748, and the MODIFIED
        # "Paginated Figure Persistence" requirement). This is why len(outputs) is now the
        # page count plus one.
        sample_sizes.to_csv(run.staging_dir / _SAMPLE_SIZES_CSV, index=False)
        outputs[_SAMPLE_SIZES_CSV] = _SAMPLE_SIZES_CSV
        stored = store.commit(run, outputs)
    except Exception:
        rmtree(run.staging_dir, ignore_errors=True)
        raise
    finally:
        # Held for the SAME reason as the creation call above, and this is not
        # belt-and-braces: plt.close -> Gcf.destroy_fig scans `Gcf.figs.values()` to
        # find the manager owning each figure, and that scan is unsynchronized. A
        # concurrent locked create (Gcf.set_active -> `figs[num] = manager` +
        # move_to_end) mutating the dict mid-scan raises RuntimeError("OrderedDict
        # mutated during iteration"). Creation-only locking therefore does NOT close
        # the race it claims to (#466 review round 7). Skipped entirely when creation
        # failed before allocating anything, so the error path adds no lock traffic.
        if figures:
            with FIGURE_REGISTRY_LOCK:
                for fig in figures:
                    plt.close(fig)

    return PlotTraitBoxplotsResult(
        experiment=params.experiment,
        source=frame.source,
        genotype_column=frame.genotype_col,
        n_traits_plotted=len(trait_cols),
        batched=batched,
        n_pages=len(figures),
        resolved_trait_columns=trait_cols,
        page_traits=page_traits,
        **disclosure,
        **buckets,
        sample_size_note=sample_size_note,
        box_labels_annotated=annotated,
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
        output_links=stored.output_links,
    )
