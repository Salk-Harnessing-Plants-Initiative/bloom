"""Shared helpers for the granular QC tools (``qc_clean`` #338, ``qc_inspect`` #360).

Both tools read the **raw** experiment frame and forward the adapter-detected role
columns into ``sleap_roots_analyze`` delegates the same way, and both validate a
caller-supplied ``trait_columns`` subset up front. Factoring these here keeps the
two tools in lockstep rather than drifting as two copies.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import ExperimentFrame

# Canonical cleanup-threshold defaults shared by qc_clean and qc_inspect — the values
# ``sleap_roots_analyze``'s QC pipeline cleans with (``CleanupConfig`` / the ``_QC_DEFAULTS``
# that ``clean_traits_for_analysis`` injects). On the pinned analyze version these coincide
# with ``apply_data_cleanup_filters``'s own signature defaults (0.5 / 0.2 / 0.0 / 10) — a
# `test_canonical_thresholds_match_upstream_delegate_defaults` tripwire pins that so an
# upstream change to the delegate defaults trips CI (re-verify against the QC pipeline
# canonical; earlier analyze versions shipped looser signature defaults, which is why both
# tools still forward all four thresholds *explicitly*). Both tools import these so a default
# ``qc_clean`` reproduces the pipeline's clean and ``qc_inspect``'s overlays/recommendation
# reflect that same clean; single-sourcing here keeps them from silently desyncing.
# Source of truth: talmolab/sleap-roots-analyze#167 + ``CleanupConfig``.
_CANONICAL_MAX_ZEROS_PER_TRAIT = 0.5
_CANONICAL_MAX_NANS_PER_TRAIT = 0.2
_CANONICAL_MAX_NANS_PER_SAMPLE = 0.0
_CANONICAL_MIN_SAMPLES_PER_TRAIT = 10


def _validate_experiment_name(experiment: str) -> None:
    """Reject an ``experiment`` that is anything but a bare filename.

    ``experiment`` flows into ``TRAITS_DIR / experiment`` + ``pd.read_csv``, so a path with
    separators or ``..`` (or an absolute path) would read outside ``TRAITS_DIR`` — and for a
    read-and-persist tool like ``qc_inspect`` the contents could then surface in committed
    artifacts. Require a bare basename. (The cross-tool fix is to centralize this in
    ``load_experiment_data`` so the whole tool family is covered; this guards the QC tools now.)

    ``Path(experiment).name != experiment`` alone is not enough: ``pathlib.Path`` only
    treats ``\\`` as a separator on Windows, so on POSIX (the deploy target)
    ``Path("..\\\\secret.csv").name`` equals the input unchanged and the traversal payload
    would slip past this guard. Check for either separator explicitly (mirrors the fix in
    ``sections/sleap_roots/analysis/_viz_shared.validate_filename``).
    """
    if (
        experiment in ("", ".", "..")
        or "/" in experiment
        or "\\" in experiment
        or Path(experiment).name != experiment
    ):
        raise BloomMCPError(
            code="invalid_input",
            message="experiment must be a bare CSV filename (no path separators).",
            remedy="Pass a filename from list_available_experiments, e.g. 'my_experiment.csv'.",
        )


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
    frame: ExperimentFrame,
    requested: list[str],
    experiment: str,
    *,
    require_certified: bool = False,
) -> None:
    """Reject a caller-supplied ``trait_columns`` subset up front with a clear remedy.

    Without this an unknown column raises ``KeyError`` (→ opaque ``internal_error``)
    and a non-numeric column silently corrupts the delegate's zero/NaN-fraction
    filtering. Both surface here as a fixable ``invalid_input`` naming the columns.

    Two strictness levels share this one validator:

    * **default (``require_certified=False``)** — the raw-frame **producers** ``qc_clean`` /
      ``qc_inspect``: a selection is valid if every column exists in the frame and is numeric.
      An empty list is allowed (falls through to "all detected traits") and duplicates are
      harmless to those delegates.
    * **``require_certified=True``** — the cleaned-frame **consumers** ``pca_analysis`` /
      ``clustering``: additionally require each column to be a member of the certified-clean
      trait set (``frame.trait_cols``), reject an empty list (it must not silently mean "all
      certified traits"), and reject duplicates (a delegate that re-selects a repeated column
      inflates the fitted feature set). The certified-set restriction is what forecloses the
      silent-``dropna()`` path — a NaN-bearing numeric column ``qc_clean`` did not adopt as a
      surviving trait cannot be selected. Keeping the *default* behavior byte-identical is what
      lets ``qc_inspect`` keep consuming this helper unchanged.
    """
    if require_certified:
        if not requested:
            raise BloomMCPError(
                code="invalid_input",
                message="trait_columns was given as an empty list.",
                remedy=(
                    "Omit trait_columns to analyze all certified-clean traits, or name at "
                    "least one certified trait column."
                ),
            )
        duplicates = sorted({c for c in requested if requested.count(c) > 1})
        if duplicates:
            raise BloomMCPError(
                code="invalid_input",
                message=f"trait_columns contains duplicate columns: {duplicates}.",
                remedy="List each trait column at most once.",
            )
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
        return

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
