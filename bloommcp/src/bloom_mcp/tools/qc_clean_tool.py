"""qc_clean — turn a raw experiment trait table into a clean, analysis-ready one.

The first granular, contract-wrapped tool (Tier 3 / #338) and the QC foundation.
It delegates **all** cleanup-and-validate to
``sleap_roots_analyze.clean_traits_for_analysis`` (the minimal-QC entry point,
analyze#164): the MCP contains **no QC logic** — it does not run the full
``QCPipeline`` and does not re-stitch ``load → cleanup → validate`` (that
orchestration is analyze's, tested upstream), nor does it touch the vendored
``bloom_mcp.data_cleanup``.

On each call it reads the **raw** frame via the :class:`ExperimentReader` port
(qc_clean is the *producer* of cleaned data, so it never sets ``require_clean``),
calls the one upstream entry point with the adapter-detected role columns, then
persists a versioned run via the :class:`ResultStore` port — the cleaned CSV
(``CLEANED_CSV_NAME``) + the cleanup log + provenance — under tool class ``qc``.
That filename is what the reader resolves as a *cleaned version*, so a later
``pca_analysis`` (``require_clean=True``) consumes this run: the
qc_clean → pca_analysis composition. The result returns a small in/out summary +
links to the persisted artifacts (run ref, manifest, object keys) — never the
cleaned table inline.

**No-NaN guarantee.** Before persisting, the tool asserts the cleaned table has
no NaNs in its kept trait columns and at least one surviving sample/trait — the
contract ``pca_analysis(require_clean=True)`` relies on. A cleanup that would
leave residual NaNs, drop every trait, or drop every sample raises a structured
``BloomMCPError`` (with a relax-the-thresholds remedy) rather than committing a
degenerate or NaN-bearing "cleaned" run.

**Shared ``qc`` tool class.** ``qc_clean`` and the legacy ``run_qc_workflow``
both persist under tool class ``qc`` writing ``CLEANED_CSV_NAME``, so the reader
resolves whichever committed most recently as "latest cleaned". Retirement of
``run_qc_workflow`` + the vendored ``data_cleanup`` is deferred to after Stage 1;
until then, prefer a single cleaner per experiment. Versioning is single-writer
(``create_run`` allocates ``v<N>`` without compare-and-set) — safe for one
bloom-mcp container; concurrent cleans on the same experiment are not guarded.
"""

from __future__ import annotations

import json
from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field
from sleap_roots_analyze import clean_traits_for_analysis

from bloom_mcp.contract import BloomMCPError, Provenance, as_mcp_tool
from bloom_mcp.contract import register as _contract_register
from bloom_mcp.data_access import ExperimentFrame, ExperimentReadError
from bloom_mcp.data_utils import convert_to_json_serializable
from bloom_mcp.experiment_utils import CLEANED_CSV_NAME, TRAITS_DIR
from bloom_mcp.tools import _ports

_TOOL_CLASS = "qc"
_LOG_NAME = "cleanup_log.json"


class QCCleanParams(BaseModel):
    """Inputs for ``qc_clean``. No ``seed`` — QC is deterministic (threshold filters)."""

    experiment: str = Field(
        ..., description="CSV filename from list_available_experiments."
    )
    trait_columns: Optional[list[str]] = Field(
        default=None,
        description="Subset of trait columns to clean; omit to clean all detected traits.",
    )
    max_zeros_per_trait: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Max fraction of zeros per trait before the trait is dropped.",
    )
    max_nans_per_trait: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Max fraction of NaNs per trait before the trait is dropped.",
    )
    max_nans_per_sample: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Max fraction of NaNs per sample before the sample is dropped.",
    )
    min_samples_per_trait: int = Field(
        default=10, ge=1, description="Min valid samples required to keep a trait."
    )
    user_label: Optional[str] = Field(
        default=None,
        description="Optional slug appended to the version directory name.",
    )


class QCCleanResult(BaseModel):
    """A small in/out summary + links to the persisted cleaned run (no table inline)."""

    experiment: str
    source: str
    n_samples_in: int
    n_samples_out: int
    n_traits_in: int
    n_traits_out: int
    n_samples_dropped: int
    n_traits_dropped: int
    sample_retention: float
    trait_retention: float
    kept_trait_columns: list[str]
    removed_traits: list[str]
    # NaN counts are scoped explicitly: the *input* (raw) frame vs the persisted
    # cleaned frame. `cleaned_nan_cells_remaining` is guaranteed 0 (see guard).
    input_nan_summary: dict[str, int]
    cleaned_nan_cells_remaining: int
    run_ref: str
    version_dir: str
    manifest_path: str
    outputs: dict[str, str]


def _role_kwargs(frame: ExperimentFrame) -> dict[str, str]:
    """Forward the adapter-detected role columns to the delegate.

    Omit any role that is ``None`` so ``clean_traits_for_analysis`` applies its
    own default rather than receiving ``None``.
    """
    roles = {
        "barcode_col": frame.sample_id_col,
        "genotype_col": frame.genotype_col,
        "replicate_col": frame.replicate_col,
    }
    return {k: v for k, v in roles.items() if v is not None}


