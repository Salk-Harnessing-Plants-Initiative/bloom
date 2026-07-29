"""Experiment-read port (Tier 2) — tools depend on the interface, not Supabase."""

from .fake_reader import FakeReader
from .local_reader import LocalReader
from .ports import (
    AmbiguousSampleIdentityError,
    AmbiguousSourceSelectionError,
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentNotFoundError,
    ExperimentReader,
    ExperimentReadError,
    ExperimentSummary,
    RawSourced,
    SourceInfo,
    SourceSelectable,
)
from .supabase_reader import SupabaseReader

__all__ = [
    "AmbiguousSampleIdentityError",
    "AmbiguousSourceSelectionError",
    "CleanedVersionRequiredError",
    "ExperimentFrame",
    "ExperimentNotFoundError",
    "ExperimentReadError",
    "ExperimentReader",
    "ExperimentSummary",
    "FakeReader",
    "LocalReader",
    "RawSourced",
    "SourceInfo",
    "SourceSelectable",
    "SupabaseReader",
]
