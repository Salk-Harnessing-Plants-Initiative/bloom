"""plot_trait_histograms — histogram plots for trait distributions (#466).

Converged onto the ``@as_mcp_tool`` contract every other tool in this folder uses (Pydantic
I/O, structured ``BloomMCPError``, one stamped ``Provenance``, versioned ``ResultStore``
persistence) — the same read-only, pre-clean EDA pattern as ``qc_inspect``: reads the raw
frame via the :class:`ExperimentReader` port (no ``require_clean``).

Delegates rendering to ``sleap_roots_analyze.visualization.create_trait_histograms`` (or its
``_batched`` counterpart above ``_viz_shared.TRAIT_BATCH_THRESHOLD`` traits); this file owns no
plotting logic of its own. A batched render persists one committed output per page — mirroring
``pca_analysis``'s ``include_plots`` multi-figure handling — rather than a single figure.

Persists a versioned run under its own tool class ``trait_histograms`` (not the shared,
unclaimed legacy ``viz`` slot — see
``openspec/changes/converge-bloommcp-viz-tools/design.md`` for why).

**Two disclosure gaps closed in #466's review** (mirrors ``plot_trait_boxplots``):
``resolved_trait_columns`` records the exact trait columns used — including when
``trait_columns`` was omitted and auto-detection resolved them — both in the result and stamped
into the persisted run's ``params``, so a later reader of the manifest doesn't have to
re-run (data-dependent) auto-detection against data that may have drifted; and ``page_traits``
names which traits landed on which page of a batched (paginated) render, previously only
discoverable by opening an image and reading its axis labels.

**How much data is behind each panel is now reported (#748).** The delegate drops null values
silently (``dropna()``), so a trait that is 90% missing bins like any other. ``low_sample_traits``
names every trait below ``_viz_shared.MIN_PLOTTED_SAMPLES`` with its binned count and missing
fraction (capped, ascending, with an uncapped ``low_sample_trait_count``);
``trait_n_min``/``_median``/``_max`` cover **every** resolved trait uncapped; ``max_nan_fraction``
names the worst-affected trait even when its count clears the floor; and the committed
``trait_sample_sizes.csv`` carries the complete per-trait table. A trait with
``n_plotted == 0`` — the delegate's literal ``"No data"`` panel (confirmed against the live
delegate, not assumed) — is named in the result rather than being discoverable only by opening
the image.

Counts come from ``pandas`` ``count()``, which **includes** ``+/-inf`` (the trap ``qc_inspect``
documents for its own missingness fields), so ``n_finite`` is reported beside ``n_plotted``
rather than left to be derived — see the non-finite guard below.

**A non-finite value fails the run early, with the trait named.** ``matplotlib.hist`` cannot bin
a non-finite range — it raises ``ValueError: supplied range of [x, inf] is not finite`` — so such
a run has always failed; it just failed with the delegate's own error, redacted into a message
naming no trait and offering no remedy. The selection is now checked **before**
``store.create_run``, raising ``assumption_violated`` that names the offending traits and points
at ``qc_clean``/``remove_outliers``, so no staging directory is created and immediately torn
down. The values are NOT stripped before rendering: this is a pre-clean EDA view, and silently
altering data the caller asked to see raw would diverge the image from what ``qc_inspect``
reports for the same frame.

**The image needs no per-panel labelling, but it does carry a note.**
``create_trait_histograms`` already titles every panel ``f"{trait}\n(n={count})"``, so the
per-box half of what #748 gave ``plot_trait_boxplots`` has no counterpart here;
``test_delegate_titles_each_panel_with_its_n`` pins that against the live delegate, because the
decision rests on it and the older ``_titled_traits`` helper splits the suffix off before
asserting.

But ``(n=…)`` is what was **binned**, not what was **dropped** — a panel reading ``(n=12)`` is
identical whether twelve plants were measured or 108 of 120 rows were lost, and "no 'N rows
excluded' count, no per-trait missingness disclosure" is the sentence #748 opens with. So
``missingness_note`` is drawn below the axes on every render (PR review round 2, which correctly
found that the earlier "the image is deliberately unchanged" argument answered the sample-size
question and left the missingness one open). It reports the binned counts against the rows read,
flags panels below ``MIN_PLOTTED_SAMPLES`` **and** panels built from under half their rows —
flagging on count alone cannot tell those two apart — and carries an unconditional caveat, since
the delegate bins into a fixed 30 regardless of ``n``.

**Two delegate properties assessed for #748 and deliberately not "fixed":** the delegate hardcodes
``bins=30``, so a panel well above the sample-size floor can still be visually degenerate (n=6
draws six bars of height 1), and a single extreme value spreads those 30 bins across the whole
range — collapsing every other observation into one bar while the ``(n=…)`` title still reads
normally. Neither is a *hidden* exclusion (``hist`` is passed no ``range=``, so nothing is
clipped), which is why no result field claims to disclose them, but both are real ways a
legitimate-looking panel can mislead.

**Still not disclosed:** a zero-variance (constant) trait renders as a degenerate single-bar
histogram with no flag. That is a legitimate, if uninformative, plot — unlike
``plot_correlation_matrix``, where a constant trait poisons every correlation cell it
participates in, which is what that tool's guard exists to catch.
"""

