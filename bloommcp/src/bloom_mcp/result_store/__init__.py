"""Result-persistence port (Tier 2) — tools depend on the interface, not Supabase."""

from .fake_store import FakeResultStore
from .ports import (
    CatalogBackendMismatchError,
    CommitFailedError,
    CorruptRunLinksError,
    ManifestIncompatibleError,
    ManifestReadError,
    OutputFileMissingError,
    ResultStore,
    ResultStoreError,
    RunHandle,
    RunNotFoundError,
    RunStateError,
    StoredRun,
)
from .supabase_store import SupabaseResultStore

__all__ = [
    "CatalogBackendMismatchError",
    "CommitFailedError",
    "CorruptRunLinksError",
    "FakeResultStore",
    "ManifestIncompatibleError",
    "ManifestReadError",
    "OutputFileMissingError",
    "ResultStore",
    "ResultStoreError",
    "RunHandle",
    "RunNotFoundError",
    "RunStateError",
    "StoredRun",
    "SupabaseResultStore",
]
