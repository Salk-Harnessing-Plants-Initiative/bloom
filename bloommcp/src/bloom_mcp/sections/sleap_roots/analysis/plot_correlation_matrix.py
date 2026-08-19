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

**Zero-variance / all-NaN traits are excluded from the strong-correlation counts with no
error** — ``pandas``' Pearson correlation is ``NaN`` for a constant or all-NaN column, and
``NaN > 0.7`` is ``False``, so such a trait's pairs silently don't count toward either
``strong_positive_correlations``/``strong_negative_correlations``. Realistic here specifically
because this tool reads **raw, uncleaned** data (no QC has dropped a zero-variance trait yet —
see the raw-read decision below). ``zero_variance_traits`` in the result names exactly which
selected traits this affects, so the counts are not silently misleading.

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

from bloom_mcp.contract import Provenance, RunLinks, as_mcp_tool
from bloom_mcp.data_access import ExperimentReadError
from bloom_mcp.result_store import CommitFailedError, ManifestReadError
from bloom_mcp.tools import _ports
from bloom_mcp.tools._qc_shared import _validate_experiment_name

from ._viz_shared import resolve_trait_columns

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
    zero_variance_traits: list[str] = Field(
        default_factory=list,
        description="Selected traits with zero variance (constant) or entirely NaN in the "
        "raw data. Pearson correlation against a zero-variance trait is NaN, which counts "
        "toward neither strong_positive_correlations nor strong_negative_correlations — "
        "empty when none were affected.",
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

    corr = frame.df[trait_cols].corr()
    zero_variance_traits = [
        c for c in trait_cols if not (frame.df[c].std(skipna=True) > 0)
    ]
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
        zero_variance_traits=zero_variance_traits,
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
        output_links=stored.output_links,
    )
