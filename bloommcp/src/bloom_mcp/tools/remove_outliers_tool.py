"""remove_outliers — trim outlier samples from a cleaned experiment.

The second granular, contract-wrapped quality tool (#378), sibling to ``qc_clean``.
It delegates **all** detection and removal to
``sleap_roots_analyze.remove_outlier_samples`` (which itself composes the tested
public detectors + ``remove_outliers_from_data``): the MCP contains **no** outlier
detection or removal logic and never touches the vendored
``bloom_mcp.outlier_detection`` filters.

On each call it reads the **cleaned** frame via the :class:`ExperimentReader` port
with ``require_clean=True`` (outlier detection requires the NaN-free, unique-index
table ``qc_clean`` produces), trims outlier samples with the chosen method
(``mahalanobis`` default, or ``isolation_forest``) and seed, then persists a
versioned run via the :class:`ResultStore` port under tool class ``qc`` — the
trimmed table under ``CLEANED_CSV_NAME`` + the ``outlier_report.json`` + provenance.

**Composition.** Writing the trimmed table under ``CLEANED_CSV_NAME`` in the ``qc``
tool class makes it the newest *cleaned version* the reader resolves, so
``qc_clean → remove_outliers → pca_analysis`` composes through ``require_clean=True``
with no reader change. The reader resolves "latest cleaned" as whichever ``qc`` run
committed most recently, so — as with ``qc_clean`` vs ``run_qc_workflow`` — prefer the
natural clean→trim order once per experiment.

**Structured errors, body-mapped.** ``require_clean`` with no cleaned version, a
degenerate trim (the delegate raises ``ValueError``/``OutlierRemovalError`` when the
trim leaves too few samples or no non-constant trait), a non-unique index, a
cross-method threshold, and an unknown plot key are all mapped **in this body** to a
structured :class:`BloomMCPError` — the contract's ``errors=`` path only yields
``tool_error``, never ``assumption_violated``/``invalid_input``. A defense-in-depth
no-NaN / row-count guard runs before any commit, so a delegate that *returns* rather
than raises a degenerate frame still cannot ship a corrupt "cleaned" artifact.

**Goodness of fit.** The mahalanobis chi-squared threshold is only meaningful when the
data fit the χ² assumption; the returned ``goodness_of_fit`` dict carries a
``fit_quality`` the agent should read — when it is poor (as on turface_19), prefer
``method="isolation_forest"`` with an explicit ``contamination``.
"""

from __future__ import annotations

import json
from typing import Literal, Optional

import pandas as pd
from pydantic import BaseModel, Field
from sleap_roots_analyze import plot_outlier_analysis, remove_outlier_samples

from bloom_mcp.contract import BloomMCPError, Provenance, as_mcp_tool
from bloom_mcp.contract import register as _contract_register
from bloom_mcp.data_access import (
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentReadError,
)
from bloom_mcp.data_utils import convert_to_json_serializable
from bloom_mcp.experiment_utils import CLEANED_CSV_NAME
from bloom_mcp.tools import _ports

_TOOL_CLASS = "qc"
_REPORT_NAME = "outlier_report.json"

_METHODS = ("mahalanobis", "isolation_forest")


