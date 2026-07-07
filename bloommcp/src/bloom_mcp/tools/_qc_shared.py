"""Shared helpers for the granular QC tools (``qc_clean`` #338, ``qc_inspect`` #360).

Both tools read the **raw** experiment frame and forward the adapter-detected role
columns into ``sleap_roots_analyze`` delegates the same way, and both validate a
caller-supplied ``trait_columns`` subset up front. Factoring these here keeps the
two tools in lockstep rather than drifting as two copies.
"""

from __future__ import annotations

import pandas as pd

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import ExperimentFrame

# Canonical cleanup-threshold defaults shared by qc_clean and qc_inspect — the values
# ``sleap_roots_analyze``'s QC pipeline actually cleans with (``CleanupConfig`` in
# ``pipeline/config/components.py``, and the ``_QC_DEFAULTS`` that
# ``clean_traits_for_analysis`` injects) — NOT the looser ``apply_data_cleanup_filters``
# *signature* defaults (``max_nans_per_trait=0.3`` / ``max_nans_per_sample=0.2``). Two
# values differ: the pipeline is stricter — ``max_nans_per_trait=0.2`` drops NaN-heavier
# traits sooner, and ``max_nans_per_sample=0.0`` drops any sample that still carries a NaN
# in a kept trait. Both tools forward all four thresholds *explicitly*, so they must carry
# the canonical values here — a default ``qc_clean`` reproduces the pipeline's clean, and
# ``qc_inspect``'s overlays/recommendation reflect that same clean. Single-sourcing them
# here keeps the two tools from silently desyncing. Source of truth:
# talmolab/sleap-roots-analyze#167 + ``CleanupConfig`` (max_nan_fraction=0.0,
# max_zeros_per_trait=0.5, max_nans_per_trait=0.2, min_samples_per_trait=10).
_CANONICAL_MAX_ZEROS_PER_TRAIT = 0.5
_CANONICAL_MAX_NANS_PER_TRAIT = 0.2
_CANONICAL_MAX_NANS_PER_SAMPLE = 0.0
_CANONICAL_MIN_SAMPLES_PER_TRAIT = 10


def _role_kwargs(frame: ExperimentFrame) -> dict[str, str]:
    """Forward the adapter-detected role columns to a cleanup/EDA delegate.

    Omit any role that is ``None`` so the delegate applies its own default rather
    than receiving ``None``.
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
            remedy="Use column names from load_experiment_data, or omit trait_columns to use all detected traits.",
        )
    non_numeric = [
        c for c in requested if not pd.api.types.is_numeric_dtype(frame.df[c])
    ]
    if non_numeric:
        raise BloomMCPError(
            code="invalid_input",
            message=f"trait_columns includes non-numeric columns: {non_numeric}.",
            remedy="Pass only numeric trait columns; metadata/identifier columns cannot be used as traits.",
        )