def _validate_trait_subset(
    frame: ExperimentFrame, requested: list[str], experiment: str
) -> None:
    """Reject a caller-supplied ``trait_columns`` subset up front with a clear remedy.

    Without this an unknown column raises ``KeyError`` (→ opaque ``internal_error``)
    and a non-numeric column silently corrupts the delegate's zero/NaN-fraction
    filtering. Both surface here as a fixable ``invalid_input`` naming the columns.
    """
    missing = [c for c in requested if c not in frame.df.columns]
    if missing:
        raise BloomMCPError(
            code="invalid_input",
            message=f"trait_columns names columns not in {experiment!r}: {missing}.",
            remedy="Use column names from load_experiment_data, or omit trait_columns to clean all detected traits.",
        )
    non_numeric = [
        c for c in requested if not pd.api.types.is_numeric_dtype(frame.df[c])
    ]
    if non_numeric:
        raise BloomMCPError(
            code="invalid_input",
            message=f"trait_columns includes non-numeric columns: {non_numeric}.",
            remedy="Pass only numeric trait columns; metadata/identifier columns cannot be cleaned as traits.",
        )


@as_mcp_tool(
    input_model=QCCleanParams,
    output_model=QCCleanResult,
    errors=(ExperimentReadError,),
)
def qc_clean(params: QCCleanParams, *, provenance: Provenance) -> QCCleanResult:
    """Clean ``experiment`` via analyze's ``clean_traits_for_analysis`` and persist it."""
    reader = _ports.reader()
    store = _ports.store()

    # qc_clean is the producer of cleaned data — read the RAW frame (no require_clean).
    frame = reader.load_experiment(params.experiment)
    if params.trait_columns is not None:
        _validate_trait_subset(frame, params.trait_columns, params.experiment)
    trait_cols = params.trait_columns or frame.trait_cols
    n_samples_in = len(frame.df)
    n_traits_in = len(trait_cols)

    # Delegate ALL cleanup + validate. No QC logic lives here.
    cleaned_df, kept_cols, log = clean_traits_for_analysis(
        frame.df,
        trait_cols=trait_cols,
        max_zeros_per_trait=params.max_zeros_per_trait,
        max_nans_per_trait=params.max_nans_per_trait,
        max_nans_per_sample=params.max_nans_per_sample,
        min_samples_per_trait=params.min_samples_per_trait,
        **_role_kwargs(frame),
    )

    kept_cols = list(kept_cols)
    n_samples_out = len(cleaned_df)
    n_traits_out = len(kept_cols)

    # No-NaN / non-degenerate guarantee, enforced before any run is committed so a
    # bad cleanup never ships a NaN-bearing or empty "cleaned" artifact that
    # pca_analysis(require_clean=True) would then resolve and fail on opaquely.
    cleaned_nan_cells = (
        int(cleaned_df[kept_cols].isna().sum().sum()) if kept_cols else 0
    )
    if not kept_cols or n_samples_out == 0 or cleaned_nan_cells > 0:
        reason = (
            "removed every trait column"
            if not kept_cols
            else "removed every sample"
            if n_samples_out == 0
            else f"left {cleaned_nan_cells} NaN cell(s) in the kept trait columns"
        )
        raise BloomMCPError(
            code="assumption_violated",
            message=f"Cleanup produced no analysis-ready table — it {reason}.",
            remedy=(
                "Relax the cleanup thresholds (e.g. raise max_nans_per_trait / "
                "max_zeros_per_trait / max_nans_per_sample, or lower "
                "min_samples_per_trait) and retry."
            ),
        )

    removed_traits = [c for c in trait_cols if c not in kept_cols]
    nan_mask = frame.df[trait_cols].isna()
    input_nan_summary = {
        "input_samples_with_nan_trait": int(nan_mask.any(axis=1).sum()),
        "input_traits_with_nan": int(nan_mask.any(axis=0).sum()),
        "input_nan_cells": int(nan_mask.sum().sum()),
    }

    # Persist a versioned cleaned run via the ResultStore port; the contract-stamped
    # provenance is carried into the manifest (no re-stamp). source_csv (when the raw
    # is on the local FS) lets the manifest content-address the cleaned run to its input.
    local_src = TRAITS_DIR / params.experiment
    run = store.create_run(
        experiment=params.experiment,
        tool_class=_TOOL_CLASS,
        provenance=provenance,
        user_label=params.user_label,
        source_csv=local_src if local_src.exists() else None,
    )
    cleaned_df.to_csv(run.staging_dir / CLEANED_CSV_NAME, index=False)
    (run.staging_dir / _LOG_NAME).write_text(
        json.dumps(convert_to_json_serializable(log), indent=2)
    )
    stored = store.commit(
        run,
        {CLEANED_CSV_NAME: CLEANED_CSV_NAME, _LOG_NAME: _LOG_NAME},
    )

    return QCCleanResult(
        experiment=params.experiment,
        source=frame.source,
        n_samples_in=n_samples_in,
        n_samples_out=n_samples_out,
        n_traits_in=n_traits_in,
        n_traits_out=n_traits_out,
        n_samples_dropped=n_samples_in - n_samples_out,
        n_traits_dropped=n_traits_in - n_traits_out,
        sample_retention=round(n_samples_out / n_samples_in, 4)
        if n_samples_in
        else 0.0,
        trait_retention=round(n_traits_out / n_traits_in, 4) if n_traits_in else 0.0,
        kept_trait_columns=kept_cols,
        removed_traits=removed_traits,
        input_nan_summary=input_nan_summary,
        cleaned_nan_cells_remaining=cleaned_nan_cells,
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
    )


def register(mcp):
    """Register qc_clean with the MCP server."""
    return _contract_register(mcp, qc_clean)
