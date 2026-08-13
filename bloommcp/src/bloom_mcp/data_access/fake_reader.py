"""In-memory :class:`ExperimentReader` for tests — no Supabase, no filesystem."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from bloom_mcp.experiment_utils import detect_columns

from .ports import (
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentNotFoundError,
    ExperimentReadError,
    ExperimentSummary,
    SourcePinningUnsupportedError,
)


class FakeReader:
    """Seed raw frames and (optionally) cleaned versions, then read them back.

    Mirrors :class:`SupabaseReader`'s observable behaviour — same resolution
    order, same not-found / clean-required signalling, same declared roles
    (via :func:`detect_columns`) — so a single scenario set can be run against
    both as a parity check. Also exposes a test-only failure-injection hook
    (`fail_next_load`), mirroring `FakeResultStore.fail_next_commit`, so a
    mid-read storage failure -- the one hazard class a flat in-memory lookup
    cannot otherwise represent -- is exercisable with no live Supabase adapter.
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
        # One-shot: (name, version) -> raise ExperimentReadError on the next
        # matching load_experiment() call, then clear itself.
        self._fail_next: set[tuple[str, str]] = set()

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

    # --- Test-only failure injection ----------------------------------------

    def fail_next_load(self, name: str, *, version: str = "latest") -> None:
        """The next `load_experiment(name, version=version)` call raises
        `ExperimentReadError` once, then clears itself.

        Mirrors `FakeResultStore.fail_next_commit`'s one-shot pattern —
        simulates a mid-read storage failure (the hazard class this fake's
        flat dict lookup otherwise cannot represent at all).
        """
        self._fail_next.add((name, version))

    # --- ExperimentReader --------------------------------------------------

    def load_experiment(
        self,
        name: str,
        *,
        version: str = "latest",
        require_clean: bool = False,
        source_id: Optional[int] = None,
        run_id: Optional[str] = None,
    ) -> ExperimentFrame:
        if source_id is not None or run_id is not None:
            # FakeReader has no source-versioned substrate — reject outright
            # rather than silently ignoring the pin (#626). The message names
            # no internal adapter/class, mirroring list_experiment_sources's
            # own care not to leak backend implementation detail.
            raise SourcePinningUnsupportedError(
                "This backend has no source-versioned raw data to pin "
                "against; source_id/run_id pinning is not supported here."
            )
        key = (name, version)
        if key in self._fail_next:
            self._fail_next.discard(key)
            raise ExperimentReadError(
                f"Simulated read failure for {name!r} (version={version!r})."
            )

        # FakeReader has no notion of tool classes and cannot model the
        # qc/outliers priority split (it has no manifests at all) — treat
        # "latest_qc" as an alias for "latest" so callers that switch to it
        # (remove_outliers's own input read) still resolve against the one
        # flat `self._cleaned` map. The outliers-preferring cross-manifest
        # behavior is proved against the real Supabase adapters instead.
        if version == "latest_qc":
            version = "latest"

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
        if raw is None:
            raise ExperimentNotFoundError(f"Experiment {name!r} not found.")
        return _frame(raw, "raw")

    def list_experiments(self) -> list[ExperimentSummary]:
        summaries: list[ExperimentSummary] = []
        for name, df in sorted(self._raw.items()):
            detected = detect_columns(df)
            summaries.append(
                ExperimentSummary(
                    filename=name,
                    stem=_stem(name),
                    rows=len(df),
                    total_columns=len(df.columns),
                    trait_columns=len(detected["trait_cols"]),
                    experiment_name=self._exp_name.get(name, _stem(name)),
                    genotype_col=detected["genotype_col"],
                    sample_id_col=detected["sample_id_col"],
                )
            )
        return summaries


def _stem(name: str) -> str:
    return name[:-4] if name.endswith(".csv") else name


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
