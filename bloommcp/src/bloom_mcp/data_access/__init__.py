"""Experiment-read port (Tier 2) — tools depend on the interface, not Supabase."""

from .fake_reader import FakeReader
from .local_reader import LocalReader
from .ports import (
    AmbiguousRunIdError,
    AmbiguousSampleIdentityError,
    AmbiguousSourceSelectionError,
    CleanedVersionRequiredError,
    DuplicateTraitReadingError,
    ExperimentFrame,
    ExperimentNotFoundError,
    ExperimentReader,
    ExperimentReadError,
    ExperimentSummary,
    MultipleScansPerPlantError,
    RawSourced,
    SourceInfo,
    SourcePinningUnsupportedError,
    SourcePinNotFoundError,
    SourceSelectable,
)
from .supabase_reader import SupabaseReader

__all__ = [
    "AmbiguousRunIdError",
    "AmbiguousSampleIdentityError",
    "AmbiguousSourceSelectionError",
    "CleanedVersionRequiredError",
    "DuplicateTraitReadingError",
    "ExperimentFrame",
    "ExperimentNotFoundError",
    "ExperimentReadError",
    "ExperimentReader",
    "ExperimentSummary",
    "FakeReader",
    "LocalReader",
    "MultipleScansPerPlantError",
    "RawSourced",
    "SourceInfo",
    "SourcePinningUnsupportedError",
    "SourcePinNotFoundError",
    "SourceSelectable",
    "SupabaseReader",
]
