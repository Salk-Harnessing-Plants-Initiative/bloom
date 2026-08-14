"""plot_correlation_matrix — correlation heatmap for trait relationships (#466).

Converged onto the ``@as_mcp_tool`` contract every other tool in this folder uses (Pydantic
I/O, structured ``BloomMCPError``, one stamped ``Provenance``, versioned ``ResultStore``
persistence) — the same read-only, pre-clean EDA pattern as ``qc_inspect``: reads the raw
frame via the :class:`ExperimentReader` port (no ``require_clean``), since a correlation view
is exactly what an agent uses *before* deciding ``qc_clean``'s thresholds.

Delegates rendering to ``sleap_roots_analyze.visualization.create_correlation_heatmap``; this
file owns no plotting logic of its own. The reported strong-correlation counts are a plain
``pandas`` summary of the same selection, computed directly here (not delegated) — unchanged
from the tool's pre-conversion behavior.

Persists a versioned run under its own tool class ``correlation_matrix`` (not the shared,
unclaimed legacy ``viz`` slot — see ``openspec/changes/converge-bloommcp-viz-tools/design.md``
for why each converged tool mints its own class rather than interleaving version history with
its siblings).
"""

from __future__ import annotations

from shutil import rmtree
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pydantic import BaseModel, Field
from sleap_roots_analyze.visualization import create_correlation_heatmap

from bloom_mcp.contract import BloomMCPError, Provenance, RunLinks, as_mcp_tool
from bloom_mcp.data_access import ExperimentReadError
from bloom_mcp.tools import _ports
from bloom_mcp.tools._qc_shared import _validate_experiment_name, _validate_trait_subset

_TOOL_CLASS = "correlation_matrix"
_HEATMAP_PNG = "correlation_matrix.png"


class PlotCorrelationMatrixParams(BaseModel):
    """Inputs for ``plot_correlation_matrix``. No ``seed`` — rendering is deterministic."""

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
    n_traits: int
    strong_positive_correlations: int = Field(
        description="Off-diagonal trait pairs with Pearson correlation > 0.7."
    )
    strong_negative_correlations: int = Field(
        description="Off-diagonal trait pairs with Pearson correlation < -0.7."
    )


@as_mcp_tool(
    input_model=PlotCorrelationMatrixParams,
    output_model=PlotCorrelationMatrixResult,
    errors=(ExperimentReadError,),
)
def plot_correlation_matrix(
    params: PlotCorrelationMatrixParams, *, provenance: Provenance
) -> PlotCorrelationMatrixResult:
    """Render a correlation heatmap for ``experiment`` via ``create_correlation_heatmap``
    and persist it."""
    reader = _ports.reader()
    store = _ports.store()

    _validate_experiment_name(params.experiment)

    frame = reader.load_experiment(params.experiment, version="raw")

    if params.trait_columns is not None:
        if not params.trait_columns:
            raise BloomMCPError(
                code="invalid_input",
                message=f"trait_columns for {params.experiment!r} was given as an empty list.",
                remedy="Omit trait_columns to correlate all detected traits, or name at least "
                "one trait column.",
            )
        _validate_trait_subset(frame, params.trait_columns, params.experiment)
    trait_cols = list(params.trait_columns or frame.trait_cols)
    if not trait_cols:
        raise BloomMCPError(
            code="invalid_input",
            message=f"No numeric trait columns detected in {params.experiment!r}.",
            remedy="Check the experiment has numeric trait columns, or pass trait_columns "
            "explicitly.",
        )

    corr = frame.df[trait_cols].corr()
    upper = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
    high_pos = int((upper > 0.7).sum().sum())
    high_neg = int((upper < -0.7).sum().sum())

    prov = provenance.model_copy(update={"based_on_version": frame.source})
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
        fig = create_correlation_heatmap(frame.df, trait_cols)
        fig.savefig(run.staging_dir / _HEATMAP_PNG, dpi=150, bbox_inches="tight")
        stored = store.commit(run, {_HEATMAP_PNG: _HEATMAP_PNG})
    except Exception:
        rmtree(run.staging_dir, ignore_errors=True)
        raise
    finally:
        if fig is not None:
            plt.close(fig)

    return PlotCorrelationMatrixResult(
        experiment=params.experiment,
        source=frame.source,
        n_traits=len(trait_cols),
        strong_positive_correlations=high_pos,
        strong_negative_correlations=high_neg,
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
        output_links=stored.output_links,
    )
