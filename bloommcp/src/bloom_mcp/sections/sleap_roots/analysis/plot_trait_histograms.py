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

**Unlike ``plot_correlation_matrix``, a zero-variance or all-NaN trait is NOT guarded or
disclosed here** — a constant trait renders as a degenerate (effectively single-bar) histogram
with no error and no flag in the result, and an all-NaN trait's delegate-rendered panel shows a
literal ``"No data"`` text (confirmed against the live delegate, not assumed) rather than being
silently blank — visible in the image, but still nowhere in the structured result. This is a
deliberate asymmetry, not an oversight: a histogram of a constant/all-NaN trait is a legitimate
(if uninformative) plot on its own — unlike
``plot_correlation_matrix``, where a constant trait poisons every correlation cell it
participates in, which is what that tool's guard/disclosure exists to catch. Tracked as a
follow-up disclosure gap at
https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/748 (#466 review).
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
from bloom_mcp.experiment_utils import load_experiment_data as _load_data
from bloom_mcp.tools._plots import call_with_figure_cleanup

from bloom_mcp.contract import Provenance, RunLinks, as_mcp_tool
from bloom_mcp.data_access import ExperimentReadError
from bloom_mcp.result_store import CommitFailedError, ManifestReadError
from bloom_mcp.tools import _ports
from bloom_mcp.tools._plots import FIGURE_REGISTRY_LOCK
from bloom_mcp.tools._qc_shared import _validate_experiment_name

from ._viz_shared import TRAIT_BATCH_THRESHOLD, resolve_trait_columns

_TOOL_CLASS = "trait_histograms"
_PNG_STEM = "trait_histograms"
# create_trait_histograms_batched's own internal page size — independent of
# TRAIT_BATCH_THRESHOLD (which only decides WHETHER to batch). Not overridden by this
# tool's call, so it is safe to use for computing which trait landed on which page;
# test_plot_trait_histograms_tool.py pins this against the live delegate signature so a
# future sleap-roots-analyze bump that changes it is caught, not silently desynced.
_DELEGATE_BATCH_SIZE = 16


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


class PlotTraitHistogramsResult(RunLinks):
    """A small summary + links to the persisted histogram run."""

    def _make_histograms():
        if len(selected) > TRAIT_BATCH_THRESHOLD:
            return create_trait_histograms_batched(df, selected)
        return create_trait_histograms(df, selected)

    try:
        # call_with_figure_cleanup: acquires the shared FIGURE_REGISTRY_LOCK around
        # this delegate call (#721 PR review) and closes any figure(s) it allocates
        # before raising, instead of leaking them — this file's own
        # `except Exception: return ...` below would otherwise swallow such an
        # exception without closing whatever was already rendered.
        fig_or_figs = call_with_figure_cleanup(_make_histograms)
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
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
        output_links=stored.output_links,
    )
