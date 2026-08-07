"""Long-run-safe Supabase Storage access for the download commands.

A Supabase JWT lapses after ~1hr. Storage answers an expired caller with a 404
``Bucket not found`` — it won't confirm a private bucket exists to someone who
can't read it — so a download that outlives its session fails every remaining
object with a message that reads like missing data (bloom#525: 36,481 frames
downloaded, then 23,855 consecutive "Bucket not found" failures).

``StorageSession`` owns the client and re-authenticates itself: proactively
every ``REFRESH_EVERY`` objects, and reactively on any failure, retrying once.
It is safe to share across the download thread pool (bloom#534) — only one
thread re-authenticates per lapse, the rest pick up the renewed handle.
"""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

IMAGES_BUCKET = "images"

# Objects downloaded between proactive re-authentications during one run.
REFRESH_EVERY = 500

_EXPIRED_MARKERS = ("bucket not found", "jwt expired", "invalid jwt")
_EXPIRED_HINT = (
    "storage request failed, most likely an expired session — Storage reports an "
    "unauthenticated caller as a missing bucket, so this usually means the login "
    "lapsed mid-run, not that the object is gone"
)


class StorageError(RuntimeError):
    """A storage request failed, after any re-authentication and retry."""


def looks_like_expired_session(error: BaseException) -> bool:
    """True for the 404 ``Bucket not found`` Storage returns to an expired caller."""
    text = str(error).lower()
    return any(marker in text for marker in _EXPIRED_MARKERS)


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes so no reader — or later run — can ever see a partial file at ``path``.

    Load-bearing twice over: a resume must never skip a half-written frame, and two
    workers handed the same destination path must never produce a torn file. The temp
    file is unique per call (``mkstemp``) and lands via an atomic ``os.replace``, so
    concurrent writers resolve to one complete file (last writer wins).
    """
    path.parent.mkdir(parents=True, exist_ok=True)  # mkdir(exist_ok) is race-safe
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".dl-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        _unlink_quietly(tmp)  # never leave a temp file behind
        raise


def already_downloaded(path: Path) -> bool:
    """True if ``path`` holds a complete frame from an earlier run (resume check)."""
    return path.is_file() and path.stat().st_size > 0


class StorageSession:
    """A Storage bucket handle that renews its own login during a long run.

    Owns the Supabase client, so table reads interleaved with downloads (e.g.
    ``fetch_images`` per scan) run on the renewed session too. Without ``creds``
    it can't re-authenticate and simply behaves like a plain bucket handle.

    Thread-safe: the client and the refresh bookkeeping live behind a lock, while
    the HTTP round-trip itself runs outside it so workers still overlap. Each
    renewal bumps a generation counter, so several workers failing on the same
    lapse trigger exactly one re-authentication between them.
    """

    def __init__(
        self,
        client: Any,
        creds: Any = None,
        *,
        bucket: str = IMAGES_BUCKET,
        refresh_every: int = REFRESH_EVERY,
    ) -> None:
        self._client = client
        self._creds = creds
        self._bucket_name = bucket
        self._refresh_every = refresh_every
        self._since_refresh = 0
        self._generation = 0
        self.refreshes = 0
        self._bucket: Any = None  # resolved on first download, so constructing a
        # session never touches the client (callers build one before any I/O)
        self._lock = threading.Lock()

    @property
    def client(self) -> Any:
        """The currently signed-in client — read it fresh, a renewal replaces it."""
        with self._lock:
            return self._client

    @property
    def can_refresh(self) -> bool:
        return self._creds is not None

    def refresh(self) -> None:
        """Sign in again, replacing the client and the bucket handle."""
        with self._lock:
            self._reauthenticate()

    def _reauthenticate(self) -> None:
        """Re-sign-in. Caller holds the lock."""
        from .. import auth

        self._client = auth.make_authed_client(self._creds)
        self._bucket = None
        self._since_refresh = 0
        self._generation += 1
        self.refreshes += 1

    def _acquire(self) -> tuple[int, Any]:
        """The (generation, bucket) to use now, re-authenticating first if one is due."""
        with self._lock:
            due = (
                self.can_refresh
                and self._refresh_every
                and self._since_refresh >= self._refresh_every
            )
            if due:
                self._reauthenticate()
            if self._bucket is None:
                self._bucket = self._client.storage.from_(self._bucket_name)
            return self._generation, self._bucket

    def _renew(self, seen_generation: int) -> bool:
        """Re-authenticate unless another thread already did since ``seen_generation``.

        Returns False only if this thread's own re-authentication attempt failed.
        """
        with self._lock:
            if self._generation != seen_generation:
                return True  # another worker already renewed; just retry on theirs
            try:
                self._reauthenticate()
            except Exception:
                return False
            return True

    def run(self, call: Callable[[Any], Any]) -> Any:
        """Run ``call(client)``, re-authenticating and retrying it once on failure."""
        with self._lock:
            generation = self._generation
        try:
            return call(self.client)
        except Exception as exc:
            if not self.can_refresh or not self._renew(generation):
                raise self._describe(exc) from exc
            try:
                return call(self.client)
            except Exception as retry_exc:
                raise self._describe(retry_exc) from retry_exc

    def download(self, object_path: str) -> bytes:
        """Download one object's bytes, refreshing before and (if needed) after failure."""
        generation, bucket = self._acquire()
        try:
            return self._fetch(bucket, object_path)
        except Exception as exc:
            if not self.can_refresh or not self._renew(generation):
                raise self._describe(exc) from exc
            _, bucket = self._acquire()
            try:
                return self._fetch(bucket, object_path)
            except Exception as retry_exc:
                raise self._describe(retry_exc) from retry_exc

    def _fetch(self, bucket: Any, object_path: str) -> bytes:
        data = bucket.download(object_path)
        if data is None:
            raise ValueError("empty response from storage")
        with self._lock:
            self._since_refresh += 1
        return data

    @staticmethod
    def _describe(error: BaseException) -> StorageError:
        """Wrap an error, naming the expired-session case the 404 otherwise hides."""
        if looks_like_expired_session(error):
            return StorageError(f"{_EXPIRED_HINT}: {error}")
        return StorageError(str(error))
