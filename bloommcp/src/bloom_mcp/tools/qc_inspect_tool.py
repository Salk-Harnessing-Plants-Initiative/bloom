"""qc_inspect — read-only NaN / missingness visualization at QC time (Tier 3 / #360).

The read-only sibling of ``qc_clean``. Where ``qc_clean`` *produces* a cleaned run,
``qc_inspect`` *produces a report* that helps the agent choose ``qc_clean``'s
thresholds before committing a clean — so it does not run blind on the defaults.

On each call it reads the **raw** frame via the :class:`ExperimentReader` port (no
``require_clean``), then delegates **all** EDA to ``sleap_roots_analyze``: it runs
``apply_data_cleanup_filters`` to get the cleanup log, feeds it to
``create_trait_eda_plots`` (per-trait NaN/zero/outlier bar charts with the threshold
lines drawn + the traits-actually-removed panel), takes the ``missing_data_pattern``
heatmap from ``create_exploratory_summary_plots``, and ``inspect_nan_samples`` for the
per-sample NaN table. The MCP contains **no** EDA/plotting logic and never calls the
vendored ``bloom_mcp.data_cleanup``.

It persists a versioned **report** run via the :class:`ResultStore` port under tool
class ``qc_inspect`` — deliberately **not** ``qc`` — so the reader never resolves it as
a cleaned version: ``qc_inspect`` is read-only and produces no cleaned table. The result
returns a small inline summary + a structured **recommendation** (which traits to drop
and the sample loss avoided) + links to the persisted figures / CSV / recommendation
JSON — never inline blobs.
"""

from __future__ import annotations

import json
import math
from typing import Optional

from pydantic import BaseModel, Field

import matplotlib

matplotlib.use("Agg")  # headless: pin Agg before importing the analyze viz funcs below
import matplotlib.pyplot as plt
from sleap_roots_analyze import (
    apply_data_cleanup_filters,
    create_exploratory_summary_plots,
    create_trait_eda_plots,
    inspect_nan_samples,
)

from bloom_mcp.contract import Provenance, as_mcp_tool
from bloom_mcp.contract import register as _contract_register
from bloom_mcp.data_access import ExperimentReadError
from bloom_mcp.data_utils import convert_to_json_serializable
from bloom_mcp.experiment_utils import TRAITS_DIR
from bloom_mcp.tools import _ports
from bloom_mcp.tools._qc_shared import _role_kwargs, _validate_trait_subset

_TOOL_CLASS = "qc_inspect"
_NAN_SAMPLES_CSV = "nan_samples.csv"
_RECOMMENDATION_JSON = "recommendation.json"
_HEATMAP_PNG = "missing_data_pattern.png"

# Canonical QC-pipeline defaults — identical to qc_clean's, so qc_inspect's overlay
# lines and recommendation reflect the clean a default qc_clean would actually apply.
_CANONICAL_MAX_ZEROS_PER_TRAIT = 0.5
_CANONICAL_MAX_NANS_PER_TRAIT = 0.2
_CANONICAL_MAX_NANS_PER_SAMPLE = 0.0
_CANONICAL_MIN_SAMPLES_PER_TRAIT = 10


class QCInspectParams(BaseModel):
    """Inputs for ``qc_inspect`` — the same threshold knobs as ``qc_clean`` (no ``seed``)."""

    experiment: str = Field(
        ..., description="CSV filename from list_available_experiments."
    )
    trait_columns: Optional[list[str]] = Field(
        default=None,
        description="Subset of trait columns to inspect; omit to inspect all detected traits.",
    )
    max_zeros_per_trait: float = Field(
        default=_CANONICAL_MAX_ZEROS_PER_TRAIT,
        ge=0.0,
        le=1.0,
        description="Zero-fraction threshold drawn on the overlay (same default as qc_clean).",
    )
    max_nans_per_trait: float = Field(
        default=_CANONICAL_MAX_NANS_PER_TRAIT,
        ge=0.0,
        le=1.0,
        description="NaN-fraction threshold drawn on the overlay (same default as qc_clean).",
    )
    max_nans_per_sample: float = Field(
        default=_CANONICAL_MAX_NANS_PER_SAMPLE,
        ge=0.0,
        le=1.0,
        description="Per-sample NaN-fraction threshold used to model sample drops "
        "(same default as qc_clean).",
    )
    min_samples_per_trait: int = Field(
        default=_CANONICAL_MIN_SAMPLES_PER_TRAIT,
        ge=1,
        description="Min valid samples to keep a trait (same default as qc_clean).",
    )
    user_label: Optional[str] = Field(
        default=None,
        description="Optional slug appended to the version directory name.",
    )


class QCInspectRecommendation(BaseModel):
    """A threshold recommendation derived from the supplied params (delegate-driven)."""

    no_change_needed: bool
    recommended_max_nans_per_trait: Optional[float]
    would_remove_traits: list[str]
    samples_lost_at_recommendation: int
    samples_lost_at_current_params: int
    naive_dropna_samples_lost: int
    rationale: str


