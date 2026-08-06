"""Long-run-safe Supabase Storage access for the download commands.

A Supabase JWT lapses after ~1hr. Storage answers an expired caller with a 404
``Bucket not found`` — it won't confirm a private bucket exists to someone who
can't read it — so a download that outlives its session fails every remaining
object with a message that reads like missing data (bloom#525: 36,481 frames
downloaded, then 23,855 consecutive "Bucket not found" failures).

``StorageSession`` owns the client and re-authenticates itself: proactively
every ``REFRESH_EVERY`` objects, and reactively on any failure, retrying once.
"""

from __future__ import annotations

import os
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


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes so a crash mid-write can never leave a truncated file at ``path``.

    Load-bearing for resume: a skip-if-already-downloaded check must never skip a
    half-written frame.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def already_downloaded(path: Path) -> bool:
    """True if ``path`` holds a complete frame from an earlier run (resume check)."""
    return path.is_file() and path.stat().st_size > 0


class StorageSession:
    """A Storage bucket handle that renews its own login during a long run.

    Owns the Supabase client, so table reads interleaved with downloads (e.g.
    ``fetch_images`` per scan) run on the renewed session too. Without ``creds``
    it can't re-authenticate and simply behaves like a plain bucket handle.
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
        self.refreshes = 0
        self._bucket: Any = None  # resolved on first download, so constructing a
        # session never touches the client (callers build one before any I/O)

    @property
    def client(self) -> Any:
        """The currently signed-in client — read it fresh, refresh() replaces it."""
        return self._client

    @property
    def can_refresh(self) -> bool:
        return self._creds is not None

    def refresh(self) -> None:
        """Sign in again, replacing the client and the bucket handle."""
        from .. import auth

        self._client = auth.make_authed_client(self._creds)
        self._bucket = None
        self._since_refresh = 0
        self.refreshes += 1

    def run(self, call: Callable[[Any], Any]) -> Any:
        """Run ``call(client)``, re-authenticating and retrying it once on failure."""
        try:
            return call(self._client)
        except Exception as exc:
            if not self.can_refresh:
                raise self._describe(exc) from exc
            try:
                self.refresh()
            except Exception:  # re-auth itself failed: report the original error
                raise self._describe(exc) from exc
            try:
                return call(self._client)
            except Exception as retry_exc:
                raise self._describe(retry_exc) from retry_exc

    def download(self, object_path: str) -> bytes:
        """Download one object's bytes, refreshing before and (if needed) after failure."""
        if self.can_refresh and self._refresh_every and self._since_refresh >= self._refresh_every:
            self.refresh()
        return self.run(lambda _client: self._download_once(object_path))

    def _download_once(self, object_path: str) -> bytes:
        if self._bucket is None:
            self._bucket = self._client.storage.from_(self._bucket_name)
        data = self._bucket.download(object_path)
        if data is None:
            raise ValueError("empty response from storage")
        self._since_refresh += 1
        return data

    @staticmethod
    def _describe(error: BaseException) -> StorageError:
        """Wrap an error, naming the expired-session case the 404 otherwise hides."""
        if looks_like_expired_session(error):
            return StorageError(f"{_EXPIRED_HINT}: {error}")
        return StorageError(str(error))
