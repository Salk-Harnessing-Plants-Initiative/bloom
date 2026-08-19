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
"""

from __future__ import annotations

from shutil import rmtree
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pydantic import BaseModel, Field
from sleap_roots_analyze.visualization import (
    create_trait_boxplots_by_genotype,
    create_trait_boxplots_by_genotype_batched,
)

from bloom_mcp.contract import BloomMCPError, Provenance, RunLinks, as_mcp_tool
from bloom_mcp.data_access import ExperimentReadError
from bloom_mcp.result_store import CommitFailedError, ManifestReadError
from bloom_mcp.tools import _ports
from bloom_mcp.tools._qc_shared import _validate_experiment_name

from ._viz_shared import TRAIT_BATCH_THRESHOLD, resolve_trait_columns

_TOOL_CLASS = "trait_boxplots"
_PNG_STEM = "trait_boxplots"


class PlotTraitBoxplotsParams(BaseModel):
    """Inputs for ``plot_trait_boxplots``. No ``seed`` — rendering is deterministic."""

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


class PlotTraitBoxplotsResult(RunLinks):
    """A small summary + links to the persisted boxplot run."""

    experiment: str
    source: str
    genotype_column: str
    n_traits_plotted: int
    batched: bool = Field(
        description="True once the selection exceeds TRAIT_BATCH_THRESHOLD traits, in which "
        "case the render is paginated (see n_pages)."
    )
    n_pages: int = Field(
        description="Number of committed output pages (1 when not batched)."
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

    prov = provenance.model_copy(update={"based_on_version": frame.source})
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
        if batched:
            figures = list(
                create_trait_boxplots_by_genotype_batched(
                    frame.df, trait_cols, genotype_col=frame.genotype_col
                )
            )
        else:
            figures = [
                create_trait_boxplots_by_genotype(
                    frame.df, trait_cols, genotype_col=frame.genotype_col
                )
            ]

        outputs: dict[str, str] = {}
        for i, fig in enumerate(figures, start=1):
            name = f"{_PNG_STEM}.png" if not batched else f"{_PNG_STEM}_page{i}.png"
            fig.savefig(run.staging_dir / name, dpi=150, bbox_inches="tight")
            outputs[name] = name
        stored = store.commit(run, outputs)
    except Exception:
        rmtree(run.staging_dir, ignore_errors=True)
        raise
    finally:
        for fig in figures:
            plt.close(fig)

    return PlotTraitBoxplotsResult(
        experiment=params.experiment,
        source=frame.source,
        genotype_column=frame.genotype_col,
        n_traits_plotted=len(trait_cols),
        batched=batched,
        n_pages=len(figures),
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
        output_links=stored.output_links,
    )
