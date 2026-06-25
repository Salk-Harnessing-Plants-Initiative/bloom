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
(``_cleaned.csv``) + the cleanup log + provenance — under tool class ``qc``. That
``_cleaned.csv`` naming is what the reader resolves as a *cleaned version*, so a
later ``pca_analysis`` (``require_clean=True``) consumes this run: the
qc_clean → pca_analysis composition. The result returns a small in/out summary +
links to the persisted artifacts (run ref, manifest, object keys) — never the
cleaned table inline.
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, Field
from sleap_roots_analyze import clean_traits_for_analysis

from bloom_mcp.contract import BloomMCPError, Provenance, as_mcp_tool
from bloom_mcp.contract import register as _contract_register
from bloom_mcp.data_access import ExperimentFrame, ExperimentReadError
from bloom_mcp.data_utils import convert_to_json_serializable
from bloom_mcp.tools import _ports

_TOOL_CLASS = "qc"


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
    retention_score: float
    kept_trait_columns: list[str]
    removed_traits: list[str]
    nan_summary: dict[str, int]
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

    if not list(kept_cols):
        raise BloomMCPError(
            code="assumption_violated",
            message="Cleanup removed every trait column; no analysis-ready table remains.",
            remedy=(
                "Relax the cleanup thresholds (e.g. raise max_nans_per_trait / "
                "max_zeros_per_trait, or lower min_samples_per_trait) and retry."
            ),
        )

    kept_cols = list(kept_cols)
    n_samples_out = len(cleaned_df)
    n_traits_out = len(kept_cols)
    removed_traits = [c for c in trait_cols if c not in kept_cols]
    nan_mask = frame.df[trait_cols].isna()
    nan_summary = {
        "n_samples_with_nan_trait": int(nan_mask.any(axis=1).sum()),
        "n_traits_with_nan": int(nan_mask.any(axis=0).sum()),
    }

    # Persist a versioned cleaned run via the ResultStore port; the contract-stamped
    # provenance is carried into the manifest (no re-stamp).
    run = store.create_run(
        experiment=params.experiment,
        tool_class=_TOOL_CLASS,
        provenance=provenance,
        user_label=params.user_label,
    )
    cleaned_df.to_csv(run.staging_dir / "_cleaned.csv", index=False)
    (run.staging_dir / "cleanup_log.json").write_text(
        json.dumps(convert_to_json_serializable(log), indent=2)
    )
    stored = store.commit(
        run,
        {"_cleaned.csv": "_cleaned.csv", "cleanup_log.json": "cleanup_log.json"},
    )

    retention = (
        (n_samples_out * n_traits_out) / (n_samples_in * n_traits_in)
        if n_samples_in and n_traits_in
        else 0.0
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
        retention_score=round(retention, 4),
        kept_trait_columns=kept_cols,
        removed_traits=removed_traits,
        nan_summary=nan_summary,
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
    )


def register(mcp):
    """Register qc_clean with the MCP server."""
    return _contract_register(mcp, qc_clean)
