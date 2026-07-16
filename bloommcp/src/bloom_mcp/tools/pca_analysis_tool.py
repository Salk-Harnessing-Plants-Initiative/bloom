"""pca_analysis — PCA on a cleaned experiment, delegating to sleap-roots-analyze.

The first granular **consumer** (Tier 4 / #308): it reads a *cleaned* experiment
through the :class:`ExperimentReader` port with ``require_clean=True`` and delegates
**all** PCA to ``sleap_roots_analyze.perform_pca_analysis``, wrapping the result into
the upstream typed :class:`PCAResult` via ``PCAResult.from_pca_dict``. The MCP owns no
PCA math — no standardization, eigendecomposition, component selection, or loadings
computation of its own, and it never touches the vendored ``bloom_mcp.pca``.

**Consume, don't re-clean.** ``perform_pca_analysis`` silently ``dropna()``s, so running
it on raw data is uncontrolled sample loss. Requiring a cleaned version is necessary but
not sufficient: the reader's cleaned frame guarantees no-NaN only in its *surviving* trait
columns (``frame.trait_cols``). So the tool restricts the selection to that certified set —
a requested column outside it (or one that still carries a non-finite value) is rejected
with ``invalid_input`` / ``assumption_violated`` rather than silently row-dropped — making
the delegate's internal ``dropna()`` a genuine no-op over the sample set ``qc_clean``
certified.

**Deterministic in this tool's regime.** PCA here fits via sklearn's ``svd_solver="auto"``,
which selects the deterministic ``covariance_eigh`` path for the tabular-phenotyping regime
this tool serves (few trait columns, many samples). There ``random_state`` is inert, so the
tool declares no ``random_state`` and records ``seed = None`` (matching ``qc_clean``). The
delegate still hard-codes an internal seed, so a fit that fell into sklearn's randomized
path (a very wide selection — see the design doc's Risks) would remain *reproducible*, but
``seed = None`` would then under-describe it; that boundary is documented rather than
silently assumed. It persists a versioned run under tool class ``pca`` — the loadings +
component scores as CSVs and the serialized ``PCAResult`` — recording ``based_on_version`` =
the consumed cleaned version and content-addressing the consumed frame via ``source_csv`` so
the ``qc_clean`` → ``pca_analysis`` lineage is recoverable, and returns a variance summary +
links (never the score/loadings matrices inline).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sleap_roots_analyze import PCAResult, perform_pca_analysis

from bloom_mcp.contract import BloomMCPError, Provenance, RunLinks, as_mcp_tool
from bloom_mcp.contract import register as _contract_register
from bloom_mcp.data_access import (
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentReadError,
)
from bloom_mcp.tools import _ports
from bloom_mcp.tools._consumer_utils import _build_output_frame, snapshot_frame
from bloom_mcp.tools._qc_shared import _validate_trait_subset

_TOOL_CLASS = "pca"
_LOADINGS_NAME = "loadings.csv"
_SCORES_NAME = "scores.csv"
_RESULT_NAME = "pca_result.json"

# Valid plot keys — four upstream plotters callable from PCA outputs.
# create_variance_decomposition_plot excluded: requires a heritability pipeline
# frame not derivable from PCA outputs (see design.md).
_PCA_CATALOG_KEYS: frozenset[str] = frozenset(
    {
        "create_pca_scree_plot",
        "create_pca_biplot",
        "create_feature_contribution_plot",
        "create_feature_contribution_heatmap",
    }
)


class PCAAnalysisParams(BaseModel):
    """Inputs for ``pca_analysis``. No ``seed`` — PCA here is deterministic."""

    experiment: str = Field(
        ...,
        description="Experiment (CSV filename) to analyze. Must have a cleaned version "
        "produced by qc_clean; pca_analysis consumes it (require_clean).",
    )
    trait_columns: list[str] | None = Field(
        default=None,
        description="Subset of cleaned trait columns to analyze; omit to use all "
        "certified-clean traits. Each must be a cleaned trait column of the experiment. "
        "Pass at least one column with no duplicates (an empty list is rejected).",
    )
    standardize: bool = Field(
        default=True,
        description="Z-score each trait before PCA (matches the recorded golden).",
    )
    explained_variance_threshold: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description="Cumulative-variance threshold for automatic component selection; "
        "used only when n_components is omitted.",
    )
    n_components: int | None = Field(
        default=None,
        ge=1,
        description="Fixed number of components; overrides the variance threshold. "
        "Clamped to the number of selected features (never raises if larger).",
    )
    include_plots: bool = Field(
        default=False,
        description="If true, generate and persist PCA plots as run artifacts. "
        "Returned as additional entries in outputs (object-key links). "
        "When false (default), behavior is identical to pre-plots behavior.",
    )
    plots: Optional[list[str]] = Field(
        default=None,
        description="Subset of plot keys to generate; omit (None) to generate all four "
        "available plots when include_plots=True. Ignored when include_plots=False. "
        "Valid keys: create_pca_scree_plot, create_pca_biplot, "
        "create_feature_contribution_plot, create_feature_contribution_heatmap.",
    )
    user_label: str | None = Field(
        default=None,
        description="Optional slug appended to the version directory name.",
    )


class PCAAnalysisResult(RunLinks):
    """A small variance summary + links to the persisted PCA run (no matrices inline)."""

    experiment: str
    source: str
    n_samples: int
    n_features: int
    n_components: int
    feature_names: list[str]
    explained_variance_ratio: list[float]
    cumulative_variance_ratio: list[float]
    eigenvalues: list[float]


def _pca_plot_calls(
    result_dict: dict,
    pca: PCAResult,
    frame: ExperimentFrame,
    threshold: float,
) -> dict:
    """Return zero-arg callables for each catalog plot key, lazily importing plotters.

    Plotters are imported here (not at module level) so that importing this
    module never pulls in matplotlib — the Tier-0 import-clean guarantee is
    maintained on the default no-plots path.
    """
    from sleap_roots_analyze import (
        create_feature_contribution_heatmap,
        create_feature_contribution_plot,
        create_pca_biplot,
        create_pca_scree_plot,
    )

    return {
        "create_pca_scree_plot": lambda: create_pca_scree_plot(
            result_dict, variance_threshold=threshold
        ),
        # color_by=None: passing the genotype column name causes matplotlib to
        # interpret string genotype values as colors, which raises ValueError.
        # Blue-point biplot is still a valid scientific visualization.
        "create_pca_biplot": lambda: create_pca_biplot(
            result_dict,
            df=frame.df,
            trait_names=list(pca.feature_names),
            color_by=None,
        ),
        "create_feature_contribution_plot": lambda: create_feature_contribution_plot(
            result_dict,
            trait_names=list(pca.feature_names),
            n_components=pca.n_components,
            variance_threshold=threshold,
        ),
        # plot_type='loadings' forces a single Figure return (default 'both' → 2-tuple).
        "create_feature_contribution_heatmap": lambda: create_feature_contribution_heatmap(
            result_dict,
            n_components=pca.n_components,
            n_features=len(pca.feature_names),
            plot_type="loadings",
        ),
    }


def _loadings_frame(pca: PCAResult) -> pd.DataFrame:
    """Component loadings as features (rows) × components (columns)."""
    cols = [f"PC{i + 1}" for i in range(pca.n_components)]
    return pd.DataFrame(pca.loadings, index=pca.feature_names, columns=cols)


@as_mcp_tool(
    input_model=PCAAnalysisParams,
    output_model=PCAAnalysisResult,
    errors=(ExperimentReadError,),
)
def pca_analysis(
    params: PCAAnalysisParams, *, provenance: Provenance
) -> PCAAnalysisResult:
    """Run PCA on a cleaned ``experiment`` via ``perform_pca_analysis`` and persist it."""
    reader = _ports.reader()
    store = _ports.store()

    # Consumer: require a cleaned version. A missing one is a precondition failure with a
    # concrete remedy — caught here so it carries "run qc_clean first" rather than the
    # contract's generic tool_error message for the declared read error.
    try:
        frame = reader.load_experiment(params.experiment, require_clean=True)
    except CleanedVersionRequiredError:
        raise BloomMCPError(
            code="tool_error",
            message=(
                f"No cleaned version of {params.experiment!r} exists; pca_analysis "
                f"requires a cleaned input."
            ),
            remedy=f"Run qc_clean on {params.experiment!r} first, then retry pca_analysis.",
        ) from None

    if params.trait_columns is None:
        trait_cols = list(frame.trait_cols)
    else:
        _validate_trait_subset(
            frame, params.trait_columns, params.experiment, require_certified=True
        )
        trait_cols = list(params.trait_columns)
    selected = frame.df[trait_cols]

    # Defense-in-depth: the certified-clean set must be finite, so the delegate's internal
    # dropna() never silently loses a certified sample. isna() alone misses ±inf (which
    # dropna() also keeps), so it would poison standardization/eigendecomposition — check
    # full finiteness. A mis-reporting reader is the only way this fires.
    if not np.isfinite(selected.to_numpy(dtype=float)).all():
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                "The cleaned experiment carries non-finite values (NaN or ±inf) in its "
                "certified trait columns."
            ),
            remedy="Re-run qc_clean to produce a finite-valued cleaned version, then retry.",
        )

    # Delegate ALL PCA. The delegate *raises* ValueError on degenerate input (< 2 samples,
    # empty, no non-constant trait) — map it to a self-correctable error rather than letting
    # it fall through to the contract's opaque internal_error.
    try:
        result_dict = perform_pca_analysis(
            selected,
            standardize=params.standardize,
            explained_variance_threshold=params.explained_variance_threshold,
            n_components=params.n_components,
        )
    except ValueError:
        # The delegate's ValueError reasons collapse to one remedy; give a fixed, actionable
        # message rather than echoing the raw exception text into the user-facing envelope
        # (it may carry backend internals — see the no-leak test).
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                "PCA could not fit the selected traits — the cleaned selection is degenerate "
                "(empty, fewer than two samples, or no trait with non-zero variance)."
            ),
            remedy=(
                "Select a broader set of numeric trait columns (at least two samples and "
                "a non-constant trait), then retry."
            ),
        ) from None

    # Stamp the threshold that produced the fit so the serialized PCAResult self-describes
    # its selection rule (random_state stays None — consistent with seed=None; the delegate's
    # deterministic path does not consume one).
    # result_dict is retained in scope — all four plot-path plotters take the raw dict,
    # not the PCAResult instance (see design.md § "result_dict retained in scope").
    pca = PCAResult.from_pca_dict(
        result_dict,
        explained_variance_threshold=params.explained_variance_threshold,
    )

    # The delegate silently drops zero-variance (constant) columns before fitting, so
    # feature_names can be shorter than the requested set — which would make the reported
    # n_features disagree with the persisted (n-1)-row loadings. Surface that rather than
    # emit an internally inconsistent artifact. (Duplicate names are already rejected, so a
    # shrink here means a constant certified trait, not a re-selected duplicate.)
    fitted = set(pca.feature_names)
    dropped = [c for c in trait_cols if str(c) not in fitted]
    if dropped:
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                "PCA dropped constant (zero-variance) trait column(s) from the certified "
                f"selection: {dropped}. Fitting the remainder would report a feature count "
                "that disagrees with the persisted loadings."
            ),
            remedy=(
                "Exclude the constant column(s) from trait_columns (or re-run qc_clean to "
                "drop them), then retry."
            ),
        )

    # Optional plots — validate keys and generate figures BEFORE create_run so an unknown
    # key fails as invalid_input with no run committed. The try/finally wraps the whole
    # persistence region (including the tempdir) so figures are always closed even when
    # the tempdir entry or store operations fail (see design.md § "Figure/tempdir nesting").
    prov = provenance.model_copy(update={"based_on_version": frame.source})
    scores_df = pd.DataFrame(
        pca.scores, columns=[f"PC{i + 1}" for i in range(pca.n_components)]
    )
    with snapshot_frame(frame.df) as source_snapshot:
        run = store.create_run(
            experiment=params.experiment,
            tool_class=_TOOL_CLASS,
            provenance=prov,
            user_label=params.user_label,
            source_csv=source_snapshot,
        )
        _loadings_frame(pca).to_csv(run.staging_dir / _LOADINGS_NAME, index=True)
        _build_output_frame(frame, scores_df).to_csv(
            run.staging_dir / _SCORES_NAME, index=False
        )
        (run.staging_dir / _RESULT_NAME).write_text(pca.to_json())
        stored = store.commit(
            run,
            {
                _LOADINGS_NAME: _LOADINGS_NAME,
                _SCORES_NAME: _SCORES_NAME,
                _RESULT_NAME: _RESULT_NAME,
            }
            for name, fig in figures.items():
                rel = f"{name}.png"
                fig.savefig(run.staging_dir / rel, bbox_inches="tight")
                outputs[rel] = rel
            stored = store.commit(run, outputs)
    finally:
        close_figures(figures)

    return PCAAnalysisResult(
        experiment=params.experiment,
        source=frame.source,
        n_samples=len(selected),
        n_features=len(pca.feature_names),
        n_components=pca.n_components,
        feature_names=list(pca.feature_names),
        explained_variance_ratio=list(pca.explained_variance_ratio),
        cumulative_variance_ratio=list(pca.cumulative_variance_ratio),
        eigenvalues=list(pca.eigenvalues),
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
    )


def register(mcp):
    """Register pca_analysis with the MCP server."""
    return _contract_register(mcp, pca_analysis)
