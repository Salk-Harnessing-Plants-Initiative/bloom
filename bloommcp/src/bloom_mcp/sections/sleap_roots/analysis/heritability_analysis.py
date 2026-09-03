"""heritability_analysis — per-trait broad-sense heritability (H²) as *data*.

The 9th granular **consumer** (#462). It reads a *cleaned* experiment through the
:class:`ExperimentReader` port with ``require_clean=True`` and delegates **all**
heritability estimation to
``sleap_roots_analyze.statistics.calculate_heritability_estimates``, wrapping the result
into the upstream typed :class:`HeritabilityResult` via
``HeritabilityResult.from_heritability_dict``. The MCP owns no H² math — no mixed model,
no variance-component estimation, no ANOVA fallback of its own.

**Replaces two retired tools.** ``plot_heritability_bar`` and
``plot_variance_decomposition`` were the only heritability surface bloommcp had. Each
called the same delegate internally and then discarded almost everything it returned —
a PNG link plus one aggregate count — so no tool ever returned the per-trait numbers, and
nothing persisted a versioned run. Both are retired into this tool's ``include_plots`` /
``plots`` parameters (see ``_HERITABILITY_CATALOG_KEYS``), exactly mirroring how
``pca_analysis`` (#426/#447), ``umap_analysis`` (#425) and ``clustering`` (#601) grew
optional plots.

**Consume, don't re-clean.** The delegate does ``df[[trait, genotype_col]].dropna()`` per
trait. On raw data — which is what the two retired tools passed it, neither declaring
``require_clean`` — that silently changes the analyzed sample count per trait with no
signal to the caller. Requiring a cleaned version, and restricting the selection to the
reader's certified set (``frame.trait_cols``), makes that ``dropna()`` a genuine no-op
over the sample set ``qc_clean`` certified. Same argument ``pca_analysis`` makes for its
own delegate.

**Genotype required; replicate optional.** Both retired tools rejected any experiment
lacking *either* column. Upstream documents ``replicate_col`` as optional and never uses
its values in the fitted model (``value ~ 1 + (1|genotype)``), so H² is identical whether
it is supplied or ``None`` (talmolab/sleap-roots-analyze#142; the repo settled the same
question in ``docs/data-access-roadmap.md``, closed 2026-06-10). This is not a marginal
loosening: ``SupabaseReader`` produces every frame with ``replicate_col=None``, so
requiring one would make every DB-backed experiment unanalyzable here.

**One delegate call feeds the numbers and the figures.** The single
``calculate_heritability_estimates`` return is the source for the inline rows, the
persisted ``heritability.csv`` / ``heritability_result.json``, *and* both plotters —
so a rendered figure cannot disagree with the numbers returned beside it. There is no
second call site to diverge. The caller's ``threshold`` is likewise forwarded to all
three consumers, including ``create_variance_decomposition_plot``, whose own default is
``0.3`` rather than ``0.5``.

*One documented exception:* ``create_heritability_plot`` sorts by H² **descending**
before paginating, while the returned and persisted tables preserve the resolved trait
order. On a wide experiment the inline top-50 and the 50 bars on page 1 are therefore
different trait sets — same numbers, different slice. Stated here and in the tool
description rather than left for a caller to trip over.

**Nothing non-finite, and nothing zero-filled, reaches the wire.** Two distinct hazards,
one mechanism — see :func:`_scrub_delegate_result`.

**A trait with no variance to partition is named, not silently averaged in.** When a trait's
``var_genetic`` and ``var_residual`` are both exactly ``0``, there is no variance to
partition and its H2 is not a measurement — but the delegate still reports *a number* for
it, and which number depends on which branch produced it: its ``no_variance`` branch
hardcodes ``0.0``, while a mixed-model fit that returned exact zeros divides ``0/0``, gets
``nan``, and clamps it to ``1.0`` (``max(0, min(1, nan))`` is ``1`` in Python). A *perfect*
heritability and a *zero* heritability for the same underlying non-finding. Those traits are
listed in ``zero_variance_traits`` so a reader of ``mean_h2`` / ``n_above_threshold`` can see
that some contributing values are not estimates. They are still reported in ``per_trait`` and
the persisted table — the delegate's own verdict is not overridden here, only labeled.

Persists a versioned run under tool class ``heritability`` — reserved in
``manifest.CANONICAL_TOOL_CLASSES`` but never written to before this tool, since the
retired heritability surface was two *plot* tools that wrote loose PNGs to ``PLOTS_DIR``
and no manifest entry at all. So this starts a fresh version lineage rather than
extending a legacy one (contrast ``descriptive_stats``' reactivation of ``"stats"``).
"""

