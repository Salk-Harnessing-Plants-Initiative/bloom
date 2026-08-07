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
# backstop, not the primary guard.
MAX_INLINE_CSV_COLUMNS = 2000

# Caps how much of csv_content `_estimate_header_columns` will scan looking for
# the header row's closing boundary. A row-aware scan (see below) must read an
# unterminated quoted field until it finds the closing quote or gives up — an
# attacker who never closes the quote could otherwise force a scan of the
# entire payload, defeating the point of a "cheap" pre-parse check. No real
# header row (even at MAX_INLINE_CSV_COLUMNS columns with generous name
# lengths) comes close to this.
_MAX_HEADER_SCAN_BYTES = 256 * 1024

_BOM = "﻿"


def _bounded_lines(text: str):
    """Yield ``text``'s lines like iterating ``io.StringIO(text)``, but raise
    ``BloomMCPError`` if more than `_MAX_HEADER_SCAN_BYTES` is consumed without
    the caller stopping — the guard against an unterminated quote forcing
    `_estimate_header_columns` to scan the whole payload (see its docstring).
    """
    consumed = 0
    for line in io.StringIO(text):
        consumed += len(line)
        if consumed > _MAX_HEADER_SCAN_BYTES:
            raise BloomMCPError(
                code="invalid_input",
                message=(
                    f"csv_content's header row could not be determined within "
                    f"the first {_MAX_HEADER_SCAN_BYTES} bytes."
                ),
                remedy=(
                    "Ensure the header row is well-formed (every quote closed) "
                    "and not unusually large, or register the data as an "
                    "experiment instead of passing it inline."
                ),
            )
        yield line


def _estimate_header_columns(csv_content: str) -> int:
    """Cheap column-count estimate for the header row — does not parse the body.

    Feeds `csv.reader` a bounded line iterator (`_bounded_lines`), not a naive
    ``csv_content.split("\\n", 1)[0]``. The naive split cuts a row short the
    moment any field contains a literal newline inside quotes (valid CSV) —
    reproduced directly: a crafted header whose first cell is a quoted value
    containing one embedded newline made the naive split's estimate say "1
    column" for a real ~480,000-column row, letting the expensive
    `pandas.read_csv` call run anyway (~5-9s of CPU) before the post-parse
    backstop caught it — exactly the cost this guard exists to avoid.
    `csv.reader` fed a genuine line iterator instead handles this correctly:
    it keeps consuming lines from the iterator until the quoted field's
    closing quote is found and the row is complete, the same way iterating a
    real file handles a multi-line quoted CSV field. `_bounded_lines` caps how
    far it will do that (an unterminated quote would otherwise force scanning
    the entire payload), rejecting outright rather than guessing when the
    header's true extent can't be found cheaply.
    """
    try:
        row = next(csv.reader(_bounded_lines(csv_content)))
    except StopIteration:
        return 0
    return len(row)


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
        # Exact backstop, not the primary guard: _estimate_header_columns
        # above already uses the same csv.reader-based, multi-line-aware
        # tokenization pandas itself effectively performs, so this should not
        # fire in practice — kept as defense-in-depth against any residual
        # divergence between the two, at the cost of the parse already having
        # run.
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
