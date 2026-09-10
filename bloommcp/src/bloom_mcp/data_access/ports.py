"""Backend-agnostic experiment-read port and its value types.

Tools depend on :class:`ExperimentReader`, never on Supabase or the
``storage`` primitives. :class:`SupabaseReader` is the deployed DB-direct
adapter; :class:`FakeReader` is the in-memory test double. An adapter
satisfies the port by **declaring** column roles for whatever shape it
sources, instead of re-inferring them from a wide frame's dtypes — so role
detection never leaks into callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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


class SourcePinNotFoundError(ExperimentReadError):
    """The experiment exists, but a given ``source_id``/``run_id`` pin matched nothing.

    Distinct from :class:`ExperimentNotFoundError` (the experiment itself does
    not exist) so a caller can tell "wrong experiment" from "right experiment,
    stale/wrong pin" programmatically rather than catching one exception for
    both.
    """


class CleanedVersionRequiredError(ExperimentReadError):
    """``require_clean=True`` was requested but no cleaned version exists."""


class AmbiguousSourceSelectionError(ExperimentReadError):
    """Both ``source_id`` and ``run_id`` were given; the DB read surface rejects that."""


class SourcePinningUnsupportedError(ExperimentReadError):
    """A non-``None`` ``source_id``/``run_id`` was given to an adapter with no source concept.

    Distinct from :class:`AmbiguousSourceSelectionError` (a pin that could apply
    but conflicts with another pin) and :class:`SourcePinNotFoundError` (a pin
    that could apply but matches nothing) — this adapter (:class:`LocalReader`,
    :class:`FakeReader`) has no source-versioned substrate at all, so any
    non-``None`` pin is rejected outright rather than silently ignored.
    """


class AmbiguousSampleIdentityError(ExperimentReadError):
    """A pivoted frame would carry a ``sample_id`` shared by more than one plant."""


class MultipleScansPerPlantError(ExperimentReadError):
    """A resolved source has more than one scan for the same plant.

    The raw-tier pivot keys one row per plant within a single resolved source;
    more than one ``scan_id`` for the same plant has no defined column layout
    (multi-scan pivoting is not yet supported).
    """


class AmbiguousRunIdError(ExperimentReadError):
    """A ``run_id`` pin matches more than one source.

    ``pipeline_run_id`` carries no DB uniqueness constraint (only
    ``idempotency_key`` is enforced), so a caller's ``run_id`` pin is not
    guaranteed to resolve to exactly one source. Raised rather than silently
    picking one of the matches.
    """


class DuplicateTraitReadingError(ExperimentReadError):
    """A resolved source has more than one trait value for the same plant+trait.

    ``cyl_scan_traits`` carries no constraint preventing this. The raw-tier
    pivot refuses to silently keep an arbitrary one of the duplicates.
    """


@dataclass(frozen=True, eq=False)
class ExperimentFrame:
    """An experiment's data plus its adapter-declared column roles.

    ``source`` records what was resolved: ``"raw"``, ``"legacy_cleaned"``, or a
    ``"v<N>_cleaned"`` label. ``resolved_source`` is the concrete DB source
    (see :class:`SourceSelectable`) actually consulted for a raw read — always
    ``None`` for a cleaned-tier read (no DB source touched for that read) or an
    adapter with no source-versioned substrate. A caller that stamps
    provenance at commit time SHOULD use this value rather than independently
    re-resolving "the current latest source": for a cleaned-tier read this
    frame's data never touched the DB at all, and even for a raw read, a fresh
    re-resolution at commit time can race ahead of what this frame's data
    actually is (see ``ResultStore.create_run``'s ``source`` parameter).

    ``available_source_count`` is the number of distinct sources ``list_sources``
    would return for this experiment, captured from the SAME resolution
    ``load_experiment`` already performed to produce ``resolved_source`` — never
    a fresh, independent ``list_sources`` call. A caller that wants to tell an
    agent "there was more than one source to choose from" should read this
    field rather than re-querying: a second call would be both a redundant DB
    round-trip and a TOCTOU window (the count could change between the two
    reads). ``None`` for a cleaned-tier read or an adapter with no source
    concept, same as ``resolved_source``.
    """

    df: pd.DataFrame
    trait_cols: list[str]
    metadata_cols: list[str]
    genotype_col: Optional[str]
    replicate_col: Optional[str]
    sample_id_col: Optional[str]
    source: str
    resolved_source: Optional["SourceInfo"] = None
    available_source_count: Optional[int] = None


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
    # `trait_columns`'s freshness -- bloom#637: it's read from a cache (design.md D5), not
    # computed live. Production refreshes automatically on a daily schedule (design.md D8
    # addendum, bloom#708); staging remains on-demand (dispatch) only. So it can lag behind
    # the actual trait data -- bounded to roughly one refresh interval on production, unbounded
    # on staging until someone dispatches a refresh. ISO-8601 string (as returned by
    # PostgREST), or `None` if never refreshed yet, or if this row came from a live (pinned)
    # call that doesn't use the cache at all.
    trait_columns_updated_at: Optional[str] = None


@runtime_checkable
class ExperimentReader(Protocol):
    """Reads experiment inputs without exposing the backend."""

    def load_experiment(
        self,
        name: str,
        *,
        version: str = "latest",
        require_clean: bool = False,
        source_id: Optional[int] = None,
        run_id: Optional[str] = None,
    ) -> ExperimentFrame:
        """Resolve ``name`` to an :class:`ExperimentFrame`.

        ``version`` is ``"latest"`` (default — prefers an outlier trim over a
        plain clean whenever one exists for the experiment), ``"latest_qc"``
        (the plain-clean tier specifically, ignoring any trim — what
        ``remove_outliers`` reads as its trimming input), ``"raw"``, or an
        explicit ``"v<N>"``. An explicit version checks every cleaned tool
        class (``qc`` and ``outliers`` each have their own independently-numbered
        ``v<N>`` sequence): it resolves whichever single class has that id,
        refuses as ambiguous if more than one does, and reports not-found if
        none does — never silently prefers ``qc`` the way an earlier revision of
        this adapter did (bloom#644). An explicit-version miss raises
        :class:`ExperimentNotFoundError`; a ``"latest"``/``"latest_qc"`` miss
        falls through the resolution order to the raw input.
        ``require_clean=True`` raises :class:`CleanedVersionRequiredError` when
        no cleaned version exists.

        ``source_id``/``run_id`` optionally pin which raw DB source/pipeline-run
        backs the read — meaningful only against the raw tier. Every adapter
        MUST accept both kwargs: an adapter backed by a source-versioned
        substrate (see :class:`SourceSelectable`) honors a non-``None`` pin, or
        raises :class:`AmbiguousSourceSelectionError` when both are given, or
        :class:`SourcePinNotFoundError` when a pin matches nothing. An adapter
        with no source concept MUST raise :class:`SourcePinningUnsupportedError`
        immediately when either is non-``None``, rather than silently ignoring
        the pin.
        """
        ...

    def list_experiments(self) -> list[ExperimentSummary]:
        """Return the available experiments; an empty list when none exist."""
        ...


@runtime_checkable
class RawSourced(Protocol):
    """Optional adapter capability: a concrete on-disk raw input path.

    Adapters backed by a real raw CSV on local disk (:class:`LocalReader`) expose
    the source path so a run can content-address its input (a non-empty
    ``input_sha256``). Adapters without one — a path-less adapter like
    ``FakeReader``, or a DB-backed raw tier like :class:`SupabaseReader` (see
    :class:`SourceSelectable` instead) — simply do not implement it; callers gate
    on ``isinstance(reader, RawSourced)`` rather than a duck-typed attribute
    lookup, so the capability is discoverable and type-checked.
    """

    def raw_source_path(self, name: str) -> Optional[Path]:
        """The on-disk raw CSV for ``name``, or ``None`` when absent."""
        ...


@dataclass(frozen=True)
class SourceInfo:
    """One database source/pipeline-run backing a source-versioned raw read."""

    source_id: int
    source_name: Optional[str]
    pipeline_run_id: Optional[str]


@runtime_checkable
class SourceSelectable(Protocol):
    """Optional adapter capability: explicit source/run pinning for a raw read.

    Mirrors :class:`RawSourced`'s isinstance-gated shape. Adapters backed by a
    source/run-versioned substrate (:class:`SupabaseReader`) implement this;
    adapters without one (``FakeReader``, ``LocalReader``) do not.
    """

    def list_sources(self, name: str) -> list[SourceInfo]:
        """Enumerate the distinct sources/runs contributing data to ``name``."""
        ...

    def resolve_source(
        self,
        name: str,
        *,
        source_id: Optional[int] = None,
        run_id: Optional[str] = None,
    ) -> Optional[SourceInfo]:
        """Resolve which source backs a read of ``name``, honoring an explicit pin.

        Returns ``None`` when ``name`` has only legacy, pre-source-tracking data
        (no ``source_id`` to report) — a normal state, not an error.
        """
        ...
