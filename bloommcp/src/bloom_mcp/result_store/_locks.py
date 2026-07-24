"""Shared per-key mutual exclusion for the two `ResultStore` adapters.

Both `SupabaseResultStore.commit` and `FakeResultStore.commit` must
serialize commits for the same manifest key — see either adapter's module
docstring for why (FastMCP dispatches sync tool handlers via a thread pool,
so two commits for the same key can genuinely race within one process). A
single shared implementation means the two adapters can't silently drift on
locking/eviction semantics the way two independently hand-rolled copies
could — the same risk already flagged for `_MAX_ID_ATTEMPTS` and the
two-phase collision check, which remain intentionally independent because
they're one-line/simple; this one is not, so it lives here instead.
"""

from __future__ import annotations

import threading
from typing import Hashable, Optional


class _Entry:
    __slots__ = ("lock", "refcount")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.refcount = 0


class KeyedLock:
    """A context manager granting exclusive access to one hashable `key`.

    Bounded by design: a key has a registry entry only while at least one
    caller is inside its `with` block (holding or waiting to acquire the
    underlying lock) for that key — never for the lifetime of the process.
    Registry size is therefore bounded by the number of *currently
    in-flight, distinct* keys, not the number ever seen, so a long-running
    process seeing many distinct experiments/tool_classes over its lifetime
    does not grow this registry without bound.

    Callers should namespace `key` per adapter (e.g. prefix with a literal
    tag) if two adapters might otherwise construct an identical key for
    logically independent resources — the registry is shared process-wide.
    """

    _guard = threading.Lock()
    _entries: dict[Hashable, _Entry] = {}

    def __init__(self, key: Hashable) -> None:
        self._key = key
        self._entry: Optional[_Entry] = None

    def __enter__(self) -> "KeyedLock":
        with KeyedLock._guard:
            entry = KeyedLock._entries.get(self._key)
            if entry is None:
                entry = _Entry()
                KeyedLock._entries[self._key] = entry
            entry.refcount += 1
        self._entry = entry
        entry.lock.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        entry = self._entry
        assert entry is not None, "KeyedLock.__exit__ called without __enter__"
        entry.lock.release()
        with KeyedLock._guard:
            entry.refcount -= 1
            if entry.refcount == 0 and KeyedLock._entries.get(self._key) is entry:
                del KeyedLock._entries[self._key]
