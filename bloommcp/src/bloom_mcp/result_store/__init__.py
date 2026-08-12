"""Result-persistence port (Tier 2) — tools depend on the interface, not Supabase."""

from .fake_store import FakeResultStore
from .ports import (
    CommitFailedError,
    CorruptRunLinksError,
    ManifestIncompatibleError,
    ManifestReadError,
    ResultStore,
    ResultStoreError,
    RunHandle,
    RunNotFoundError,
    RunStateError,
    StoredRun,
)
from .supabase_store import SupabaseResultStore

__all__ = [
    "CommitFailedError",
    "CorruptRunLinksError",
    "FakeResultStore",
    "ManifestIncompatibleError",
    "ManifestReadError",
    "ResultStore",
    "ResultStoreError",
    "RunHandle",
    "RunNotFoundError",
    "RunStateError",
    "StoredRun",
    "SupabaseResultStore",
]