class QCInspectResult(BaseModel):
    """A small inline summary + recommendation + links to the persisted report run."""

    experiment: str
    source: str
    n_samples: int
    n_traits: int
    per_trait_nan_fraction: dict[str, float]
    traits_exceeding_thresholds: list[str]
    traits_would_be_removed: list[str]
    samples_lost_at_current_params: int
    residual_nan_cells_at_current_params: int
    recommendation: QCInspectRecommendation
    run_ref: str
    version_dir: str
    manifest_path: str
    outputs: dict[str, str]


def _filter(df, trait_cols, params, role_kwargs, *, max_nans_per_trait):
    """Run the analyze cleanup filter at the given NaN-per-trait threshold."""
    return apply_data_cleanup_filters(
        df,
        trait_cols,
        max_zeros_per_trait=params.max_zeros_per_trait,
        max_nans_per_trait=max_nans_per_trait,
        max_nans_per_sample=params.max_nans_per_sample,
        min_samples_per_trait=params.min_samples_per_trait,
        **role_kwargs,
    )


def _removed_traits(log: dict) -> list[str]:
    return [t["trait"] for t in log.get("removed_traits", []) if isinstance(t, dict)]


def _build_recommendation(
    df,
    trait_cols,
    params,
    role_kwargs,
    nan_frac,
    current_log,
    naive_dropna_lost: int,
) -> QCInspectRecommendation:
    """Recommend a ``max_nans_per_trait`` that drops NaN-bearing traits to cut sample loss.

    Delegate-driven: the consequence of the recommended threshold is measured by
    re-running ``apply_data_cleanup_filters`` at that threshold — no filtering logic
    is re-implemented here.
    """
    samples_lost_current = int(
        current_log.get("original_samples", len(df))
        - current_log.get("final_samples", len(df))
    )
    removed_now = set(_removed_traits(current_log))
    # Traits the current params KEEP but that still carry NaN — these are what force
    # the sample loss (or leave residual NaN at a looser max_nans_per_sample).
    offending = {
        t: float(nan_frac[t])
        for t in trait_cols
        if t not in removed_now and nan_frac[t] > 0
    }

    if not offending:
        return QCInspectRecommendation(
            no_change_needed=True,
            recommended_max_nans_per_trait=None,
            would_remove_traits=[],
            samples_lost_at_recommendation=samples_lost_current,
            samples_lost_at_current_params=samples_lost_current,
            naive_dropna_samples_lost=naive_dropna_lost,
            rationale=(
                "No NaN-bearing trait survives the current thresholds, so the current "
                "settings lose no samples to missingness — no change recommended."
            ),
        )

    min_frac = min(offending.values())
    rec = (
        math.floor(min_frac * 100) / 100
    )  # largest 0.01 step strictly below the fraction
    if rec >= min_frac:
        rec = round(min_frac - 0.01, 4)
    rec = max(rec, 0.0)

    _, rec_log = _filter(df, trait_cols, params, role_kwargs, max_nans_per_trait=rec)
    would_remove = _removed_traits(rec_log)
    samples_lost_rec = int(
        rec_log.get("original_samples", len(df)) - rec_log.get("final_samples", len(df))
    )
    return QCInspectRecommendation(
        no_change_needed=False,
        recommended_max_nans_per_trait=rec,
        would_remove_traits=would_remove,
        samples_lost_at_recommendation=samples_lost_rec,
        samples_lost_at_current_params=samples_lost_current,
        naive_dropna_samples_lost=naive_dropna_lost,
        rationale=(
            f"At the current max_nans_per_trait={params.max_nans_per_trait}, the "
            f"NaN-heavy trait(s) {sorted(offending)} are kept and {samples_lost_current} "
            f"sample(s) are lost. Lowering max_nans_per_trait to {rec} drops "
            f"{would_remove or 'them'} instead, leaving {samples_lost_rec} sample(s) lost."
        ),
    )


