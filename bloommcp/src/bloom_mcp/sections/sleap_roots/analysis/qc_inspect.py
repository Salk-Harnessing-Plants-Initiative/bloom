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
per-sample NaN table. The MCP contains **no** EDA/plotting logic of its own — every symbol
above resolves to ``sleap_roots_analyze``, never a vendored copy (the former vendored
``bloom_mcp.data_cleanup`` was deleted by ``devendor-bloommcp-analysis``).

It persists a versioned **report** run via the :class:`ResultStore` port under tool
class ``qc_inspect`` — deliberately **not** ``qc`` — so the reader never resolves it as
a cleaned version: ``qc_inspect`` is read-only and produces no cleaned table. The result
returns a small inline summary + a structured **recommendation** (which traits to drop
and the sample loss avoided) + links to the persisted figures / CSV / recommendation
JSON — never inline blobs.

Caveat: missingness is measured with ``pandas.isna`` (matching the delegate), so ``inf`` /
``-inf`` are **not** counted as missing — an all-``inf`` trait reports ``nan_fraction=0.0`` and
is kept, which would otherwise pass silently into a downstream PCA. Because that is exactly the
silent-bias class this tool exists to surface, the result reports ``per_trait_inf_count`` and
prepends a warning to the recommendation rationale whenever a trait carries non-finite values.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from shutil import rmtree
from typing import Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

import matplotlib

# Headless: pin Agg before importing the analyze viz funcs below. NOTE: the analyze
# delegates render on matplotlib's *global* pyplot state (`plt.subplots`), so figure
# handling here (and the no-leak test's global `get_fignums()` baseline) assumes a
# single in-flight render — i.e. one bloom-mcp writer at a time. Concurrent qc_inspect
# calls in one process share that global registry; that is the same single-writer
# assumption the versioned ResultStore already makes (see qc_clean).
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sleap_roots_analyze import (
    apply_data_cleanup_filters,
    create_exploratory_summary_plots,
    create_trait_eda_plots,
    inspect_nan_samples,
)

from bloom_mcp.contract import BloomMCPError, Provenance, as_mcp_tool
from bloom_mcp.data_access import ExperimentReadError
from sleap_roots_analyze.data_utils import convert_to_json_serializable
from bloom_mcp.tools import _ports

# Canonical thresholds + shared helpers are single-sourced in _qc_shared so qc_inspect's
# overlays/recommendation cannot silently desync from the clean qc_clean would apply.
from bloom_mcp.tools._qc_shared import (
    _CANONICAL_MAX_NANS_PER_SAMPLE,
    _CANONICAL_MAX_NANS_PER_TRAIT,
    _CANONICAL_MAX_ZEROS_PER_TRAIT,
    _CANONICAL_MIN_SAMPLES_PER_TRAIT,
    _role_kwargs,
    _validate_experiment_name,
    _validate_trait_subset,
)

logger = logging.getLogger(__name__)

_TOOL_CLASS = "qc_inspect"
_NAN_SAMPLES_CSV = "nan_samples.csv"
_RECOMMENDATION_JSON = "recommendation.json"
_HEATMAP_PNG = "missing_data_pattern.png"


