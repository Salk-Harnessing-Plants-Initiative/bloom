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
a requested column outside it (or one that still carries NaN) is rejected with
``invalid_input`` rather than silently row-dropped — making the delegate's internal
``dropna()`` a genuine no-op over the sample set ``qc_clean`` certified.

**Deterministic.** PCA here fits via sklearn's deterministic solver, so the tool declares
no ``random_state`` and records ``seed = None`` (matching ``qc_clean``). It persists a
versioned run under tool class ``pca`` — the loadings + component scores as CSVs and the
serialized ``PCAResult`` — recording ``based_on_version`` = the consumed cleaned version so
the ``qc_clean`` → ``pca_analysis`` lineage is recoverable, and returns a variance summary +
links (never the score/loadings matrices inline).
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field
from sleap_roots_analyze import PCAResult, perform_pca_analysis

from bloom_mcp.contract import BloomMCPError, Provenance, as_mcp_tool
from bloom_mcp.contract import register as _contract_register
from bloom_mcp.data_access import (
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentReadError,
)
from bloom_mcp.tools import _ports

_TOOL_CLASS = "pca"
_LOADINGS_NAME = "loadings.csv"
_SCORES_NAME = "scores.csv"
_RESULT_NAME = "pca_result.json"


class PCAAnalysisParams(BaseModel):
    """Inputs for ``pca_analysis``. No ``seed`` — PCA here is deterministic."""

    experiment: str = Field(
        ...,
        description="Experiment (CSV filename) to analyze. Must have a cleaned version "
        "produced by qc_clean; pca_analysis consumes it (require_clean).",
    )
    trait_columns: Optional[list[str]] = Field(
        default=None,
        description="Subset of cleaned trait columns to analyze; omit to use all "
        "certified-clean traits. Each must be a cleaned trait column of the experiment.",
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
    n_components: Optional[int] = Field(
        default=None,
        ge=1,
        description="Fixed number of components; overrides the variance threshold. "
        "Clamped to the number of selected features (never raises if larger).",
    )
    user_label: Optional[str] = Field(
        default=None,
        description="Optional slug appended to the version directory name.",
    )


class PCAAnalysisResult(BaseModel):
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
    run_ref: str
    version_dir: str
    manifest_path: str
    outputs: dict[str, str]


def _validate_trait_subset(
    frame: ExperimentFrame, requested: list[str], experiment: str
) -> None:
    """Require every selected column to be in the certified-clean trait set.

    ``require_clean`` guarantees no-NaN only within ``frame.trait_cols``; the frame may
    still carry other numeric columns holding NaNs. Restricting the selection to the
    certified set (not merely "exists + numeric" over the whole frame) is what forecloses
    the silent-``dropna()`` path — a NaN-bearing numeric column ``qc_clean`` did not adopt
    as a surviving trait cannot be selected. Unknown, metadata, and non-certified columns
    all surface here as a fixable ``invalid_input`` naming the offenders.
    """
    certified = set(frame.trait_cols)
    outside = [c for c in requested if c not in certified]
    if outside:
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"trait_columns includes columns that are not certified-clean traits of "
                f"{experiment!r}: {outside}."
            ),
            remedy=(
                "Pass only cleaned trait columns (see load_experiment_data on the cleaned "
                "version), or omit trait_columns to use all of them."
            ),
        )
    non_numeric = [
        c for c in requested if not pd.api.types.is_numeric_dtype(frame.df[c])
    ]
    if non_numeric:
        raise BloomMCPError(
            code="invalid_input",
            message=f"trait_columns includes non-numeric columns: {non_numeric}.",
            remedy="Pass only numeric trait columns; identifiers/metadata cannot be analyzed.",
        )


def _loadings_frame(pca: PCAResult) -> pd.DataFrame:
    """Component loadings as features (rows) × components (columns)."""
    cols = [f"PC{i + 1}" for i in range(pca.n_components)]
    return pd.DataFrame(pca.loadings, index=pca.feature_names, columns=cols)


def _scores_frame(pca: PCAResult) -> pd.DataFrame:
    """Sample scores as samples (rows) × components (columns)."""
    cols = [f"PC{i + 1}" for i in range(pca.n_components)]
    return pd.DataFrame(pca.scores, columns=cols)


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

    if params.trait_columns is not None:
        _validate_trait_subset(frame, params.trait_columns, params.experiment)
    trait_cols = (
        list(params.trait_columns) if params.trait_columns else list(frame.trait_cols)
    )
    selected = frame.df[trait_cols]

    # Defense-in-depth: the certified-clean set must be NaN-free, so the delegate's
    # internal dropna() never silently loses a certified sample. A mis-reporting reader
    # is the only way this fires.
    if int(selected.isna().sum().sum()) > 0:
        raise BloomMCPError(
            code="assumption_violated",
            message="The cleaned experiment carries NaNs in its certified trait columns.",
            remedy="Re-run qc_clean to produce a NaN-free cleaned version, then retry.",
        )

    # Delegate ALL PCA. The delegate *raises* ValueError on degenerate input (< 2 samples,
    # no non-constant trait) — map it to a self-correctable error rather than letting it
    # fall through to the contract's opaque internal_error.
    try:
        result_dict = perform_pca_analysis(
            selected,
            standardize=params.standardize,
            explained_variance_threshold=params.explained_variance_threshold,
            n_components=params.n_components,
        )
    except ValueError:
        # The delegate's ValueError reasons (< 2 samples / no non-constant trait) collapse
        # to one remedy; give a fixed, actionable message rather than echoing the raw
        # exception text into the user-facing envelope.
        raise BloomMCPError(
            code="assumption_violated",
            message="PCA could not fit the selected traits (too few samples, or no trait with non-zero variance).",
            remedy=(
                "Select a broader set of numeric trait columns (at least two samples and "
                "a non-constant trait), then retry."
            ),
        ) from None

    pca = PCAResult.from_pca_dict(result_dict)

    # Persist a versioned run, recording the cleaned-source lineage on the manifest so the
    # qc_clean run that produced this PCA's input is recoverable.
    prov = provenance.model_copy(update={"based_on_version": frame.source})
    run = store.create_run(
        experiment=params.experiment,
        tool_class=_TOOL_CLASS,
        provenance=prov,
        user_label=params.user_label,
    )
    _loadings_frame(pca).to_csv(run.staging_dir / _LOADINGS_NAME, index=True)
    _scores_frame(pca).to_csv(run.staging_dir / _SCORES_NAME, index=False)
    (run.staging_dir / _RESULT_NAME).write_text(pca.to_json())
    stored = store.commit(
        run,
        {
            _LOADINGS_NAME: _LOADINGS_NAME,
            _SCORES_NAME: _SCORES_NAME,
            _RESULT_NAME: _RESULT_NAME,
        },
    )

    return PCAAnalysisResult(
        experiment=params.experiment,
        source=frame.source,
        n_samples=len(selected),
        n_features=len(trait_cols),
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