def _render_report(df, trait_cols, params, current_log, role_kwargs, staging_dir):
    """Render + persist the delegated EDA figures and the NaN-samples table.

    Returns the ``{logical_name: relative_path}`` map for the figures/CSV. All
    matplotlib figures the delegates create are closed before returning (no handle
    leak in a long-lived server process).
    """
    outputs: dict[str, str] = {}

    # 1. Per-trait NaN/zero/outlier overlay charts + the traits-actually-removed panel.
    eda_figs = create_trait_eda_plots(
        df,
        trait_cols,
        thresholds={
            "nan": params.max_nans_per_trait,
            "zero": params.max_zeros_per_trait,
        },
        cleanup_log=current_log,
        min_samples_per_trait=params.min_samples_per_trait,
    )
    try:
        for name, fig in eda_figs.items():
            fname = f"{name}.png"
            fig.savefig(staging_dir / fname, dpi=120, bbox_inches="tight")
            outputs[fname] = fname
    finally:
        for fig in eda_figs.values():
            plt.close(fig)

    # 2. The sample x trait missingness heatmap (best-effort — the secondary panels of
    #    create_exploratory_summary_plots can be fragile on tiny/degenerate frames; the
    #    overview + recommendation are the load-bearing outputs).
    try:
        summary_figs = create_exploratory_summary_plots(
            df, trait_cols, genotype_col=role_kwargs.get("genotype_col", "geno")
        )
    except Exception:
        summary_figs = {}
    try:
        heatmap = summary_figs.get("missing_data_pattern")
        if heatmap is not None:
            heatmap.savefig(staging_dir / _HEATMAP_PNG, dpi=120, bbox_inches="tight")
            outputs[_HEATMAP_PNG] = _HEATMAP_PNG
    finally:
        for fig in summary_figs.values():
            plt.close(fig)

    # 3. Per-sample NaN report (which samples, which traits, nan_fraction).
    nan_samples = inspect_nan_samples(df, trait_cols, verbose=False, **role_kwargs)
    nan_samples.to_csv(staging_dir / _NAN_SAMPLES_CSV, index=False)
    outputs[_NAN_SAMPLES_CSV] = _NAN_SAMPLES_CSV

    return outputs


@as_mcp_tool(
    input_model=QCInspectParams,
    output_model=QCInspectResult,
    errors=(ExperimentReadError,),
)
def qc_inspect(params: QCInspectParams, *, provenance: Provenance) -> QCInspectResult:
    """Inspect raw ``experiment`` missingness and recommend a cleanup threshold."""
    reader = _ports.reader()
    store = _ports.store()

    # Read the RAW frame — qc_inspect inspects the raw missingness (no require_clean).
    frame = reader.load_experiment(params.experiment)
    if params.trait_columns is not None:
        _validate_trait_subset(frame, params.trait_columns, params.experiment)
    trait_cols = list(params.trait_columns or frame.trait_cols)
    role_kwargs = _role_kwargs(frame)

    nan_frac = frame.df[trait_cols].isna().mean()
    per_trait_nan = {c: round(float(nan_frac[c]), 4) for c in trait_cols}
    traits_exceeding = [
        c for c in trait_cols if nan_frac[c] > params.max_nans_per_trait
    ]
    naive_dropna_lost = int(len(frame.df) - len(frame.df.dropna(subset=trait_cols)))

    # One cleanup-filter call at the supplied params drives both the overlay's
    # "traits actually removed" panel and the recommendation.
    cleaned_current, current_log = _filter(
        frame.df,
        trait_cols,
        params,
        role_kwargs,
        max_nans_per_trait=params.max_nans_per_trait,
    )
    removed_now = _removed_traits(current_log)
    kept_now = [c for c in trait_cols if c not in set(removed_now)]
    residual_now = int(cleaned_current[kept_now].isna().sum().sum()) if kept_now else 0
    samples_lost_now = int(
        current_log.get("original_samples", len(frame.df))
        - current_log.get("final_samples", len(frame.df))
    )

    recommendation = _build_recommendation(
        frame.df,
        trait_cols,
        params,
        role_kwargs,
        nan_frac,
        current_log,
        naive_dropna_lost,
    )

    # Persist a versioned REPORT run under tool class `qc_inspect` (never `qc`, never
    # CLEANED_CSV_NAME) so the reader cannot resolve it as a cleaned version.
    local_src = TRAITS_DIR / params.experiment
    run = store.create_run(
        experiment=params.experiment,
        tool_class=_TOOL_CLASS,
        provenance=provenance,
        user_label=params.user_label,
        source_csv=local_src if local_src.exists() else None,
    )
    outputs = _render_report(
        frame.df, trait_cols, params, current_log, role_kwargs, run.staging_dir
    )
    (run.staging_dir / _RECOMMENDATION_JSON).write_text(
        json.dumps(convert_to_json_serializable(recommendation.model_dump()), indent=2)
    )
    outputs[_RECOMMENDATION_JSON] = _RECOMMENDATION_JSON

    stored = store.commit(run, outputs)

    return QCInspectResult(
        experiment=params.experiment,
        source=frame.source,
        n_samples=len(frame.df),
        n_traits=len(trait_cols),
        per_trait_nan_fraction=per_trait_nan,
        traits_exceeding_thresholds=traits_exceeding,
        traits_would_be_removed=removed_now,
        samples_lost_at_current_params=samples_lost_now,
        residual_nan_cells_at_current_params=residual_now,
        recommendation=recommendation,
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
    )


def register(mcp):
    """Register qc_inspect with the MCP server."""
    return _contract_register(mcp, qc_inspect)
