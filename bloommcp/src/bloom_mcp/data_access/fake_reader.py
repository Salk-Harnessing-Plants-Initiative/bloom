"""In-memory :class:`ExperimentReader` for tests — no Supabase, no filesystem."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from bloom_mcp.experiment_utils import detect_columns

from .ports import (
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentNotFoundError,
    ExperimentSummary,
)


class FakeReader:
    """Seed raw frames and (optionally) cleaned versions, then read them back.

    Mirrors :class:`SupabaseReader`'s observable behaviour — same resolution
    order, same not-found / clean-required signalling, same declared roles
    (via :func:`detect_columns`) — so a single scenario set can be run against
    both as a parity check.
    """

    def __init__(self) -> None:
        # name -> raw DataFrame
        self._raw: dict[str, pd.DataFrame] = {}
        # name -> {version_id: cleaned DataFrame}
        self._cleaned: dict[str, dict[str, pd.DataFrame]] = {}
        # name -> latest cleaned version id
        self._latest: dict[str, str] = {}
        # name -> experiment_name label
        self._exp_name: dict[str, str] = {}
        # name -> uploaded input frame (bloommcp_input/ analogue)
        self._uploaded: dict[str, pd.DataFrame] = {}

    # --- seeding -----------------------------------------------------------

    def add_experiment(
        self,
        name: str,
        df: pd.DataFrame,
        *,
        experiment_name: Optional[str] = None,
    ) -> None:
        """Register a raw experiment frame under ``name``."""
        self._raw[name] = df.copy()
        self._exp_name[name] = experiment_name or _stem(name)

    def add_uploaded_input(
        self,
        name: str,
        df: pd.DataFrame,
        *,
        experiment_name: Optional[str] = None,
    ) -> None:
        """Register an uploaded input frame under ``name`` (the ``bloommcp_input/``
        analogue). Resolves with source ``"uploaded"`` when no raw/cleaned exists."""
        self._uploaded[name] = df.copy()
        self._exp_name.setdefault(name, experiment_name or _stem(name))

    def add_cleaned_version(
        self,
        name: str,
        version_id: str,
        df: pd.DataFrame,
        *,
        make_latest: bool = True,
    ) -> None:
        """Register a cleaned version of ``name`` and optionally mark it latest."""
        self._cleaned.setdefault(name, {})[version_id] = df.copy()
        if make_latest:
            self._latest[name] = version_id

    # --- ExperimentReader --------------------------------------------------

    def load_experiment(
        self,
        name: str,
        *,
        version: str = "latest",
        require_clean: bool = False,
    ) -> ExperimentFrame:
        if version not in ("latest", "raw"):
            cleaned = self._cleaned.get(name, {}).get(version)
            if cleaned is None:
                raise ExperimentNotFoundError(
                    f"Version {version!r} not found for experiment {name!r}."
                )
            return _frame(cleaned, f"{version}_cleaned")

        if version == "latest":
            latest = self._latest.get(name)
            if latest is not None:
                return _frame(self._cleaned[name][latest], f"{latest}_cleaned")

        if require_clean:
            raise CleanedVersionRequiredError(
                f"No cleaned dataset found for {name!r}; run the QC workflow first."
            )

        raw = self._raw.get(name)
        if raw is not None:
            return _frame(raw, "raw")

        uploaded = self._uploaded.get(name)
        if uploaded is not None:
            return _frame(uploaded, "uploaded")

        raise ExperimentNotFoundError(f"Experiment {name!r} not found.")

    def list_experiments(self) -> list[ExperimentSummary]:
        summaries: list[ExperimentSummary] = []
        # Raw first, then uploaded inputs not shadowed by a raw of the same name.
        for name, df in sorted(self._raw.items()):
            summaries.append(self._summary(name, df))
        for name, df in sorted(self._uploaded.items()):
            if name not in self._raw:
                summaries.append(self._summary(name, df))
        return summaries

    def _summary(self, name: str, df: pd.DataFrame) -> ExperimentSummary:
        detected = detect_columns(df)
        return ExperimentSummary(
            filename=name,
            stem=_stem(name),
            rows=len(df),
            total_columns=len(df.columns),
            trait_columns=len(detected["trait_cols"]),
            experiment_name=self._exp_name.get(name, _stem(name)),
            genotype_col=detected["genotype_col"],
            sample_id_col=detected["sample_id_col"],
        )


def _stem(name: str) -> str:
    return Path(name).stem


def _frame(df: pd.DataFrame, source: str) -> ExperimentFrame:
    detected = detect_columns(df)
    return ExperimentFrame(
        df=df,
        trait_cols=detected["trait_cols"],
        metadata_cols=detected["metadata_cols"],
        genotype_col=detected["genotype_col"],
        replicate_col=detected["replicate_col"],
        sample_id_col=detected["sample_id_col"],
        source=source,
    )
