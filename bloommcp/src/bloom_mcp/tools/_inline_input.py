"""Shared ephemeral CSV-parsing helper for a tool's inline-content input path (#582).

Parses a caller-supplied CSV string directly into an in-memory :class:`ExperimentFrame`
— never written to Storage, never registered, never persisted. Resolves column roles
and trait columns through the same :func:`resolve_columns` unit every
:class:`ExperimentReader` adapter uses, so an inline frame is indistinguishable in shape
from an adapter-sourced one. This is the shared surface every consumer tool's own
``csv_content`` path imports; ``qc_clean`` is the first (and, as of this module, only)
caller — it is not specific to any one tool.
"""

from __future__ import annotations

import hashlib
import io

import pandas as pd

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import ExperimentFrame
from bloom_mcp.data_access.columns import resolve_columns

# A caller-controlled string is an unbounded allocation with no upload step to
# rate-limit it first, in a shared container — see design.md for the rationale
# behind this specific number.
MAX_INLINE_CSV_BYTES = 5 * 1024 * 1024

_BOM = "﻿"


def parse_inline_csv_frame(csv_content: str) -> ExperimentFrame:
    """Parse ``csv_content`` into an in-memory :class:`ExperimentFrame`.

    Raises :class:`BloomMCPError` (``invalid_input``) for an oversized payload,
    unparseable content, zero data rows, zero columns, or a decode failure —
    never a raw ``pandas``/``Unicode`` exception.
    """
    if csv_content.startswith(_BOM):
        csv_content = csv_content[len(_BOM) :]

    byte_length = len(csv_content.encode("utf-8"))
    if byte_length > MAX_INLINE_CSV_BYTES:
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"csv_content is {byte_length} bytes, exceeding the "
                f"{MAX_INLINE_CSV_BYTES}-byte limit for inline content."
            ),
            remedy=(
                "Reduce the CSV content size, or register the data as an "
                "experiment instead of passing it inline."
            ),
        )

    try:
        df = pd.read_csv(io.StringIO(csv_content))
    except pd.errors.EmptyDataError:
        raise BloomMCPError(
            code="invalid_input",
            message="csv_content has no data rows.",
            remedy="Supply CSV content with a header row and at least one data row.",
        ) from None
    except pd.errors.ParserError as exc:
        raise BloomMCPError(
            code="invalid_input",
            message=f"csv_content could not be parsed as CSV: {exc}",
            remedy="Fix the CSV formatting (consistent field counts per row) and retry.",
        ) from None
    except UnicodeDecodeError as exc:
        raise BloomMCPError(
            code="invalid_input",
            message=f"csv_content could not be decoded: {exc}",
            remedy="Ensure csv_content is valid UTF-8 text and retry.",
        ) from None

    if df.shape[1] == 0:
        raise BloomMCPError(
            code="invalid_input",
            message="csv_content has no columns.",
            remedy="Supply CSV content with a header row naming at least one column.",
        )
    if df.shape[0] == 0:
        raise BloomMCPError(
            code="invalid_input",
            message="csv_content has no data rows.",
            remedy="Supply CSV content with a header row and at least one data row.",
        )

    resolved = resolve_columns(df)
    return ExperimentFrame(
        df=df,
        trait_cols=resolved.trait_cols,
        metadata_cols=resolved.metadata_cols,
        genotype_col=resolved.genotype,
        replicate_col=resolved.replicate,
        sample_id_col=resolved.sample_id,
        source="inline",
    )


def compute_input_sha256(csv_content: str) -> str:
    """SHA-256 hex digest over the exact UTF-8-encoded bytes of ``csv_content``.

    Computed over the original string (before any BOM-stripping), so it reflects
    exactly what the caller sent. Independent of any manifest-/``Provenance``-level
    hash — this value exists solely for the caller's own record-keeping, since
    nothing is stored server-side to check it against later.
    """
    return hashlib.sha256(csv_content.encode("utf-8")).hexdigest()


__all__ = ["MAX_INLINE_CSV_BYTES", "parse_inline_csv_frame", "compute_input_sha256"]
