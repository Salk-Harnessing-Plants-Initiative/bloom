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

import csv
import hashlib
import io

import pandas as pd

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import ExperimentFrame
from bloom_mcp.data_access.columns import resolve_columns

# By the time this string reaches us it is already fully materialized in memory
# (the MCP transport/JSON layer allocated it) — this check does not prevent that
# initial allocation. What it bounds is everything downstream: pandas' in-memory
# representation of a parsed frame is a multiple of the raw text size, so this
# caps that multiplication before `pandas.read_csv` runs, in a shared container
# with no upload step to rate-limit a caller-controlled payload first. See
# design.md for the rationale behind this specific number.
MAX_INLINE_CSV_BYTES = 5 * 1024 * 1024

# A byte cap alone does NOT bound CPU cost: a pathologically wide-but-short CSV
# (many narrow columns) can sit comfortably under MAX_INLINE_CSV_BYTES while
# still costing seconds of CPU in pandas' per-column overhead (dtype inference,
# Python object overhead for labels) — measured directly: ~480,000 columns in a
# single row, 4.69 MB (under the byte cap), took ~7.7s of CPU in
# `pandas.read_csv` alone, with bloommcp having no rate limiting in front of
# this path (FastMCP ships a RateLimitingMiddleware but it is not wired into
# server.py) and no persistence step to create natural backpressure — a real,
# reproducible DoS vector for the shared container. A post-parse check on
# `df.shape[1]` cannot prevent this: the expensive parse has already run by the
# time it fires. `_estimate_header_columns` below is the actual guard — a cheap
# pre-parse estimate that rejects before `pandas.read_csv` is ever called;
# `df.shape[1] > MAX_INLINE_CSV_COLUMNS` after parsing is kept only as an exact
# backstop for the rare residual case (a header value containing an embedded
# newline inside quotes, which a single-line pre-check cannot see) where the
# cheap estimate would miss the true count.
MAX_INLINE_CSV_COLUMNS = 2000

_BOM = "﻿"


def _estimate_header_columns(csv_content: str) -> int:
    """Cheap, O(header-length) column-count estimate — does not parse the body.

    Parses only the first line via `csv.reader` (not a naive `.count(",")`,
    which would overcount — and so falsely reject an otherwise-fine payload —
    the moment a single header name contains a quoted comma). This function's
    job is purely to reject an absurdly wide payload before the expensive
    `pandas.read_csv` call runs at all; `parse_inline_csv_frame`'s post-parse
    `df.shape[1]` check is the exact backstop for the one case this single-line
    estimate cannot see: a header value containing an embedded newline within
    quotes.
    """
    header_line = csv_content.split("\n", 1)[0]
    try:
        return len(next(csv.reader([header_line])))
    except StopIteration:
        return 0


def parse_inline_csv_frame(csv_content: str) -> ExperimentFrame:
    """Parse ``csv_content`` into an in-memory :class:`ExperimentFrame`.

    Raises :class:`BloomMCPError` (``invalid_input``) for an oversized payload,
    too many columns, unparseable content, zero data rows, zero columns, or an
    encode/decode failure — never a raw ``pandas``/``Unicode`` exception.
    """
    # Strip every leading BOM, not just one — a double-encoded or re-saved file
    # can carry more than one, and any left in place mangles the first column
    # name (e.g. "﻿Barcode"), silently breaking role detection for it.
    csv_content = csv_content.lstrip(_BOM)

    # Cheap pre-parse guard FIRST — before the byte-size check even, since both
    # are cheap, but this one is what actually prevents the wide-CSV CPU DoS
    # (see MAX_INLINE_CSV_COLUMNS above). Must run before pandas.read_csv, not
    # after.
    estimated_columns = _estimate_header_columns(csv_content)
    if estimated_columns > MAX_INLINE_CSV_COLUMNS:
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"csv_content's header implies approximately {estimated_columns} "
                f"columns, exceeding the {MAX_INLINE_CSV_COLUMNS}-column limit "
                f"for inline content."
            ),
            remedy=(
                "Reduce the number of columns, or register the data as an "
                "experiment instead of passing it inline."
            ),
        )

    try:
        encoded = csv_content.encode("utf-8")
    except UnicodeEncodeError as exc:
        # A lone UTF-16 surrogate (possible via a lossy upstream decode) raises
        # here, not in pandas — must be mapped explicitly or it becomes an
        # opaque internal_error, contradicting this module's "never a raw
        # Unicode exception" guarantee.
        raise BloomMCPError(
            code="invalid_input",
            message=f"csv_content could not be encoded as UTF-8: {exc}",
            remedy="Ensure csv_content is valid UTF-8 text (no unpaired "
            "surrogates) and retry.",
        ) from None

    byte_length = len(encoded)
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
    if df.shape[1] > MAX_INLINE_CSV_COLUMNS:
        # Exact backstop for the rare case _estimate_header_columns undercounts
        # (a quoted comma inside a header name) — the common case is already
        # rejected above, before the parse ever ran.
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"csv_content has {df.shape[1]} columns, exceeding the "
                f"{MAX_INLINE_CSV_COLUMNS}-column limit for inline content."
            ),
            remedy=(
                "Reduce the number of columns, or register the data as an "
                "experiment instead of passing it inline."
            ),
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

    Raises :class:`BloomMCPError` (``invalid_input``) rather than a raw
    ``UnicodeEncodeError`` if ``csv_content`` cannot be encoded (e.g. a lone
    surrogate) — this function is a public entry point in its own right, not
    guaranteed to run only after ``parse_inline_csv_frame`` has already
    validated the same string.
    """
    try:
        encoded = csv_content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BloomMCPError(
            code="invalid_input",
            message=f"csv_content could not be encoded as UTF-8: {exc}",
            remedy="Ensure csv_content is valid UTF-8 text (no unpaired "
            "surrogates) and retry.",
        ) from None
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "MAX_INLINE_CSV_BYTES",
    "MAX_INLINE_CSV_COLUMNS",
    "parse_inline_csv_frame",
    "compute_input_sha256",
]
