"""Backend-agnostic experiment-read port and its value types.

Tools depend on :class:`ExperimentReader`, never on Supabase or the
``storage`` primitives. The current :class:`SupabaseReader` adapter wraps the
deployed read path; :class:`FakeReader` is the in-memory test double. A future
DB-direct adapter can satisfy the same port by **declaring** column roles for
whatever shape it sources, instead of re-inferring them from a wide frame's
dtypes — so role detection never leaks into callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

import pandas as pd


class ExperimentReadError(Exception):
    """Base for read-port failures, carrying a caller-safe message.

    Adapters MUST NOT leak a filesystem path, bucket name, or storage
    traceback in the message, so the contract layer can surface it without
    exposing backend internals.
    """


class ExperimentNotFoundError(ExperimentReadError):
    """The experiment, or an explicitly requested version, could not be resolved."""


class CleanedVersionRequiredError(ExperimentReadError):
    """``require_clean=True`` was requested but no cleaned version exists."""


@dataclass(frozen=True, eq=False)
class ExperimentFrame:
    """An experiment's data plus its adapter-declared column roles.

    ``source`` records what was resolved: ``"raw"``, ``"legacy_cleaned"``, or a
    ``"v<N>_cleaned"`` label.
    """

    df: pd.DataFrame
    trait_cols: list[str]
    metadata_cols: list[str]
    genotype_col: Optional[str]
    replicate_col: Optional[str]
    sample_id_col: Optional[str]
    source: str


@dataclass(frozen=True)
class ExperimentSummary:
    """One entry returned by :meth:`ExperimentReader.list_experiments`."""

    filename: str
    stem: str
    rows: int
    total_columns: int
    trait_columns: int
    experiment_name: str
    genotype_col: Optional[str]
    sample_id_col: Optional[str]


@runtime_checkable
class ExperimentReader(Protocol):
    """Reads experiment inputs without exposing the backend."""

    def load_experiment(
        self,
        name: str,
        *,
        version: str = "latest",
        require_clean: bool = False,
    ) -> ExperimentFrame:
        """Resolve ``name`` to an :class:`ExperimentFrame`.

        ``version`` is ``"latest"`` (default), ``"raw"``, or an explicit
        ``"v<N>"``. An explicit-version miss raises
        :class:`ExperimentNotFoundError`; a ``"latest"`` miss falls through the
        resolution order to the raw input. ``require_clean=True`` raises
        :class:`CleanedVersionRequiredError` when no cleaned version exists.
        """
        ...

    def list_experiments(self) -> list[ExperimentSummary]:
        """Return the available experiments; an empty list when none exist."""
        ...