from __future__ import annotations

from shutil import rmtree
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pydantic import BaseModel, ConfigDict, Field
from sleap_roots_analyze.visualization import (
    create_trait_histograms,
    create_trait_histograms_batched,
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
    draw_disclosure_note,
    flagged_names,
    native,
    resolve_trait_columns,
    trait_sample_size_table,
)

_TOOL_CLASS = "trait_histograms"
_PNG_STEM = "trait_histograms"
# The complete per-trait sample-size table, committed as its own output (#748). A download,
# not a response field: at cylinder width (846 traits) an inline per-trait dict is tens of
# kilobytes, against the family's "links, not blobs" contract. Mirrors qc_inspect's
# nan_samples.csv and descriptive_stats' stats.csv.
_SAMPLE_SIZES_CSV = "trait_sample_sizes.csv"
# create_trait_histograms_batched's own internal page size — independent of
# TRAIT_BATCH_THRESHOLD (which only decides WHETHER to batch). Not overridden by this
# tool's call, so it is safe to use for computing which trait landed on which page;
# test_plot_trait_histograms_tool.py pins this against the live delegate signature so a
# future sleap-roots-analyze bump that changes it is caught, not silently desynced.
_DELEGATE_BATCH_SIZE = 16


def _missingness_note(page_table, n_page_traits, n_total_traits, n_rows_read, scoped):
    """Build the note drawn below a histogram page (#748, PR review round 2).

    The delegate already titles every panel ``f"{trait}\n(n={count})"``, which is why this tool
    needs no per-box labelling — but that number is what was *binned*, not what was *dropped*.
    A reader seeing ``(n=12)`` cannot tell a trait measured on 12 plants from one that lost 108
    of its 120 measurements, and "no 'N rows excluded' count, no per-trait missingness
    disclosure" is the gap #748 opens with. So the same unconditional note the boxplots carry
    is drawn here, in the unit this tool plots: panels, not boxes.

    Same rules as its sibling: always drawn (its absence must not read as a sufficiency
    claim), every denominator named, page-scoped renders say so in their own text, and an
    unconditional tail about what clearing the floor does not establish.
    """
    scope = " (this page)" if scoped else ""
    binned = page_table["n_plotted"]
    head = (
        f"n rows per panel{scope}: min={int(binned.min())}, "
        f"median={float(binned.median()):g}, max={int(binned.max())} across "
        f"{len(page_table)} of {n_total_traits} trait(s), from {n_rows_read} row(s) read."
    )

    thin = page_table[page_table["n_plotted"] < MIN_PLOTTED_SAMPLES].sort_values(
        ["n_plotted", "trait"], kind="stable"
    )
    # Flagged on FRACTION as well as count: a trait with 200 of 20,000 rows clears any count
    # floor while having silently dropped 99% of its measurements -- which is the case the
    # panel title's own (n=...) cannot distinguish from a small experiment.
    gappy = page_table[
        (page_table["nan_fraction"] >= 0.5)
        & (page_table["n_plotted"] >= MIN_PLOTTED_SAMPLES)
    ].sort_values(["nan_fraction", "trait"], ascending=[False, True], kind="stable")

    clauses = []
    if not thin.empty:
        clauses.append(
            f"{len(thin)} of {len(page_table)} panel(s) below n={MIN_PLOTTED_SAMPLES}: "
            + flagged_names(
                thin.to_dict("records"),
                lambda r: f"{r['trait']} (n={int(r['n_plotted'])})",
            )
        )
    if not gappy.empty:
        clauses.append(
            f"{len(gappy)} panel(s) built from under half their rows: "
            + flagged_names(
                gappy.to_dict("records"),
                lambda r: f"{r['trait']} ({r['nan_fraction']:.0%} missing)",
            )
        )

    tail = (
        f" A panel at or above n={MIN_PLOTTED_SAMPLES} is not thereby reliable: the delegate "
        "bins into a fixed 30 bins regardless of n, so a single extreme value can collapse "
        "every other observation into one bar."
    )
    if not clauses:
        return head + tail
    return (
        f"{head} ⚠ "
        + "; ".join(clauses)
        + f". See {_SAMPLE_SIZES_CSV} for every trait."
        + tail
    )


