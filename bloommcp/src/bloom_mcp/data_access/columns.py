"""Column-role resolution + trait detection — bloommcp's single source of truth.

Role-name matching (which column *is* the genotype / sample-id / replicate) is
**bloommcp domain knowledge** and lives here: ``sleap_roots_analyze`` takes
*configured* role names, it does not detect them. Trait detection, however,
**delegates to** ``sleap_roots_analyze.get_trait_columns`` so numeric metadata
(e.g. ``Computation.Time.s`` — matched by the upstream ``"time"`` substring rule)
is never analyzed as a biological trait, consistently for every consumer.

Both the read adapters (via :func:`bloom_mcp.experiment_utils.detect_columns`,
now a thin shim over this) and ``qc_clean`` (with overrides) resolve columns
through :func:`resolve_columns`, so the reader and the QC producer cannot drift.
This module has **no** ``bloom_mcp`` imports, so it is import-safe from
``experiment_utils`` (which the ``data_access`` package imports transitively).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from sleap_roots_analyze import get_trait_columns

# Auto-detect patterns for the role columns (case-insensitive exact match).
# These are bloommcp's mapping of real-world column names to the canonical roles;
# they moved here from ``experiment_utils`` so role matching lives in one place.
GENOTYPE_PATTERNS = ["geno", "genotype", "accession", "species_name"]
REPLICATE_PATTERNS = ["rep", "replicate", "wave_number"]
SAMPLE_ID_PATTERNS = ["barcode", "plant_qr_code", "scan_id", "plant_id", "plant_name"]


@dataclass(frozen=True)
class ResolvedColumns:
    """The resolved role columns + the delegated trait/metadata split for a frame.

    ``trait_cols`` is what ``sleap_roots_analyze.get_trait_columns`` returns (numeric
    metadata excluded). ``excluded_cols`` is the numeric columns dropped from the
    trait set (numeric metadata such as ``Computation.Time.s`` plus any explicit
    ``exclude_columns``) — the "why isn't my column a trait" list. ``metadata_cols``
    is every non-trait column (roles + non-numeric + excluded), so a caller can
    reconstruct the old ``detect_columns`` dict.
    """

    genotype: Optional[str]
    sample_id: Optional[str]
    replicate: Optional[str]
    trait_cols: list[str]
    excluded_cols: list[str]
    metadata_cols: list[str]


def _find_column(columns, patterns: list[str]) -> Optional[str]:
    """Find first column matching any pattern (case-insensitive exact match)."""
    col_lower_map = {c.lower().strip(): c for c in columns}
    for pattern in patterns:
        if pattern.lower() in col_lower_map:
            return col_lower_map[pattern.lower()]
    return None


def resolve_columns(
    df: pd.DataFrame,
    *,
    sample_id_column: Optional[str] = None,
    genotype_column: Optional[str] = None,
    exclude_columns: Optional[list[str]] = None,
) -> ResolvedColumns:
    """Resolve role columns (bloommcp matching) + traits (delegated upstream).

    ``sample_id_column`` / ``genotype_column`` override auto-detection for that
    role; ``exclude_columns`` is forwarded to ``get_trait_columns`` as
    ``additional_exclude`` (a metadata deny-list). Replicate is auto-detect only.

    This function is **pure resolution** — it never raises on an unresolved or a
    non-existent override; enforcing that a *required* role resolved, and that an
    override names a real column, is the caller's policy (see ``qc_clean``).
    """
    genotype = genotype_column or _find_column(df.columns, GENOTYPE_PATTERNS)
    sample_id = sample_id_column or _find_column(df.columns, SAMPLE_ID_PATTERNS)
    replicate = _find_column(df.columns, REPLICATE_PATTERNS)

    # Trait detection is delegated so numeric metadata (e.g. Computation.Time.s)
    # is excluded consistently for every consumer. get_trait_columns is None-safe:
    # a None role simply never matches a column to exclude.
    trait_cols = get_trait_columns(
        df,
        barcode_col=sample_id,
        genotype_col=genotype,
        replicate_col=replicate,
        additional_exclude=list(exclude_columns) if exclude_columns else None,
    )

    trait_set = set(trait_cols)
    role_set = {r for r in (genotype, sample_id, replicate) if r}
    exclude_set = set(exclude_columns or ())
    excluded_cols = [
        c
        for c in df.columns
        if c not in trait_set
        and c not in role_set
        and (pd.api.types.is_numeric_dtype(df[c]) or c in exclude_set)
    ]
    metadata_cols = [c for c in df.columns if c not in trait_set]
    return ResolvedColumns(
        genotype=genotype,
        sample_id=sample_id,
        replicate=replicate,
        trait_cols=trait_cols,
        excluded_cols=excluded_cols,
        metadata_cols=metadata_cols,
    )


@dataclass(frozen=True)
class _Roles:
    """Duck-typed ``ColumnRoles`` for ``validate_entry_input`` — note ``barcode``.

    The upstream contract's ``ColumnRoles`` protocol reads ``.genotype`` /
    ``.barcode`` / ``.replicate``; bloommcp's ``sample_id`` role maps onto
    ``.barcode`` (the upstream name for the sample identifier).
    """

    genotype: Optional[str]
    barcode: Optional[str]
    replicate: Optional[str]


class _WarningCapture(logging.Handler):
    """Collect ``validate_entry_input``'s advisory warnings into a list."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def run_input_validation(
    df: pd.DataFrame,
    resolved: ResolvedColumns,
    *,
    exclude_columns: Optional[list[str]] = None,
    mode: str = "warn",
) -> list[str]:
    """Run analyze's input contract and return the advisory warnings.

    Delegates to ``sleap_roots_analyze.validation.validate_entry_input`` (no
    contract logic here), mapping the resolved ``sample_id`` role onto the
    contract's ``.barcode``. In ``warn`` mode it **raises ``ValueError``** on a
    universal structural failure (no numeric trait, NaN/blank genotype, bad role
    dtype) — the caller maps that to a structured error. If ``sleap-roots-contracts``
    is not installed, the delegate degrades to a logged no-op (returns ``[]``).
    """
    from sleap_roots_analyze.validation import validate_entry_input

    capture = _WarningCapture()
    logger = logging.Logger("bloom_mcp.input_validation")
    logger.setLevel(logging.WARNING)
    logger.addHandler(capture)
    validate_entry_input(
        df,
        columns=_Roles(
            genotype=resolved.genotype,
            barcode=resolved.sample_id,
            replicate=resolved.replicate,
        ),
        mode=mode,
        additional_exclude=list(exclude_columns) if exclude_columns else None,
        logger=logger,
    )
    return capture.messages
