"""remove_outliers — trim outlier samples from a cleaned experiment.

The second granular, contract-wrapped quality tool (#378), sibling to ``qc_clean``.
It delegates **all** detection and removal to
``sleap_roots_analyze.remove_outlier_samples`` (which itself composes the tested
public detectors + ``remove_outliers_from_data``): the MCP contains **no** outlier
detection or removal logic and never touches the vendored
``bloom_mcp.outlier_detection`` filters.

**Persists under its own dedicated tool class ``outliers``** (#420) — plural,
deliberately distinct from the retired legacy ``outlier`` (singular) class the old,
vendored-detector ``run_outlier_workflow`` used before ``devendor-bloommcp-analysis``
retired it (that class is kept read-only for historical lookups, never written to
again). Persisting under a class of its own, rather than sharing ``qc_clean``'s
class, is what makes the trim's resolution as "latest cleaned" independent of
whether ``qc_clean`` happens to run again afterward.

On each call it reads the **cleaned** frame via the :class:`ExperimentReader` port
with ``require_clean=True`` and **``version="latest_qc"``** — the plain-clean tier
specifically, ignoring any prior trim of its own (outlier detection requires the
NaN-free, unique-index table ``qc_clean`` produces) — trims outlier samples with the
chosen method (``mahalanobis`` default, or ``isolation_forest``) and seed, then
persists a versioned run via the :class:`ResultStore` port under tool class
``outliers`` — the trimmed table under ``CLEANED_CSV_NAME`` + the
``outlier_report.json`` + provenance.

**Composition.** For every *other* ``require_clean=True`` consumer (``pca_analysis``,
``umap_analysis``, ``clustering``, ``descriptive_stats``,
``cross_experiment_correlations``), the reader's default ``version="latest"``
prefers the ``outliers`` class's latest entry over ``qc``'s whenever one exists at
all — a fixed priority, not a recency comparison — so
``qc_clean → remove_outliers → pca_analysis`` composes with no call-site change, and
a later plain ``qc_clean`` re-run does **not** silently revert an existing trim.
``remove_outliers`` itself reads via ``version="latest_qc"`` specifically so that
same later ``qc_clean`` re-run *is* immediately visible to its own next invocation —
without this, ``remove_outliers`` would keep re-trimming its own stale prior output
forever, never seeing the fresh clean.

**Disclosed trade-off (not a bug).** Once any ``outliers`` version exists for an
experiment, a plain ``qc_clean`` re-run does not become "latest" for other
consumers on its own — only a fresh ``remove_outliers`` run (which always reads the
current ``qc`` clean via ``version="latest_qc"``) makes the new clean reachable
again. This is the intentional fix for the #420 hazard (a `qc_clean` re-run
silently reverting a trim with no signal): the trade-off is auditable (the stale
``outliers`` entry's own ``based_on_version`` still names the ``qc`` version it was
trimmed from) and recoverable by a known action (re-run ``remove_outliers``), unlike
the silent revert it replaces. See
``openspec/changes/archive/2026-08-09-fix-bloommcp-remove-outliers-tool-class/design.md`` for the full
reasoning, including why a recency-based cross-class comparison (an earlier draft of
this fix) does not actually work.

**Structured errors, body-mapped.** ``require_clean`` with no cleaned version, a
degenerate trim (the delegate raises ``ValueError``/``OutlierRemovalError`` when the
trim leaves too few samples or no non-constant trait), a non-unique index, a
cross-method threshold, and an unknown plot key are all mapped **in this body** to a
structured :class:`BloomMCPError` — the contract's ``errors=`` path only yields
``tool_error``, never ``assumption_violated``/``invalid_input``. A defense-in-depth
no-NaN / row-count guard runs before any commit, so a delegate that *returns* rather
than raises a degenerate frame still cannot ship a corrupt "cleaned" artifact.

**Goodness of fit is enforced, not merely advisory (#419).** The mahalanobis
chi-squared threshold is only meaningful when the data fit the χ² assumption. When the
delegate's fit is untrustworthy (``fit_is_trustworthy`` would be ``False`` —
poor/very_poor/unknown ``fit_quality``, as on both turface_19 and cylinder under
mahalanobis defaults), the tool raises a structured ``assumption_violated`` error
**before persisting anything** — naming ``method="isolation_forest"`` with an explicit
``contamination`` as the remedy — rather than silently committing a trim on a bogus
threshold. The error message embeds the counts and flagged barcodes so a caller can
still inspect what would have been flagged even though nothing is persisted. This gate
never fires for ``isolation_forest`` (no chi-squared assumption) or an
acceptable-or-better mahalanobis fit.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Literal, Optional

import pandas as pd
from pydantic import BaseModel, Field
from sleap_roots_analyze import plot_outlier_analysis, remove_outlier_samples
from sleap_roots_analyze.outlier_removal import OutlierRemovalError

from bloom_mcp.contract import BloomMCPError, Provenance, RunLinks, as_mcp_tool
from bloom_mcp.data_access import (
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentReadError,
)
from sleap_roots_analyze.data_utils import convert_to_json_serializable
from bloom_mcp.experiment_utils import (
    CLEANED_CSV_NAME,
    OUTLIER_REPORT_NAME,
    OUTLIERS_TOOL_CLASS,
    # This module-level name shares its name with `RemoveOutliersResult.fit_is_
    # trustworthy` (the Pydantic field, below) and was previously safe from
    # shadowing only because the function was underscore-prefixed pre-#593. The
    # local variable in `remove_outliers()` is deliberately named `trustworthy`,
    # not `fit_is_trustworthy`, to avoid a future `fit_is_trustworthy =
    # fit_is_trustworthy(...)` silently reassigning this import to a bool/None
    # within that function's scope. Keep it that way if this function is ever
    # touched again.
    fit_is_trustworthy,
)
from bloom_mcp.tools import _ports
from bloom_mcp.tools._qc_shared import _role_kwargs, _validate_trait_subset

if TYPE_CHECKING:  # matplotlib stays out of the runtime import graph (Tier-0)
    from matplotlib.figure import Figure

_TOOL_CLASS = OUTLIERS_TOOL_CLASS
_REPORT_NAME = OUTLIER_REPORT_NAME

# The delegate's own default for isolation_forest's ``contamination`` kwarg
# (``sleap_roots_analyze.outlier_detection.detect_outliers_isolation_forest``) — quoted
# in the #419 gate's remedy and the ``contamination`` field description below so the two
# never drift apart from each other or from the delegate.
_ISOLATION_FOREST_DEFAULT_CONTAMINATION = 0.1

# Cap on how many outlier_barcodes the #419 fit-gate embeds in its (unpersisted) error
# message — both characterized fixtures flag well under this today, but the message is
# a plain string with no pagination, so a much noisier dataset shouldn't be able to
# produce an unbounded one.
_MAX_BARCODES_IN_MESSAGE = 50


class RemoveOutliersParams(BaseModel):
    """Inputs for ``remove_outliers``.

    Stochastic: the resolved ``seed`` (default 42) drives the detector's
    ``random_state`` and is recorded in provenance. The per-method threshold is
    forwarded to the delegate; a threshold set for the wrong method is rejected.
    """

    experiment: str = Field(
        ...,
        description="Experiment identifier from list_available_experiments (must be "
        "cleaned).",
    )
    version: Optional[str] = Field(
        default=None,
        description="Pin outlier detection to a specific committed cleaned "
        "version (e.g. 'v2'; see list_existing_analyses). Omit to use the "
        "latest plain-clean version (today's default is 'latest_qc', not "
        "'latest' — it ignores any prior outlier trim so this tool trims "
        "from the plain clean, not from its own previous output).",
    )
    method: Literal["mahalanobis", "isolation_forest"] = Field(
        default="mahalanobis",
        description="Detection method. 'mahalanobis' (default) flags samples far in "
        "Mahalanobis distance (chi-squared threshold); 'isolation_forest' flags a "
        "'contamination' fraction. Prefer isolation_forest when goodness_of_fit is poor.",
    )
    trait_columns: Optional[list[str]] = Field(
        default=None,
        description="Subset of trait columns to detect on; omit to use all detected traits.",
    )
    seed: int = Field(
        default=42,
        ge=0,
        description="Random seed for the detector (recorded in provenance for reproducibility).",
    )
    chi2_percentile: Optional[float] = Field(
        default=None,
        gt=0.0,
        lt=100.0,
        description="mahalanobis only: chi-squared percentile threshold (e.g. 97.5). "
        "Higher = fewer outliers. Do not set for isolation_forest.",
    )
    contamination: Optional[float] = Field(
        default=None,
        gt=0.0,
        lt=0.5,
        description=(
            "isolation_forest only: expected outlier fraction (e.g. "
            f"{_ISOLATION_FOREST_DEFAULT_CONTAMINATION}, the delegate's own default). "
            "Do not set for mahalanobis."
        ),
    )
    include_plots: bool = Field(
        default=False,
        description="If true, persist detection figures as run artifacts (returned as "
        "resource links). Set when the user wants to see/inspect the flagged outliers.",
    )
    plots: Optional[list[str]] = Field(
        default=None,
        description="Optional subset of figure keys to persist (see plot_outlier_analysis); "
        "omit to persist every figure the method produces.",
    )
    user_label: Optional[str] = Field(
        default=None,
        description="Optional slug appended to the version directory name.",
    )


class RemoveOutliersResult(RunLinks):
    """The numeric outlier report + links to the persisted trimmed run (no table inline)."""

    experiment: str
    source: str
    method: str
    n_input_samples: int
    n_outliers: int
    n_output_samples: int
    removal_fraction: float
    # Method-dependent — all three are None for isolation_forest.
    threshold_type: Optional[str] = None
    threshold_value: Optional[float] = None
    goodness_of_fit: Optional[dict] = None
    # Machine-visible trust flag derived from goodness_of_fit.fit_quality: True when
    # the mahalanobis fit is acceptable-or-better, None when there is no fit report
    # (isolation_forest has no chi-squared assumption). NEVER False here (#419) — a
    # poor/very_poor/unknown fit raises assumption_violated before this result is ever
    # constructed, so an untrustworthy fit is never returned, only gated.
    fit_is_trustworthy: Optional[bool] = None
    outlier_barcodes: list[str]


def _detect_kwargs(params: RemoveOutliersParams) -> dict[str, float]:
    """Forward the per-method threshold; reject a threshold set for the wrong method."""
    if params.method == "mahalanobis":
        if params.contamination is not None:
            raise BloomMCPError(
                code="invalid_input",
                message="contamination is only valid for method='isolation_forest'.",
                remedy="Drop contamination, or set method='isolation_forest'. "
                "For mahalanobis, tune chi2_percentile.",
            )
        return (
            {}
            if params.chi2_percentile is None
            else {"chi2_percentile": params.chi2_percentile}
        )
    # isolation_forest
    if params.chi2_percentile is not None:
        raise BloomMCPError(
            code="invalid_input",
            message="chi2_percentile is only valid for method='mahalanobis'.",
            remedy="Drop chi2_percentile, or set method='mahalanobis'. "
            "For isolation_forest, tune contamination.",
        )
    return (
        {} if params.contamination is None else {"contamination": params.contamination}
    )


_RELAX_REMEDY = (
    "Relax the threshold (raise chi2_percentile for mahalanobis, or lower "
    "contamination for isolation_forest) so more samples survive, and retry."
)

# A bare ValueError here is NOT a too-aggressive trim, so "relax the threshold" would
# mislead. But it also is not always a *malformed* frame — the same except catches both a
# structural fault (non-unique index / duplicate columns, which re-cleaning fixes) and a
# detector-cannot-fit fault on a well-formed frame (too few samples, or near-constant /
# singular covariance, which re-cleaning does NOT fix). Distinguishing them from a bare
# ValueError would mean fragile message-sniffing, so the remedy stays neutral and lists
# the real options rather than prescribing a re-clean that may loop.
_STRUCTURAL_REMEDY = (
    "The cleaned table could not be analyzed by the detector — either a structural "
    "fault (a non-unique index or duplicate column names, which re-running qc_clean "
    "fixes) or too few / near-constant (singular-covariance) samples for the chosen "
    "method to fit. Check the cleaned input, narrow trait_columns, or try "
    "method='isolation_forest', which is more robust to ill-conditioned covariance."
)


def _rows_subset(frame: ExperimentFrame, trimmed_df: pd.DataFrame) -> bool:
    """True when the trimmed rows are a *multiset* subset of the cleaned input's rows.

    Prefer the detected sample-id column — it is the row identity that survives
    ``to_csv(index=False)`` and that a downstream reader keys on; fall back to the
    frame index when no id column is detected (a barcode-less cleaned frame). Outlier
    removal only drops rows, so this always holds against the real delegate — the
    check is defense-in-depth against a delegate that *returns* a mutated frame.

    Uses multiset (``Counter``) containment, not plain set membership: a cleaned frame
    is unique-indexed but its id *column* may repeat, and set membership would then
    vacuously pass a returned frame that duplicated or invented rows. Multiset
    containment requires each id to appear no more often than it does in the input.
    """
    id_col = frame.sample_id_col
    if id_col and id_col in trimmed_df.columns and id_col in frame.df.columns:
        return Counter(trimmed_df[id_col]) <= Counter(frame.df[id_col])
    return Counter(trimmed_df.index) <= Counter(frame.df.index)


def _barcodes(report: dict) -> list[str]:
    """Coerce the delegate's ``outlier_barcodes`` (``None`` for a barcode-less frame)
    to a plain ``list[str]``. Shared by the fit-gate's raise (which sorts this for a
    deterministic message) and the successful return path (which does not)."""
    return [str(b) for b in (report.get("outlier_barcodes") or [])]


@as_mcp_tool(
    input_model=RemoveOutliersParams,
    output_model=RemoveOutliersResult,
    errors=(ExperimentReadError,),
)
def remove_outliers(
    params: RemoveOutliersParams, *, random_state: int, provenance: Provenance
) -> RemoveOutliersResult:
    """Trim outlier samples from a cleaned experiment and persist the trimmed run.

    Returns a numeric outlier report by default (no table inline). When the
    mahalanobis chi-squared fit is untrustworthy (``fit_is_trustworthy`` would be
    ``false`` — poor/very_poor/unknown ``goodness_of_fit.fit_quality``), this raises a
    structured ``assumption_violated`` error instead of persisting the trim — the
    message embeds the outlier counts and flagged barcodes, and the remedy names
    ``method="isolation_forest"`` with an explicit ``contamination``. Set
    ``include_plots=true`` when the user wants to see or inspect the flagged outliers
    on a trustworthy-fit run; the figures are persisted and returned as resource links.
    """
    reader = _ports.reader()
    store = _ports.store()

    # Consumer of cleaned data — detection requires the NaN-free, unique-index table
    # qc_clean produces. A missing cleaned version is the QC guardrail, mapped here to
    # a self-correctable assumption_violated (not the reader's raw message / tool_error).
    try:
        # version="latest_qc" is this tool's own default (not the Protocol's generic
        # "latest"): always the current plain clean, never a prior trim of our own —
        # see the module docstring's Composition section for why (a fresh qc_clean
        # must always be visible to the *next* remove_outliers call). #626: an
        # explicit params.version overrides that default; omitting it preserves it.
        frame = reader.load_experiment(
            params.experiment,
            require_clean=True,
            version=params.version if params.version is not None else "latest_qc",
        )
    except CleanedVersionRequiredError:
        raise BloomMCPError(
            code="assumption_violated",
            message=f"No cleaned version of {params.experiment!r} exists to trim outliers from.",
            remedy="Run qc_clean on this experiment first, then remove_outliers.",
        ) from None

    # This run derives from the cleaned version it trims, not from raw — record that in
    # provenance so the manifest lineage is honest (the Provenance default is "raw",
    # correct for qc_clean the raw-producer, wrong for this cleaned-consumer). Also what
    # makes an order-dependent un-trim (a later qc_clean re-run reverting "latest
    # cleaned") auditable after the fact.
    # NB: this is currently the *only* place a tool mutates the injected Provenance. It is
    # correct and tested, but the pattern should not proliferate — if a third tool needs
    # it, grow the contract a first-class `create_run(based_on=...)` seam rather than
    # copying this mutation.
    provenance.based_on_version = frame.source

    if params.trait_columns is not None:
        _validate_trait_subset(
            frame, params.trait_columns, params.experiment, require_certified=True
        )
    trait_cols = params.trait_columns or frame.trait_cols
    detect_kwargs = _detect_kwargs(params)

    # Delegate ALL detection + removal. No outlier logic lives here. The delegate
    # *raises* on the common real-world misuse — mapped to a structured, self-correctable
    # error in the body (errors= would only yield tool_error, never assumption_violated).
    # Distinguish the two raise families so the remedy is not misleading:
    #   * OutlierRemovalError (a ValueError subclass) — the trim itself was too
    #     aggressive (below min survivors / no non-constant trait) → relax the threshold.
    #   * a bare ValueError — a detector precondition failed (a structural fault like a
    #     non-unique index, or a well-formed-but-unfittable frame: too few / singular-
    #     covariance samples) → the neutral _STRUCTURAL_REMEDY, not relax-the-threshold.
    # (Cross-method detect kwargs are pre-empted by _detect_kwargs above; the cleaned
    # input is NaN-free, so the NaN precondition path is unreachable here.)
    try:
        trimmed_df, report = remove_outlier_samples(
            frame.df,
            trait_cols,
            method=params.method,
            random_state=random_state,
            **_role_kwargs(frame),
            **detect_kwargs,
        )
    except OutlierRemovalError as exc:
        raise BloomMCPError(
            code="assumption_violated",
            message=f"Outlier removal could not produce an analysis-ready table: {exc}",
            remedy=_RELAX_REMEDY,
        ) from None
    except ValueError as exc:
        raise BloomMCPError(
            code="assumption_violated",
            message=f"Outlier removal could not run on this cleaned table: {exc}",
            remedy=_STRUCTURAL_REMEDY,
        ) from None

    n_input = int(report["n_input_samples"])
    n_outliers = int(report["n_outliers"])
    n_output = int(report["n_output_samples"])

    # Fit-trustworthiness gate (#419) — BEFORE the structural guard below, before any
    # plots=/figure handling, and before any ResultStore interaction. The mahalanobis
    # chi-squared threshold means nothing when the data don't fit that distribution;
    # fit_is_trustworthy used to be computed only in the return value, by which point
    # persistence had already committed the untrustworthy-fit trim as the new "latest
    # cleaned" version. Gating here instead means an untrustworthy fit never becomes
    # canonical, not even advisorily. Never fires for isolation_forest
    # (fit_is_trustworthy is always None — no chi-squared assumption to violate) or an
    # acceptable-or-better mahalanobis fit (fit_is_trustworthy is True).
    goodness_of_fit = convert_to_json_serializable(report.get("goodness_of_fit"))
    trustworthy = fit_is_trustworthy(goodness_of_fit)
    if trustworthy is False:
        fit_quality = (
            goodness_of_fit.get("fit_quality")
            if isinstance(goodness_of_fit, dict)
            else None
        )
        # Nothing is persisted on this path, so the barcodes are embedded here — the
        # only way a caller can still inspect what would have been flagged. Capped
        # (_MAX_BARCODES_IN_MESSAGE) so a noisier dataset than today's two fixtures
        # can't produce an unbounded plain-string message.
        barcodes = sorted(_barcodes(report))
        shown_barcodes = barcodes[:_MAX_BARCODES_IN_MESSAGE]
        omitted = len(barcodes) - len(shown_barcodes)
        barcodes_repr = (
            f"{shown_barcodes} (+{omitted} more)"
            if omitted > 0
            else f"{shown_barcodes}"
        )
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                f"The mahalanobis chi-squared fit is untrustworthy "
                f"(fit_quality={fit_quality!r}) for {params.experiment!r} — the "
                f"flagged threshold does not mean what it claims to. Would have "
                f"flagged n_outliers={n_outliers} of n_input_samples={n_input} "
                f"(n_output_samples={n_output}), outlier_barcodes={barcodes_repr}. No "
                f"run was persisted."
            ),
            remedy=(
                "Re-run with method='isolation_forest' and "
                f"contamination={_ISOLATION_FOREST_DEFAULT_CONTAMINATION} (the "
                "delegate's own default) — it has no chi-squared assumption to "
                "violate, though it also has no fit-quality self-diagnostic of its "
                "own, so choose contamination deliberately rather than assuming the "
                "default suits this data."
            ),
        )

    # Defense-in-depth guard before any commit — for a delegate that *returns* (rather
    # than raises on) a degenerate frame. Trimming only drops rows from an already
    # NaN-free input, so a NaN-bearing, empty, grown, row-foreign, or trait-column-
    # mutated table is not reachable in principle; asserting it here makes the spec's
    # guarantee ("a row-subset of the cleaned input with its trait columns unchanged,
    # no NaNs, at least one sample") explicit and version-independent, so no corrupt
    # "cleaned" artifact can be resolved by a downstream require_clean consumer. The
    # row-subset / trait-identity checks inspect the returned frame itself — not the
    # delegate's self-reported n_input/n_output counts, which would only validate the
    # delegate against itself. (The n_output > n_input branch cannot fire against the
    # real row-dropping delegate; it is retained as cheap defense-in-depth against a
    # future/misbehaving delegate that grew the frame.)
    # Compute missing_traits first and scope the NaN scan to present columns so a
    # dropped trait column surfaces as a structured guard failure, not a KeyError.
    missing_traits = [c for c in trait_cols if c not in trimmed_df.columns]
    present_traits = [c for c in trait_cols if c in trimmed_df.columns]
    residual_nans = (
        int(trimmed_df[present_traits].isna().sum().sum()) if present_traits else 0
    )
    rows_are_subset = _rows_subset(frame, trimmed_df)
    if (
        n_output <= 0
        or n_output > n_input
        or residual_nans > 0
        or missing_traits
        or not rows_are_subset
    ):
        reason = (
            "removed every sample"
            if n_output <= 0
            else (
                "returned more samples than it received"
                if n_output > n_input
                else (
                    f"left {residual_nans} NaN cell(s) in the trait columns"
                    if residual_nans > 0
                    else (
                        f"dropped or renamed the trait column(s) {missing_traits}"
                        if missing_traits
                        else "returned rows that are not a subset of the cleaned input"
                    )
                )
            )
        )
        raise BloomMCPError(
            code="assumption_violated",
            message=f"Outlier removal produced no analysis-ready table — it {reason}.",
            remedy=_RELAX_REMEDY,
        )

    # Optional plots — persistence only, no plotting logic. Generate before persisting so
    # an unknown requested key fails as invalid_input with no run committed.
    figures: dict[str, "Figure"] = {}
    if params.include_plots:
        figures = _make_figures(frame, trait_cols, params, random_state, detect_kwargs)

    # try/finally spanning the WHOLE persistence region: matplotlib figures are
    # process-global, so any failure between figure creation and commit (create_run /
    # to_csv / write_text / savefig) must still close every open figure or they leak.
    try:
        # No source_csv= (unlike qc_clean): remove_outliers reads a persisted *cleaned*
        # version, not a raw CSV on the local FS, so there is no local input file to
        # content-address the run to.
        run = store.create_run(
            experiment=params.experiment,
            tool_class=_TOOL_CLASS,
            provenance=provenance,
            user_label=params.user_label,
            source=frame.resolved_source,
        )
        outputs: dict[str, str] = {
            CLEANED_CSV_NAME: CLEANED_CSV_NAME,
            _REPORT_NAME: _REPORT_NAME,
        }
        # index=False (parity with qc_clean, and what the reader's detect_columns expects
        # on reload). NB: for a barcode-less cleaned frame (no sample-id column) the
        # trimmed rows are then traceable only *positionally* — no stable id survives to
        # map a retained row back to its scan/plant/wave. That is inherent to a
        # barcode-less input (the identity was never present); requiring a sample-id at
        # qc_clean (#403) removes the case upstream. (_rows_subset falls back to the index
        # for the guard in exactly this case.)
        trimmed_df.to_csv(run.staging_dir / CLEANED_CSV_NAME, index=False)
        (run.staging_dir / _REPORT_NAME).write_text(
            json.dumps(convert_to_json_serializable(report), indent=2)
        )
        for name, fig in figures.items():
            rel = f"{name}.png"
            fig.savefig(run.staging_dir / rel, bbox_inches="tight")
            outputs[rel] = rel
        stored = store.commit(run, outputs)
    finally:
        for fig in figures.values():
            _close_figure(fig)

    return RemoveOutliersResult(
        experiment=params.experiment,
        source=frame.source,
        method=str(report.get("method", params.method)),
        n_input_samples=n_input,
        n_outliers=n_outliers,
        n_output_samples=n_output,
        removal_fraction=round(float(report["removal_fraction"]), 6),
        threshold_type=report.get("threshold_type"),
        threshold_value=(
            float(report["threshold_value"])
            if report.get("threshold_value") is not None
            else None
        ),
        goodness_of_fit=goodness_of_fit,
        fit_is_trustworthy=trustworthy,
        # The delegate sets outlier_barcodes to None (not []) when the frame has no
        # barcode column, and the key is always present — so `.get(..., [])` returns
        # None and `for b in None` would crash into an opaque internal_error. `_barcodes`
        # coerces that valid barcode-less return to an empty list.
        outlier_barcodes=_barcodes(report),
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
        output_links=stored.output_links,
    )


def _make_figures(
    frame: ExperimentFrame,
    trait_cols: list[str],
    params: RemoveOutliersParams,
    random_state: int,
    detect_kwargs: dict[str, float],
) -> dict[str, "Figure"]:
    """Delegate figure generation to plot_outlier_analysis; validate a requested subset.

    The MCP owns no plotting logic: it persists what the delegate returns. An explicit
    ``plots`` subset is validated against the method's available figure keys (unknown →
    invalid_input) rather than surfacing the delegate's opaque ValueError.
    """
    # Import matplotlib lazily and select the headless Agg backend only on the plots
    # path — this preserves the Tier-0 import-clean guarantee (matplotlib stays out of
    # the module's runtime import graph), unlike the top-level viz_tools/correlation_tools.
    import matplotlib

    matplotlib.use("Agg")

    available = plot_outlier_analysis(
        frame.df,
        trait_cols,
        method=params.method,
        random_state=random_state,
        which=None,
        **_role_kwargs(frame),
        **detect_kwargs,
    )
    if params.plots is None:
        return available
    unknown = [k for k in params.plots if k not in available]
    if unknown:
        for fig in available.values():
            _close_figure(fig)
        raise BloomMCPError(
            code="invalid_input",
            message=f"plots names figure key(s) not produced by method={params.method!r}: "
            f"{unknown}. Available: {sorted(available)}.",
            remedy="Use one of the available figure keys, or omit plots to persist all.",
        )
    selected = {k: available[k] for k in params.plots}
    for name, fig in available.items():
        if name not in selected:
            _close_figure(fig)
    return selected


def _close_figure(fig: "Figure") -> None:
    try:
        import matplotlib.pyplot as plt

        plt.close(fig)
    except Exception:  # pragma: no cover - best-effort cleanup
        pass