from __future__ import annotations

import math
from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field
from sleap_roots_analyze import HeritabilityResult
from sleap_roots_analyze.statistics import calculate_heritability_estimates

from bloom_mcp.contract import BloomMCPError, Provenance, RunLinks, as_mcp_tool
from bloom_mcp.data_access import (
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentReadError,
)
from bloom_mcp.result_store import CommitFailedError, ManifestReadError
from bloom_mcp.tools import _ports
from bloom_mcp.tools._consumer_utils import snapshot_frame
from bloom_mcp.tools._plots import close_figures, generate_figures, validate_plot_keys
from bloom_mcp.tools._qc_shared import _validate_trait_subset

_TOOL_CLASS = "heritability"
_TABLE_NAME = "heritability.csv"
_RESULT_NAME = "heritability_result.json"

# Matches ``descriptive_stats._SUMMARY_TRAIT_CAP`` deliberately — the two wide per-trait
# tools should present one idiom to an agent reading both. Cylinder's ~846 traits make
# this mandatory, not decorative (#483).
_SUMMARY_TRAIT_CAP = 50

_HERITABILITY_CATALOG_KEYS: frozenset[str] = frozenset(
    {
        "create_heritability_plot",
        "create_variance_decomposition_plot",
    }
)

# Every key the tool reads off a per-trait delegate entry. Absence of ANY of these routes
# the trait to `failed_traits` — see `_scrub_delegate_result` for why presence, not just
# finiteness, has to be checked.
_REQUIRED_TRAIT_KEYS = (
    "heritability",
    "var_genetic",
    "var_residual",
    "n_genotypes",
    "n_observations",
    "model_type",
)
_FINITE_TRAIT_KEYS = ("heritability", "var_genetic", "var_residual")


class HeritabilityAnalysisParams(BaseModel):
    """Inputs for ``heritability_analysis``. No ``seed`` — the delegate has no RNG."""

    experiment: str = Field(
        ...,
        description="Experiment (CSV filename) to analyze. Must have a cleaned version "
        "produced by qc_clean; heritability_analysis consumes it (require_clean). "
        "Resolves the most recent outlier trim when one exists for the experiment, not "
        "merely the most recent clean.",
    )
    version: Optional[str] = Field(
        default=None,
        description="Pin the analysis to a specific committed cleaned version "
        "(e.g. 'v2'; see list_existing_analyses). Omit to use the latest cleaned "
        "version.",
    )
    trait_columns: Optional[list[str]] = Field(
        default=None,
        description="Subset of cleaned trait columns to analyze; omit to use all "
        "certified-clean traits. Each must be a cleaned trait column of the experiment. "
        "Pass at least one column with no duplicates (an empty list is rejected).",
    )
    threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="H2 threshold classifying each trait's passed_threshold, and the "
        "reference line drawn on both plots. One value drives the numbers and every "
        "figure, so they cannot disagree — note this is passed explicitly to "
        "create_variance_decomposition_plot, whose own default is 0.3, not 0.5.",
    )
    include_plots: bool = Field(
        default=False,
        description="If true, generate and persist heritability plots as run artifacts, "
        "returned as additional entries in outputs. When false (default), no figure is "
        "generated and no plotting library is imported on this path.",
    )
    plots: Optional[list[str]] = Field(
        default=None,
        description="Subset of plot keys to generate; omit (None) to generate both "
        "available plots when include_plots=True. Ignored when include_plots=False. "
        "Valid keys: create_heritability_plot (the retired plot_heritability_bar's "
        "figure), create_variance_decomposition_plot (the retired "
        "plot_variance_decomposition's figure).",
    )
    user_label: Optional[str] = Field(
        default=None,
        description="Optional slug appended to the version directory name.",
    )


