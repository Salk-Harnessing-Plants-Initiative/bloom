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
