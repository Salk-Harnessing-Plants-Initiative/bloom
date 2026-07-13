"""Supabase-backed :class:`ExperimentReader` — wraps the deployed read path.

This adapter relocates ``experiment_utils.load_experiment_data`` behind the
port: versioned-cleaned outputs from Supabase Storage, then the legacy
un-versioned cleaned CSV, then the raw input. The raw-input read still comes
from the local ``BLOOM_TRAITS_DIR`` and is **deprecated** — it emits a
:class:`DeprecationWarning` so the follow-up that migrates inputs into
``bloommcp_input/`` can remove it.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Optional

import bloom_mcp.experiment_utils as _eu
from bloom_mcp.experiment_utils import (
    detect_columns,
    list_experiments as _list_experiments,
    load_experiment_data as _load_experiment_data,
)

from .ports import (
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentNotFoundError,
    ExperimentSummary,
)

_LOCAL_RAW_DEPRECATION = (
    "Reading raw experiment inputs from the local BLOOM_TRAITS_DIR on the Supabase "
    "path is deprecated; for local inputs use the opt-in LocalReader adapter "
    "(BLOOM_STORAGE_BACKEND=local) — the path is promoted, not slated for removal."
)


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
        if df is None:
            # `source_label` here is the raw error string from the deployed
            # loader (may name a path); do NOT surface it. Raise a caller-safe
            # message instead.
            if require_clean:
                raise CleanedVersionRequiredError(
                    f"No cleaned dataset found for {name!r}; run the QC workflow first."
                )
            raise ExperimentNotFoundError(
                f"Experiment {name!r} (version={version!r}) could not be resolved."
            )

        if source_label == "raw":
            warnings.warn(_LOCAL_RAW_DEPRECATION, DeprecationWarning, stacklevel=2)

        return ExperimentFrame(
            df=df,
            trait_cols=config["trait_cols"],
            metadata_cols=config["metadata_cols"],
            genotype_col=config["genotype_col"],
            replicate_col=config["replicate_col"],
            sample_id_col=config["sample_id_col"],
            source=source_label,
        )

    def raw_source_path(self, name: str) -> Optional[Path]:
        """The on-disk raw input path for ``name`` (local ``BLOOM_TRAITS_DIR``).

        Used by ``tools._ports.start_run`` to content-address a run's input; the
        deployed raw inputs still live on local disk, so this is the path the store
        hashes into ``input_sha256``. Returns ``None`` when the file is absent.
        Read via the module attribute so a monkeypatched ``TRAITS_DIR`` is honoured.

        ``name`` is LLM-controlled (it arrives via ``_ports.start_run``), so reject
        anything but a bare filename: a crafted ``"../secrets.csv"`` must not resolve
        outside ``TRAITS_DIR`` and have its bytes hashed into the run provenance.
        (``LocalReader`` guards the same via ``_safe_name``.)
        """
        if name != Path(name).name:
            return None
        candidate = _eu.TRAITS_DIR / name
        return candidate if candidate.is_file() else None

    def list_experiments(self) -> list[ExperimentSummary]:
        # The scan dicts' keys are exactly ExperimentSummary's fields, so splat them
        # (identical to LocalReader — one place to update if a field is added).
        return [ExperimentSummary(**exp) for exp in _list_experiments()]


# Re-exported so consumers that need ad-hoc role detection use the same source
# of truth the adapter declares roles from.
__all__ = ["SupabaseReader", "detect_columns"]