class PlotTraitHistogramsParams(BaseModel):
    """Inputs for ``plot_trait_histograms``. No ``seed`` — rendering is deterministic."""

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


class TraitSampleSize(BaseModel):
    """One trait's plotted-sample disclosure (#748)."""

    trait: str
    n_plotted: int = Field(
        description="Observations actually binned — non-null values, which INCLUDES +/-inf "
        "(pandas' isna convention, the same caveat qc_inspect documents). Zero means the "
        "delegate rendered a literal 'No data' panel for this trait."
    )
    n_missing: int = Field(description="Rows where this trait was null.")
    nan_fraction: float = Field(
        description="n_missing / rows read, rounded to 4 places; 0.0 for a zero-row frame."
    )


class PlotTraitHistogramsResult(RunLinks):
    """A small summary + links to the persisted histogram run."""

    experiment: str
    source: str
    n_traits_plotted: int
    n_rows_read: int = Field(
        description="Rows in the raw frame — the denominator behind every count below. Note "
        "this is NOT plot_trait_boxplots' denominator for the same experiment: a histogram "
        "bins every row, while a box excludes rows whose genotype value is null. The two "
        "tools' counts differ by exactly that tool's rows_missing_genotype, which is the "
        "reconciliation — not data loss."
    )
    trait_n_min: Optional[int] = Field(
        default=None,
        description="Smallest binned count across EVERY resolved trait (uncapped, not just "
        "the reported sample). Includes traits with none: a panel is drawn for every selected "
        "trait, so a zero describes something the reader can see — unlike "
        "plot_trait_boxplots, whose absent cells are drawn as nothing at all and are excluded "
        "from its summaries.",
    )
    trait_n_median: Optional[float] = Field(
        default=None,
        description="Median binned count across every resolved trait (uncapped). Reported "
        "because a capped list is a biased sample: 20 thin traits drawn from 846 read as if "
        "the whole selection were thin.",
    )
    trait_n_max: Optional[int] = Field(
        default=None, description="Largest binned count across every resolved trait."
    )
    low_sample_traits: list[TraitSampleSize] = Field(
        default_factory=list,
        description="Traits binning fewer than _viz_shared.MIN_PLOTTED_SAMPLES finite "
        "observations, ordered ASCENDING by count then trait name — the ascending order is "
        f"what makes the cap safe, since it truncates the best-supported end — capped at "
        f"{MAX_FLAGGED_REPORTED}. The tie-break is explicit because ties are the normal case, "
        "and without one the persisted list would not be reproducible between runs over the "
        "same data. See low_sample_trait_count for the true total and the committed "
        f"{_SAMPLE_SIZES_CSV} for every trait. The floor is a DEGENERACY bound, not a "
        "sufficiency threshold: a trait just above it is not thereby trustworthy.",
    )
    low_sample_trait_count: int = Field(
        default=0,
        description="Number of traits below the floor, UNCAPPED — the true size of the list "
        "above before truncation.",
    )
    max_nan_fraction: Optional[float] = Field(
        default=None,
        description="Largest missing fraction across every resolved trait. Reported because "
        "the flagged list keys on absolute count: a trait with 200 non-null rows out of "
        "20,000 clears the floor and would otherwise be named nowhere in the response.",
    )
    max_nan_fraction_trait: Optional[str] = Field(
        default=None,
        description="The trait carrying max_nan_fraction (first by name on a tie).",
    )
    missingness_note: str = Field(
        default="",
        description="The run-wide note drawn below the axes of every rendered page. The "
        "delegate's own per-panel (n=...) says what was BINNED; this says what was dropped, "
        "how the panels compare, and what the floor does not establish. On a single-page "
        "render it is exactly the text drawn; on a paginated render each page carries its own "
        "page-scoped text, reconstructible from page_traits and the committed table.",
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
        "trait_sample_sizes.csv, which is a table rather than a page and deliberately has no "
        "entry here, so iterating `outputs` and indexing this field raises KeyError. Iterate "
        "this field directly when you want the pages.",
    )