class TraitH2(BaseModel):
    """One trait's heritability estimate, as returned by the delegate."""

    trait: str
    h2: float
    passed_threshold: bool
    var_genetic: float
    var_residual: float
    n_genotypes: int
    n_observations: int
    model_type: str


class HeritabilityAnalysisResult(RunLinks):
    """Per-trait H2 values (bounded) + links to the persisted run."""

    experiment: str
    source: str
    n_samples: int
    genotype_col: str
    replicate_col: Optional[str]
    method: str
    threshold: float
    n_traits_requested: int
    n_traits_reported: int
    n_failed: int
    failed_traits: list[str] = Field(default_factory=list)
    nonfinite_traits: list[str] = Field(
        default_factory=list,
        description=(
            "Traits routed to failed_traits specifically because the delegate returned a "
            "non-finite heritability/var_genetic/var_residual for them, rather than "
            "reporting the trait as failed itself. A subset of failed_traits."
        ),
    )
    zero_variance_traits: list[str] = Field(
        default_factory=list,
        description=(
            "Scored traits whose var_genetic AND var_residual are both exactly 0 — no "
            "variance to partition, so their h2 is not a measurement whatever number the "
            "delegate attached to it (its no_variance branch reports 0.0; a mixed-model fit "
            "returning exact zeros divides 0/0 and clamps the NaN to 1.0). These traits ARE "
            "still reported in per_trait and the persisted table, and they DO contribute to "
            "mean_h2 and n_above_threshold — a non-empty list here means those aggregates "
            "include values that are not estimates. Unlike nonfinite_traits, NOT a subset of "
            "failed_traits: nothing failed, the trait simply carried no signal."
        ),
    )
    mean_h2: Optional[float] = Field(
        default=None,
        description=(
            "Mean H2 over scored traits, or null when no trait scored. Deliberately null "
            "rather than 0.0 in that case (which HeritabilityResult.mean_h2 returns) — "
            "'no data' must not read as 'heritability is zero'."
        ),
    )
    n_above_threshold: int
    per_trait: list[TraitH2] = Field(default_factory=list)
    truncated_in_summary: bool = False
    omitted_traits: list[str] = Field(default_factory=list)


def _scrub_delegate_result(
    raw: dict, trait_cols: list[str]
) -> tuple[dict, list[str], list[str]]:
    """Return a copy of ``raw`` with unusable per-trait entries replaced, + two name lists.

    Two distinct hazards, one mechanism — both routed by replacing the offending entry
    with an ``{"error": ...}`` dict in a **copy**, so the trait lands in
    ``HeritabilityResult.failed_traits`` through the delegate's own routing rather than
    through parallel bookkeeping here.

    1. **Missing keys are silently zero-filled by the upstream constructor.**
       ``HeritabilityResult.from_heritability_dict`` does
       ``float(entry.get("var_genetic", 0.0))`` (likewise ``var_residual``, and
       ``int(entry.get("n_genotypes", 0))``). A renamed or dropped upstream key would
       therefore be emitted as a plausible-looking *zero variance component* — and on the
       default ``include_plots=False`` path, the variance-component guard in
       ``_comparison_frame`` never runs to catch it. This is precisely what the
       ``bloommcp-packaging`` spec forbids, so key **presence** is checked on every path,
       not only when a figure is requested.

    2. **Non-finite floats abort the whole run.** ``HeritabilityResult.to_json()``
       defaults to ``allow_nan=False`` and *raises* on any non-finite float. Catching that
       after the fact would fail a run whose other 800 traits were fine.

    Only case 2 is reported in ``nonfinite_traits`` — case 1 is a contract breakage, not a
    numeric edge case, and is already visible in ``failed_traits``.

    ``raw`` is not mutated, matching ``from_heritability_dict``'s own contract. Note the
    scrubbed copy is what both plotters receive too, so a figure can never render a value
    the returned numbers disowned.

    Separately — and NOT a scrub — this also collects the traits whose ``var_genetic`` and
    ``var_residual`` are both exactly ``0``. Those are *kept*: the delegate scored them and
    its verdict is not overridden here. But there is no variance to partition, so whatever
    h2 it attached is not a measurement, and which number it attached depends on the branch
    that produced it — ``no_variance`` hardcodes ``0.0``, whereas a mixed-model fit that
    returned exact zeros computes ``0/0`` → ``nan`` → clamped to ``1.0``. Reporting a
    *perfect* and a *zero* heritability for the same non-finding is exactly why the caller
    needs the names. The check is done here rather than in a second pass because this loop
    already reads both keys off every entry.

    Exact ``== 0`` is deliberate, not a tolerance: it is the condition that makes the
    delegate's denominator vanish. A tiny-but-nonzero variance (a near-constant column fits
    at ~1e-19 rather than 0) still yields a real, if unremarkable, quotient.
    """
    scrubbed = dict(raw)
    nonfinite: list[str] = []
    zero_variance: list[str] = []
    for trait in trait_cols:
        entry = scrubbed.get(trait)
        if not isinstance(entry, dict) or "heritability" not in entry:
            continue  # already a delegate-reported failure, or absent — handled by caller
        missing = [k for k in _REQUIRED_TRAIT_KEYS if k not in entry]
        if missing:
            scrubbed[trait] = {
                "error": f"delegate result missing required key(s): {missing}"
            }
            continue
        bad = [
            k
            for k in _FINITE_TRAIT_KEYS
            if not isinstance(entry[k], (int, float))
            or not math.isfinite(float(entry[k]))
        ]
        if bad:
            scrubbed[trait] = {
                "error": f"non-finite heritability result for key(s): {bad}"
            }
            nonfinite.append(trait)
            continue
        if float(entry["var_genetic"]) == 0.0 and float(entry["var_residual"]) == 0.0:
            zero_variance.append(trait)
    return scrubbed, nonfinite, zero_variance


