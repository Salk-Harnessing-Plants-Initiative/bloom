"""Supabase-backed :class:`ExperimentReader` — wraps the deployed read path.

This adapter relocates ``experiment_utils.load_experiment_data`` behind the
port. Resolution order for a name:

1. versioned-cleaned output from Supabase Storage (the QC manifest's latest),
2. the legacy un-versioned cleaned CSV,
3. the raw input from the local ``BLOOM_TRAITS_DIR`` — **deprecated**; it emits a
   :class:`DeprecationWarning` and is removed once inputs fully move to
   ``bloommcp_input/``,
4. an **uploaded input** in ``bloommcp_input/`` (Supabase Storage), loaded via
   the format registry so any registered format works, not just CSV.

``list_experiments`` returns the local experiments plus any uploaded inputs.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Optional

from bloom_mcp import input_formats
from bloom_mcp.experiment_utils import (
    detect_columns,
    list_experiments as _list_experiments,
    load_experiment_data as _load_experiment_data,
)
from bloom_mcp.supabase_client import download_input, list_input_names

from .ports import (
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentNotFoundError,
    ExperimentSummary,
)

_LOCAL_RAW_DEPRECATION = (
    "Reading raw experiment inputs from the local BLOOM_TRAITS_DIR is "
    "deprecated; inputs will move to Supabase Storage (bloommcp_input/)."
)


def _frame_from(df, config, source: str) -> ExperimentFrame:
    return ExperimentFrame(
        df=df,
        trait_cols=config["trait_cols"],
        metadata_cols=config["metadata_cols"],
        genotype_col=config["genotype_col"],
        replicate_col=config["replicate_col"],
        sample_id_col=config["sample_id_col"],
        source=source,
    )


def _load_uploaded(name: str) -> Optional[ExperimentFrame]:
    """Resolve an uploaded input in `bloommcp_input/` via the format registry.

    Returns ``None`` when the name is not a registered format, the object is
    missing, or it does not parse — the caller then signals not-found. Storage
    errors are swallowed so no bucket/path detail leaks to the caller.
    """
    if input_formats.get_format_by_filename(name) is None:
        return None
    try:
        data = download_input(name)
    except Exception:  # noqa: BLE001 - missing/unreadable object → fall through
        return None
    try:
        df = input_formats.load_frame(name, data)
    except input_formats.FormatError:
        return None
    return _frame_from(df, detect_columns(df), "uploaded")


def _list_uploaded_summaries() -> list[ExperimentSummary]:
    """List uploaded inputs (names only — not downloaded), skipping unregistered
    formats. Numeric fields are 0/None until the file is actually loaded."""
    try:
        names = list_input_names()
    except Exception:  # noqa: BLE001 - listing failure is not fatal to discovery
        return []
    summaries: list[ExperimentSummary] = []
    for name in names:
        if input_formats.get_format_by_filename(name) is None:
            continue
        stem = Path(name).stem
        summaries.append(
            ExperimentSummary(
                filename=name,
                stem=stem,
                rows=0,
                total_columns=0,
                trait_columns=0,
                experiment_name=stem,
                genotype_col=None,
                sample_id_col=None,
            )
        )
    return summaries


class SupabaseReader:
    """Reads experiment inputs via the deployed Supabase + local-FS path."""

    def load_experiment(
        self,
        name: str,
        *,
        version: str = "latest",
        require_clean: bool = False,
    ) -> ExperimentFrame:
        df, _trait_cols, config, source_label = _load_experiment_data(
            name, require_clean=require_clean, version=version
        )
        if df is not None:
            if source_label == "raw":
                warnings.warn(_LOCAL_RAW_DEPRECATION, DeprecationWarning, stacklevel=2)
            return _frame_from(df, config, source_label)

        # The deployed tiers (versioned/legacy cleaned, local raw) all missed.
        # Try an uploaded input in bloommcp_input/ (multi-format) before
        # signalling not-found. Uploaded inputs are raw, so only for latest/raw.
        if not require_clean and version in ("latest", "raw"):
            uploaded = _load_uploaded(name)
            if uploaded is not None:
                return uploaded

        # `source_label` here may be the loader's raw error string (may name a
        # path); do NOT surface it — raise a caller-safe message instead.
        if require_clean:
            raise CleanedVersionRequiredError(
                f"No cleaned dataset found for {name!r}; run the QC workflow first."
            )
        raise ExperimentNotFoundError(
            f"Experiment {name!r} (version={version!r}) could not be resolved."
        )

    def list_experiments(self) -> list[ExperimentSummary]:
        summaries = [
            ExperimentSummary(
                filename=exp["filename"],
                stem=exp["stem"],
                rows=exp["rows"],
                total_columns=exp["total_columns"],
                trait_columns=exp["trait_columns"],
                experiment_name=exp["experiment_name"],
                genotype_col=exp["genotype_col"],
                sample_id_col=exp["sample_id_col"],
            )
            for exp in _list_experiments()
        ]
        seen = {s.filename for s in summaries}
        for extra in _list_uploaded_summaries():
            if extra.filename not in seen:
                summaries.append(extra)
                seen.add(extra.filename)
        return summaries


# Re-exported so consumers that need ad-hoc role detection use the same source
# of truth the adapter declares roles from.
__all__ = ["SupabaseReader", "detect_columns"]