class QCInspectParams(BaseModel):
    """Inputs for ``qc_inspect`` — the same threshold knobs as ``qc_clean`` (no ``seed``)."""

    experiment: str = Field(
        ..., description="Experiment identifier from list_available_experiments."
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
    """A threshold recommendation derived from the supplied params (delegate-driven).

    ``no_change_needed`` is ``True`` whenever lowering ``max_nans_per_trait`` would not
    reduce sample loss below the current settings — either because no NaN-bearing trait
    survives the current thresholds, or because the current per-sample threshold already
    tolerates the missingness so dropping the trait buys no samples. In that case
    ``recommended_max_nans_per_trait`` is ``None`` and ``would_remove_traits`` is empty.
    """

    no_change_needed: bool
    recommended_max_nans_per_trait: Optional[float]
    would_remove_traits: list[str]
    # The recommended threshold drops EVERY kept NaN-bearing trait at once (it is derived
    # from the smallest offending NaN fraction). This maps each such trait to the number
    # of samples that carry a NaN in it — its individual missingness footprint — so the
    # agent can weigh keeping a low-missingness trait instead of accepting the all-or-nothing
    # drop. Empty when no change is recommended.
    offending_trait_nan_counts: dict[str, int]
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
    # Non-finite (inf/-inf) counts per trait, restricted to traits that carry any (empty =
    # all finite). isna() treats inf as present, so an inf-heavy trait reports nan_fraction≈0
    # and would be kept — this surfaces that silent bias before it flows into a PCA.
    per_trait_inf_count: dict[str, int]
    # NaN-only view: traits whose NaN fraction alone exceeds max_nans_per_trait. This can
    # differ from ``traits_would_be_removed`` below, which is the delegate's FULL removal
    # set — it also drops traits for too-many-zeros or too-few-samples. ``removed_trait_reasons``
    # explains each removal so the two fields never look contradictory without cause.
    traits_exceeding_thresholds: list[str]
    traits_would_be_removed: list[str]
    removed_trait_reasons: dict[str, str]
    samples_lost_at_current_params: int
    residual_nan_cells_at_current_params: int
    recommendation: QCInspectRecommendation
    run_ref: str
    version_dir: str
    manifest_path: str
    outputs: dict[str, str]


def _filter(
    df: pd.DataFrame,
    trait_cols: list[str],
    params: "QCInspectParams",
    role_kwargs: dict[str, str],
    *,
    max_nans_per_trait: float,
) -> tuple[pd.DataFrame, dict]:
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


def _samples_lost(log: dict) -> int:
    """Samples the delegate dropped = ``original_samples - final_samples``.

    Requires both keys rather than falling back to ``len(df) - len(df) == 0``: a silent 0
    would flip the benefit gate to ``no_change_needed`` on every input if the upstream log
    ever renamed these keys. A missing key is delegate contract-drift, surfaced structurally.
    """
    try:
        return int(log["original_samples"] - log["final_samples"])
    except (KeyError, TypeError) as exc:
        raise BloomMCPError(
            code="internal_error",
            message="Cleanup log is missing expected sample-count keys.",
            remedy="Verify the pinned sleap-roots-analyze version; its cleanup-log shape changed.",
        ) from exc


def _removed_traits(log: dict) -> list[str]:
    return [t["trait"] for t in log.get("removed_traits", []) if isinstance(t, dict)]


def _removed_reasons(log: dict) -> dict[str, str]:
    """Map each delegate-removed trait to the delegate's removal reason.

    The delegate log tags every removed trait with why (``too_many_nans`` /
    ``too_many_zeros`` / ``too_few_samples``). Surfacing it explains why a trait can be in
    ``traits_would_be_removed`` yet absent from the NaN-only ``traits_exceeding_thresholds``.
    """
    return {
        t["trait"]: str(t.get("reason", "removed"))
        for t in log.get("removed_traits", [])
        if isinstance(t, dict)
    }


def _build_recommendation(
    df: pd.DataFrame,
    trait_cols: list[str],
    params: "QCInspectParams",
    role_kwargs: dict[str, str],
    nan_frac: "pd.Series",
    current_log: dict,
    naive_dropna_lost: int,
) -> QCInspectRecommendation:
    """Recommend a ``max_nans_per_trait`` that drops NaN-bearing traits to cut sample loss.

    Delegate-driven: the consequence of any threshold is measured by re-running
    ``apply_data_cleanup_filters`` at it — no filtering logic is re-implemented here. A
    change is recommended ONLY when it strictly reduces sample loss; if the current
    per-sample threshold already tolerates the missingness (so dropping the trait would
    save no samples), the honest answer is ``no_change_needed`` rather than advising a drop
    that buys nothing on the metric this tool optimizes.
    """
    samples_lost_current = _samples_lost(current_log)
    removed_now = set(_removed_traits(current_log))
    # Traits the current params KEEP but that still carry NaN — these are what force
    # the sample loss (or leave residual NaN at a looser max_nans_per_sample).
    offending = {
        t: float(nan_frac[t])
        for t in trait_cols
        if t not in removed_now and nan_frac[t] > 0
    }
    # Per-offending-trait missingness footprint: how many samples carry a NaN in each.
    # Lets the agent weigh keeping a low-footprint trait vs the all-or-nothing drop below.
    offending_counts = {t: int(df[t].isna().sum()) for t in offending}

    if not offending:
        return QCInspectRecommendation(
            no_change_needed=True,
            recommended_max_nans_per_trait=None,
            would_remove_traits=[],
            offending_trait_nan_counts={},
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
    samples_lost_rec = _samples_lost(rec_log)

    # Benefit gate: recommend a change only if it strictly reduces sample loss. When the
    # current max_nans_per_sample already tolerates the missingness, lowering
    # max_nans_per_trait drops the trait but frees no samples — so do not advise it.
    if samples_lost_rec >= samples_lost_current:
        return QCInspectRecommendation(
            no_change_needed=True,
            recommended_max_nans_per_trait=None,
            would_remove_traits=[],
            offending_trait_nan_counts=offending_counts,
            samples_lost_at_recommendation=samples_lost_current,
            samples_lost_at_current_params=samples_lost_current,
            naive_dropna_samples_lost=naive_dropna_lost,
            rationale=(
                f"The NaN-bearing trait(s) {sorted(offending)} are kept, but the current "
                f"max_nans_per_sample={params.max_nans_per_sample} already tolerates their "
                f"missingness — only {samples_lost_current} sample(s) are lost and lowering "
                f"max_nans_per_trait would not reduce that. No change recommended for sample "
                f"loss; tighten max_nans_per_sample if you instead want those NaN-bearing "
                f"samples (or traits) removed. See offending_trait_nan_counts for each trait's "
                f"missingness footprint."
            ),
        )

    return QCInspectRecommendation(
        no_change_needed=False,
        recommended_max_nans_per_trait=rec,
        would_remove_traits=would_remove,
        offending_trait_nan_counts=offending_counts,
        samples_lost_at_recommendation=samples_lost_rec,
        samples_lost_at_current_params=samples_lost_current,
        naive_dropna_samples_lost=naive_dropna_lost,
        rationale=(
            f"At the current max_nans_per_trait={params.max_nans_per_trait}, the "
            f"NaN-heavy trait(s) {sorted(offending)} are kept and {samples_lost_current} "
            f"sample(s) are lost. Lowering max_nans_per_trait to {rec} drops "
            f"{would_remove or 'them'} instead, leaving {samples_lost_rec} sample(s) lost. "
            f"The recommended threshold drops EVERY trait above it at once; weigh "
            f"offending_trait_nan_counts before keeping a low-missingness trait."
        ),
    )


def _render_report(
    df: pd.DataFrame,
    trait_cols: list[str],
    params: "QCInspectParams",
    current_log: dict,
    role_kwargs: dict[str, str],
    staging_dir: Path,
) -> dict[str, str]:
    """Render + persist the delegated EDA figures and the NaN-samples table.

    Returns the ``{logical_name: relative_path}`` map for the figures/CSV. All
    matplotlib figures the delegates create are closed before returning (no handle
    leak in a long-lived server process). The missingness heatmap is best-effort: on
    a degenerate frame it may be absent from the output set (logged, never raised), so
    consumers must treat ``missing_data_pattern.png`` as optional.
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
    except Exception as exc:
        logger.warning(
            "qc_inspect: missingness heatmap unavailable (create_exploratory_summary_plots "
            "failed on this frame: %s); the report omits %s.",
            exc,
            _HEATMAP_PNG,
        )
        summary_figs = {}
    try:
        heatmap = summary_figs.get("missing_data_pattern")
        if heatmap is not None:
            heatmap.savefig(staging_dir / _HEATMAP_PNG, dpi=120, bbox_inches="tight")
            outputs[_HEATMAP_PNG] = _HEATMAP_PNG
        else:
            logger.warning(
                "qc_inspect: missingness heatmap not produced for this frame; "
                "the report omits %s.",
                _HEATMAP_PNG,
            )
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

    # Bare-filename guard before any read — experiment flows into the reader's input root.
    _validate_experiment_name(params.experiment)

    # Read the RAW frame — qc_inspect inspects the raw missingness (no require_clean).
    # version="raw" explicitly: the default "latest" would resolve a cleaned (or,
    # post-#420, a trimmed) version once one exists for this experiment, which
    # defeats the whole point of this tool (picking qc_clean's thresholds BEFORE
    # cleaning) — inspecting already-cleaned data to choose its own cleaning
    # thresholds is circular. This was a latent bug even before #420 (order-
    # dependent on whether qc_clean had ever run); #420's own outliers-preferring
    # resolution makes it worse (deterministic once any trim exists, not merely
    # order-dependent), which is what surfaced it during that PR's review.
    frame = reader.load_experiment(params.experiment, version="raw")
    if params.trait_columns is not None:
        # An empty list is a caller mistake, not "inspect everything" — reject it
        # explicitly rather than silently falling through to all traits.
        if not params.trait_columns:
            raise BloomMCPError(
                code="invalid_input",
                message="trait_columns was an empty list.",
                remedy="Omit trait_columns to inspect all detected traits, or name at least one trait column.",
            )
        _validate_trait_subset(frame, params.trait_columns, params.experiment)
    trait_cols = list(params.trait_columns or frame.trait_cols)
    if not trait_cols:
        # No caller subset and the adapter detected no numeric traits (metadata-only frame,
        # or duplicate columns collapsing) — there is nothing to inspect; don't commit an
        # empty report run.
        raise BloomMCPError(
            code="invalid_input",
            message=f"No numeric trait columns detected in {params.experiment!r}.",
            remedy="Check the experiment has numeric trait columns, or pass trait_columns explicitly.",
        )
    role_kwargs = _role_kwargs(frame)

    nan_frac = frame.df[trait_cols].isna().mean()
    per_trait_nan = {c: round(float(nan_frac[c]), 4) for c in trait_cols}
    # Non-finite (inf/-inf) footprint — isna() ignores inf, so this surfaces the silent bias
    # of an inf-heavy trait reading as ~0% missing (restricted to traits that carry any).
    inf_by_trait = np.isinf(
        frame.df[trait_cols].to_numpy(dtype="float64", na_value=np.nan)
    )
    per_trait_inf = {
        c: int(n) for c, n in zip(trait_cols, inf_by_trait.sum(axis=0)) if n
    }
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
    removed_reasons = _removed_reasons(current_log)
    kept_now = [c for c in trait_cols if c not in set(removed_now)]
    residual_now = int(cleaned_current[kept_now].isna().sum().sum()) if kept_now else 0
    samples_lost_now = _samples_lost(current_log)

    recommendation = _build_recommendation(
        frame.df,
        trait_cols,
        params,
        role_kwargs,
        nan_frac,
        current_log,
        naive_dropna_lost,
    )
    # Non-finite values defeat the NaN-based recommendation (they read as ~0% missing and are
    # kept); warn loudly and stamp the caveat into the rationale the agent will act on.
    if per_trait_inf:
        logger.warning(
            "qc_inspect: %s trait(s) carry non-finite (inf) values not counted as missing: %s",
            len(per_trait_inf),
            per_trait_inf,
        )
        recommendation.rationale = (
            f"⚠️ Non-finite (inf) values present in {sorted(per_trait_inf)} "
            f"(counts {per_trait_inf}); these are NOT counted as missing, so the "
            f"missingness recommendation below understates their data-quality risk. "
        ) + recommendation.rationale

    # Persist a versioned REPORT run under tool class `qc_inspect` (never `qc`, never
    # CLEANED_CSV_NAME) so the reader cannot resolve it as a cleaned version.
    # Source-CSV provenance goes through the active reader (mirrors _ports.start_run)
    # so a custom BLOOM_EXPERIMENT_LOCAL_ROOT / BLOOM_LOCAL_ROOT input root is
    # honoured rather than a hard-coded TRAITS_DIR, which is unused/empty under
    # fully-local mode's BLOOM_LOCAL_ROOT-only configuration (#479).
    run = store.create_run(
        experiment=params.experiment,
        tool_class=_TOOL_CLASS,
        provenance=provenance,
        user_label=params.user_label,
        source_csv=_ports.raw_source_for(params.experiment),
        source=frame.resolved_source,
    )
    # Render + persist under the run's staging dir; on any partial failure remove the
    # staging dir so a long-lived server does not leak a half-written temp run.
    try:
        outputs = _render_report(
            frame.df, trait_cols, params, current_log, role_kwargs, run.staging_dir
        )
        (run.staging_dir / _RECOMMENDATION_JSON).write_text(
            json.dumps(
                convert_to_json_serializable(recommendation.model_dump()), indent=2
            )
        )
        outputs[_RECOMMENDATION_JSON] = _RECOMMENDATION_JSON
        stored = store.commit(run, outputs)
    except Exception:
        rmtree(run.staging_dir, ignore_errors=True)
        raise

    return QCInspectResult(
        experiment=params.experiment,
        source=frame.source,
        n_samples=len(frame.df),
        n_traits=len(trait_cols),
        per_trait_nan_fraction=per_trait_nan,
        per_trait_inf_count=per_trait_inf,
        traits_exceeding_thresholds=traits_exceeding,
        traits_would_be_removed=removed_now,
        removed_trait_reasons=removed_reasons,
        samples_lost_at_current_params=samples_lost_now,
        residual_nan_cells_at_current_params=residual_now,
        recommendation=recommendation,
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
    )