def _comparison_frame(
    frame: ExperimentFrame,
    trait_cols: list[str],
    scrubbed: dict,
    genotype_col: str,
    replicate_col: Optional[str],
) -> pd.DataFrame:
    """Build ``create_variance_decomposition_plot``'s input via the upstream helper.

    Both behaviors here are carried over verbatim from the retired
    ``plot_variance_decomposition``, because both were correct:

    * traits the delegate could not score land as NaN-``heritability`` rows in
      ``compare_trait_heritabilities``' output — dropped, not plotted;
    * a **scored** trait whose ``var_genetic``/``var_residual`` is NaN means the delegated
      return contract changed shape. Refuse to render a silently zero-filled bar. The
      retired tool returned an error string; here it raises before ``create_run``, so no
      run is committed either.

    Called before ``generate_figures`` (never inside a plot closure) for two reasons: the
    "no run committed on a bad frame" guarantee above, and because ``generate_figures``
    holds a process-wide figure-registry lock — doing table work inside it would block
    every concurrent figure-creating call in the process.
    """
    from sleap_roots_analyze.statistics import compare_trait_heritabilities

    comparison = compare_trait_heritabilities(
        frame.df,
        trait_cols,
        scrubbed,
        genotype_col=genotype_col,
        replicate_col=replicate_col,
    )
    comparison = comparison[comparison["heritability"].notna()]
    inconsistent = comparison[
        comparison["var_genetic"].isna() | comparison["var_residual"].isna()
    ]
    if not inconsistent.empty:
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                "Variance decomposition unavailable: the heritability result for "
                f"{list(inconsistent['trait'])} is missing var_genetic/var_residual — "
                "the sleap-roots-analyze return contract changed shape."
            ),
            remedy=(
                "Re-run without plots=['create_variance_decomposition_plot'] to get the "
                "numeric result, and report the upstream contract change."
            ),
        )
    return comparison


def _plot_calls(
    scrubbed: dict, comparison: Optional[pd.DataFrame], threshold: float
) -> dict:
    """Zero-arg callables per catalog key, lazily importing the plotters.

    Imported here (not at module level) so importing this module never pulls in
    matplotlib — the default ``include_plots=False`` path stays import-clean.
    """
    from sleap_roots_analyze.visualization import (
        create_heritability_plot,
        create_variance_decomposition_plot,
    )

    calls: dict = {
        # Returns a list[Figure] above its traits_per_page default (50) —
        # `generate_figures` expands that into `<key>_page<N>` entries.
        "create_heritability_plot": lambda: create_heritability_plot(
            scrubbed, threshold=threshold
        ),
    }
    if comparison is not None:
        calls["create_variance_decomposition_plot"] = (
            lambda: create_variance_decomposition_plot(comparison, threshold=threshold)
        )
    return calls