@as_mcp_tool(
    input_model=PlotTraitHistogramsParams,
    output_model=PlotTraitHistogramsResult,
    errors=(ExperimentReadError, CommitFailedError, ManifestReadError),
)
def plot_trait_histograms(
    params: PlotTraitHistogramsParams, *, provenance: Provenance
) -> PlotTraitHistogramsResult:
    """Render histograms for ``experiment``'s **raw, uncleaned** trait distributions and
    persist them. No QC cleaning has been applied — this is a pre-clean EDA view, the same
    category as ``qc_inspect``."""
    reader = _ports.reader()
    store = _ports.store()

    _validate_experiment_name(params.experiment)

    frame = reader.load_experiment(params.experiment, version="raw")
    trait_cols = resolve_trait_columns(frame, params.trait_columns, params.experiment)
    batched = len(trait_cols) > TRAIT_BATCH_THRESHOLD

    # Every summary is computed HERE, before create_run: Provenance is stamped at run
    # creation and commit cannot amend it, so anything computed during the render loop is
    # too late to reach the manifest (#748).
    sample_sizes = trait_sample_size_table(frame.df, trait_cols)
    n_rows_read = len(frame.df)

    # Non-finite pre-flight (#748). matplotlib.hist cannot bin a non-finite range -- it
    # raises "supplied range of [x, inf] is not finite" -- so this run has always failed.
    # It just failed with the delegate's own error, redacted into a message naming no trait
    # and offering no remedy. Checked before create_run so no staging directory is written
    # and immediately torn down, and before the delegate call so the failure is ours.
    non_finite = sample_sizes.loc[sample_sizes["n_non_finite"] > 0, "trait"].tolist()
    if non_finite:
        shown = non_finite[:MAX_FLAGGED_REPORTED]
        remainder = len(non_finite) - len(shown)
        named = ", ".join(shown) + (f", +{remainder} more" if remainder else "")
        raise BloomMCPError(
            code="assumption_violated",
            message=f"{params.experiment!r} carries non-finite (+/-inf) values in "
            f"{len(non_finite)} selected trait(s): {named}. A histogram cannot be binned "
            f"over a non-finite range.",
            remedy="Clean the experiment first (qc_clean, or remove_outliers for extreme "
            "values), or pass trait_columns naming only finite traits.",
        )

    # Ascending by count, then trait name. The tie-break is not decoration: ties are the
    # normal case, and without one which entries survive the cap -- and therefore the
    # persisted manifest -- would not be reproducible between runs over the same data.
    flagged = sample_sizes[sample_sizes["n_finite"] < MIN_PLOTTED_SAMPLES].sort_values(
        ["n_finite", "trait"], kind="stable"
    )
    low_sample_traits = [
        TraitSampleSize(
            trait=str(row["trait"]),
            n_plotted=int(row["n_plotted"]),
            n_missing=int(row["n_missing"]),
            nan_fraction=round(float(row["nan_fraction"]), 4),
        )
        for row in flagged.head(MAX_FLAGGED_REPORTED).to_dict("records")
    ]
    # Over EVERY resolved trait, including those binning nothing: the delegate draws a panel
    # per selected trait (an empty one carries a literal "No data" label), so a zero here
    # describes something the reader can see. plot_trait_boxplots deliberately differs --
    # an absent cell there is drawn as nothing at all.
    counts = sample_sizes["n_plotted"]
    worst_nan = sample_sizes.sort_values(
        ["nan_fraction", "trait"], ascending=[False, True], kind="stable"
    ).iloc[0]

    def _page_slice(index):
        start = index * _DELEGATE_BATCH_SIZE
        return (
            trait_cols[start : start + _DELEGATE_BATCH_SIZE]
            if batched
            else list(trait_cols)
        )

    def _note_for(page_cols, scoped):
        page_table = sample_sizes[sample_sizes["trait"].isin(page_cols)]
        return _missingness_note(
            page_table, len(page_cols), len(trait_cols), n_rows_read, scoped
        )

    n_expected_pages = -(-len(trait_cols) // _DELEGATE_BATCH_SIZE) if batched else 1
    missingness_note = _note_for(trait_cols, scoped=False)
    page_notes = {
        (
            f"{_PNG_STEM}.png" if not batched else f"{_PNG_STEM}_page{i + 1}.png"
        ): _note_for(_page_slice(i), scoped=batched)
        for i in range(n_expected_pages)
    }

    disclosure = {
        "n_rows_read": n_rows_read,
        "trait_n_min": native(counts.min()),
        "trait_n_median": native(counts.median()),
        "trait_n_max": native(counts.max()),
        "low_sample_trait_count": int(len(flagged)),
        "max_nan_fraction": round(float(worst_nan["nan_fraction"]), 4),
        "max_nan_fraction_trait": str(worst_nan["trait"]),
    }

    prov = provenance.model_copy(
        update={
            "based_on_version": frame.source,
            "params": {
                **provenance.params,
                "resolved_trait_columns": trait_cols,
                # native(...) throughout: these are the first numeric aggregates this tool
                # stamps, and np.int64 raises PydanticSerializationError at the manifest
                # write (design.md Decision 9).
                **disclosure,
                "low_sample_traits": [t.model_dump() for t in low_sample_traits],
                "missingness_note": missingness_note,
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
                return list(create_trait_histograms_batched(frame.df, trait_cols))
            return [create_trait_histograms(frame.df, trait_cols)]

        figures = call_with_figure_cleanup(_render)

        if len(figures) != n_expected_pages:
            raise BloomMCPError(
                code="internal_error",
                message=f"Renderer returned {len(figures)} page(s) for "
                f"{len(trait_cols)} trait(s); expected {n_expected_pages}.",
                remedy="The rendering delegate's batch size appears to have changed. "
                "Report this as a bug.",
            )

        outputs: dict[str, str] = {}
        page_traits: dict[str, list[str]] = {}
        for i, fig in enumerate(figures, start=1):
            name = f"{_PNG_STEM}.png" if not batched else f"{_PNG_STEM}_page{i}.png"
            # The delegate already calls plt.tight_layout(), so the note only needs its own
            # strip reserved -- no layout pass here (#748 review round 2).
            note = page_notes[name]
            draw_disclosure_note(fig, note, flagged="⚠" in note)
            fig.savefig(run.staging_dir / name, dpi=150, bbox_inches="tight")
            outputs[name] = name
            start = (i - 1) * _DELEGATE_BATCH_SIZE
            page_traits[name] = (
                trait_cols[start : start + _DELEGATE_BATCH_SIZE]
                if batched
                else list(trait_cols)
            )
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

    return PlotTraitHistogramsResult(
        experiment=params.experiment,
        source=frame.source,
        n_traits_plotted=len(trait_cols),
        batched=batched,
        n_pages=len(figures),
        resolved_trait_columns=trait_cols,
        page_traits=page_traits,
        low_sample_traits=low_sample_traits,
        missingness_note=missingness_note,
        **disclosure,
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
        output_links=stored.output_links,
    )