class RemoveOutliersParams(BaseModel):
    """Inputs for ``remove_outliers``.

    Stochastic: the resolved ``seed`` (default 42) drives the detector's
    ``random_state`` and is recorded in provenance. The per-method threshold is
    forwarded to the delegate; a threshold set for the wrong method is rejected.
    """

    experiment: str = Field(
        ...,
        description="CSV filename from list_available_experiments (must be cleaned).",
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
        description="isolation_forest only: expected outlier fraction (e.g. 0.05). "
        "Do not set for mahalanobis.",
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


class RemoveOutliersResult(BaseModel):
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
    outlier_barcodes: list[str]
    run_ref: str
    version_dir: str
    manifest_path: str
    outputs: dict[str, str]


def _role_kwargs(frame: ExperimentFrame) -> dict[str, str]:
    """Forward the adapter-detected role columns, omitting any that are None."""
    roles = {
        "barcode_col": frame.sample_id_col,
        "genotype_col": frame.genotype_col,
        "replicate_col": frame.replicate_col,
    }
    return {k: v for k, v in roles.items() if v is not None}


def _validate_trait_subset(
    frame: ExperimentFrame, requested: list[str], experiment: str
) -> None:
    """Reject an unknown or non-numeric ``trait_columns`` subset as ``invalid_input``."""
    missing = [c for c in requested if c not in frame.df.columns]
    if missing:
        raise BloomMCPError(
            code="invalid_input",
            message=f"trait_columns names columns not in {experiment!r}: {missing}.",
            remedy="Use column names from load_experiment_data, or omit trait_columns.",
        )
    non_numeric = [
        c for c in requested if not pd.api.types.is_numeric_dtype(frame.df[c])
    ]
    if non_numeric:
        raise BloomMCPError(
            code="invalid_input",
            message=f"trait_columns includes non-numeric columns: {non_numeric}.",
            remedy="Pass only numeric trait columns; metadata columns cannot be detected on.",
        )


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


@as_mcp_tool(
    input_model=RemoveOutliersParams,
    output_model=RemoveOutliersResult,
    errors=(ExperimentReadError,),
)
def remove_outliers(
    params: RemoveOutliersParams, *, random_state: int, provenance: Provenance
) -> RemoveOutliersResult:
    """Trim outlier samples from ``experiment``'s cleaned table and persist the result."""
    reader = _ports.reader()
    store = _ports.store()

    # Consumer of cleaned data — detection requires the NaN-free, unique-index table
    # qc_clean produces. A missing cleaned version is the QC guardrail, mapped here to
    # a self-correctable assumption_violated (not the reader's raw message / tool_error).
    try:
        frame = reader.load_experiment(params.experiment, require_clean=True)
    except CleanedVersionRequiredError:
        raise BloomMCPError(
            code="assumption_violated",
            message=f"No cleaned version of {params.experiment!r} exists to trim outliers from.",
            remedy="Run qc_clean on this experiment first, then remove_outliers.",
        ) from None

    if params.trait_columns is not None:
        _validate_trait_subset(frame, params.trait_columns, params.experiment)
    trait_cols = params.trait_columns or frame.trait_cols
    detect_kwargs = _detect_kwargs(params)

    # Delegate ALL detection + removal. No outlier logic lives here. The delegate
    # *raises* ValueError (OutlierRemovalError is a ValueError subclass) on a degenerate
    # trim (too few survivors / no non-constant trait), a non-unique index, or a bad
    # detect kwarg — the common real-world misuse. Map it to a structured, self-correctable
    # error in the body (errors= would only yield tool_error, never assumption_violated).
    try:
        trimmed_df, report = remove_outlier_samples(
            frame.df,
            trait_cols,
            method=params.method,
            random_state=random_state,
            **_role_kwargs(frame),
            **detect_kwargs,
        )
    except ValueError as exc:
        raise BloomMCPError(
            code="assumption_violated",
            message=f"Outlier removal could not produce an analysis-ready table: {exc}",
            remedy=_RELAX_REMEDY,
        ) from None

    n_input = int(report["n_input_samples"])
    n_outliers = int(report["n_outliers"])
    n_output = int(report["n_output_samples"])

    # Defense-in-depth guard before any commit — for a delegate that *returns* (rather
    # than raises on) a degenerate frame. Trimming only drops rows and the input is
    # already NaN-free, so a NaN-bearing or empty trimmed table is not reachable in
    # principle; this makes that guarantee explicit and version-independent, so no
    # corrupt "cleaned" artifact can be resolved by a downstream require_clean consumer.
    residual_nans = int(trimmed_df[trait_cols].isna().sum().sum())
    if n_output <= 0 or n_output > n_input or residual_nans > 0:
        reason = (
            "removed every sample"
            if n_output <= 0
            else (
                "returned more samples than it received"
                if n_output > n_input
                else f"left {residual_nans} NaN cell(s) in the trait columns"
            )
        )
        raise BloomMCPError(
            code="assumption_violated",
            message=f"Outlier removal produced no analysis-ready table — it {reason}.",
            remedy=_RELAX_REMEDY,
        )

    # Optional plots — persistence only, no plotting logic. Generate before persisting so
    # an unknown requested key fails as invalid_input with no run committed.
    figures: dict[str, "object"] = {}
    if params.include_plots:
        figures = _make_figures(frame, trait_cols, params, random_state, detect_kwargs)

    run = store.create_run(
        experiment=params.experiment,
        tool_class=_TOOL_CLASS,
        provenance=provenance,
        user_label=params.user_label,
    )
    outputs: dict[str, str] = {
        CLEANED_CSV_NAME: CLEANED_CSV_NAME,
        _REPORT_NAME: _REPORT_NAME,
    }
    trimmed_df.to_csv(run.staging_dir / CLEANED_CSV_NAME, index=False)
    (run.staging_dir / _REPORT_NAME).write_text(
        json.dumps(convert_to_json_serializable(report), indent=2)
    )
    for name, fig in figures.items():
        rel = f"{name}.png"
        fig.savefig(run.staging_dir / rel, bbox_inches="tight")
        _close_figure(fig)
        outputs[rel] = rel

    stored = store.commit(run, outputs)

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
        goodness_of_fit=convert_to_json_serializable(report.get("goodness_of_fit")),
        outlier_barcodes=[str(b) for b in report.get("outlier_barcodes", [])],
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
    )


def _make_figures(
    frame: ExperimentFrame,
    trait_cols: list[str],
    params: RemoveOutliersParams,
    random_state: int,
    detect_kwargs: dict[str, float],
) -> dict[str, "object"]:
    """Delegate figure generation to plot_outlier_analysis; validate a requested subset.

    The MCP owns no plotting logic: it persists what the delegate returns. An explicit
    ``plots`` subset is validated against the method's available figure keys (unknown →
    invalid_input) rather than surfacing the delegate's opaque ValueError.
    """
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


def _close_figure(fig: "object") -> None:
    try:
        import matplotlib.pyplot as plt

        plt.close(fig)
    except Exception:  # pragma: no cover - best-effort cleanup
        pass


def register(mcp):
    """Register remove_outliers with the MCP server."""
    return _contract_register(mcp, remove_outliers)