def _rows(result: HeritabilityResult, trait_cols: list[str]) -> list[TraitH2]:
    """Scored traits as output rows, in the caller's resolved trait order."""
    by_trait = {t.trait: t for t in result.per_trait}
    return [
        TraitH2(
            trait=t.trait,
            h2=t.h2,
            passed_threshold=t.passed_threshold,
            var_genetic=t.var_genetic,
            var_residual=t.var_residual,
            n_genotypes=t.n_genotypes,
            n_observations=t.n_observations,
            model_type=t.model_type,
        )
        for t in (by_trait[c] for c in trait_cols if c in by_trait)
    ]


@as_mcp_tool(
    input_model=HeritabilityAnalysisParams,
    output_model=HeritabilityAnalysisResult,
    errors=(ExperimentReadError, CommitFailedError, ManifestReadError),
)
def heritability_analysis(
    params: HeritabilityAnalysisParams, *, provenance: Provenance
) -> HeritabilityAnalysisResult:
    """Estimate per-trait broad-sense heritability (H2) on a cleaned experiment.

    Returns the per-trait H2 values as data — not just a chart — and persists a versioned
    run (heritability.csv + heritability_result.json) with provenance. Optionally renders
    the two heritability figures from the same computation.

    REPLACES two retired tools: plot_heritability_bar is now
    include_plots=true, plots=["create_heritability_plot"]; plot_variance_decomposition is
    now include_plots=true, plots=["create_variance_decomposition_plot"]. Unlike those,
    this tool requires a cleaned version (run qc_clean first) and returns structured data
    plus links rather than a plain string with a static plot URL.

    Note: the bar plot orders traits by H2 descending, while per_trait and the persisted
    table preserve the experiment's trait order — the same numbers, sliced differently, so
    the inline top-50 of a wide experiment is not the first plotted page.

    Check zero_variance_traits before quoting mean_h2 or n_above_threshold: a trait listed
    there had no variance to partition, so its reported h2 is not a measurement (it will be
    0.0 or 1.0 depending on which upstream branch produced it) even though it counts toward
    both aggregates.
    """
    reader = _ports.reader()
    store = _ports.store()

    version_kwargs = {} if params.version is None else {"version": params.version}
    try:
        frame = reader.load_experiment(
            params.experiment, require_clean=True, **version_kwargs
        )
    except CleanedVersionRequiredError:
        raise BloomMCPError(
            code="tool_error",
            message=(
                f"No cleaned version of {params.experiment!r} exists; "
                f"heritability_analysis requires a cleaned input."
            ),
            remedy=(
                f"Run qc_clean on {params.experiment!r} first, then retry "
                f"heritability_analysis."
            ),
        ) from None

    genotype_col = frame.genotype_col
    if not genotype_col:
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                f"Heritability needs a genotype column to partition variance, and none "
                f"was detected for {params.experiment!r} (resolved roles: "
                f"genotype={frame.genotype_col!r}, replicate={frame.replicate_col!r}, "
                f"sample_id={frame.sample_id_col!r})."
            ),
            remedy=(
                "Use an experiment whose genotype column is detectable. A replicate "
                "column is not required — its values never enter the model."
            ),
        )
    # Deliberately NOT required: the delegate never uses replicate values
    # (value ~ 1 + (1|genotype)), and SupabaseReader resolves it as None for every frame.
    replicate_col = frame.replicate_col

    if params.trait_columns is None:
        trait_cols = list(frame.trait_cols)
    else:
        _validate_trait_subset(
            frame, params.trait_columns, params.experiment, require_certified=True
        )
        trait_cols = list(params.trait_columns)

    h2_raw = calculate_heritability_estimates(
        frame.df,
        trait_cols,
        genotype_col=genotype_col,
        replicate_col=replicate_col,
    )
    run_error = h2_raw.get("error")
    if isinstance(run_error, str):
        # Fixed, actionable text — the delegate's own string may carry backend internals.
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                "Heritability could not be estimated for this experiment — the delegate "
                "reported the required columns were unusable before scoring any trait."
            ),
            remedy=(
                "Confirm the cleaned experiment carries a genotype column with at least "
                "two genotypes, then retry."
            ),
        )

    scrubbed, nonfinite_traits, zero_variance_traits = _scrub_delegate_result(
        h2_raw, trait_cols
    )
    result = HeritabilityResult.from_heritability_dict(scrubbed, params.threshold)

    rows = _rows(result, trait_cols)
    scored = {r.trait for r in rows}
    # `from_heritability_dict` iterates only the keys the delegate returned, so a trait it
    # omitted ENTIRELY is invisible to `result.failed_traits`. Reconcile against what was
    # actually requested rather than adopting that list unexamined.
    failed_traits = [c for c in trait_cols if c not in scored]

    per_trait = rows[:_SUMMARY_TRAIT_CAP]
    omitted_traits = [r.trait for r in rows[_SUMMARY_TRAIT_CAP:]]

    prov = provenance.model_copy(update={"based_on_version": frame.source})

    # Figures are generated BEFORE create_run so an unknown key (or a broken comparison
    # frame) fails with no run committed. try/finally wraps the whole persistence region
    # so every figure closes even when the tempdir or store operations fail.
    figures: dict = {}
    try:
        if params.include_plots:
            import matplotlib

            matplotlib.use("Agg")
            validate_plot_keys(params.plots, _HERITABILITY_CATALOG_KEYS)
            keys = (
                list(params.plots)
                if params.plots is not None
                else sorted(_HERITABILITY_CATALOG_KEYS)
            )
            comparison = None
            if "create_variance_decomposition_plot" in keys:
                comparison = _comparison_frame(
                    frame, trait_cols, scrubbed, genotype_col, replicate_col
                )
                if comparison.empty:
                    # An empty decomposition figure is not a useful artifact. Skip it
                    # rather than persisting a blank PNG — failed_traits already names why.
                    keys = [
                        k for k in keys if k != "create_variance_decomposition_plot"
                    ]
                    comparison = None
            calls = _plot_calls(scrubbed, comparison, params.threshold)
            generate_figures({k: calls[k] for k in keys}, figures)

        with snapshot_frame(frame.df) as source_snapshot:
            run = store.create_run(
                experiment=params.experiment,
                tool_class=_TOOL_CLASS,
                provenance=prov,
                user_label=params.user_label,
                source_csv=source_snapshot,
                source=frame.resolved_source,
            )
            # Explicit columns so a zero-row table still writes its header rather than
            # an empty file: every trait failing is a legitimate outcome (the run is
            # still persisted, failed_traits names why), and a downstream reader must
            # not have to special-case "no columns to parse from file".
            pd.DataFrame(
                [r.model_dump() for r in rows],
                columns=list(TraitH2.model_fields),
            ).to_csv(run.staging_dir / _TABLE_NAME, index=False)
            (run.staging_dir / _RESULT_NAME).write_text(result.to_json())
            outputs: dict[str, str] = {
                _TABLE_NAME: _TABLE_NAME,
                _RESULT_NAME: _RESULT_NAME,
            }
            for name, fig in figures.items():
                rel = f"{name}.png"
                fig.savefig(run.staging_dir / rel, bbox_inches="tight")
                outputs[rel] = rel
            stored = store.commit(run, outputs)
    finally:
        close_figures(figures)

    return HeritabilityAnalysisResult(
        experiment=params.experiment,
        source=frame.source,
        n_samples=len(frame.df),
        genotype_col=genotype_col,
        replicate_col=replicate_col,
        method=result.method,
        threshold=params.threshold,
        n_traits_requested=len(trait_cols),
        n_traits_reported=len(rows),
        n_failed=len(failed_traits),
        failed_traits=failed_traits,
        nonfinite_traits=nonfinite_traits,
        zero_variance_traits=zero_variance_traits,
        mean_h2=result.mean_h2 if rows else None,
        n_above_threshold=result.n_above_threshold,
        per_trait=per_trait,
        truncated_in_summary=len(rows) > _SUMMARY_TRAIT_CAP,
        omitted_traits=omitted_traits,
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
        output_links=stored.output_links,
    )
