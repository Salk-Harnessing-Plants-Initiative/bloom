"""Storage access helpers for the download commands.

bloom#525: a long download failed every remaining frame after roughly an hour with a
404 ``Bucket not found``. The cause was *not* the session expiring. ``supabase-py``
refreshes the JWT on its own timer (``auto_refresh_token`` defaults to True) and drops
its cached storage client on ``TOKEN_REFRESHED``, so the next ``client.storage`` access
is rebuilt with the new token.

The bug was hoisting ``client.storage.from_("images")`` *above* the download loop: a
``SyncBucketProxy`` captures the Authorization header at construction, so that one
handle kept presenting the token it was born with while the client refreshed around it.
Resolving the bucket per download is the fix — ``from_()`` is a trivial constructor over
the client's shared httpx session, so it costs nothing per frame.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

IMAGES_BUCKET = "images"

# Mode for downloaded frames. mkstemp creates 0600, which would leave images unreadable
# to collaborators on a shared research directory while scans.csv beside them stays 0644.
FRAME_MODE = 0o644

# Read once at import: os.umask is process-global and momentarily clears the mask, so
# calling it from download workers would race every other thread creating a file.
_UMASK = os.umask(0)
os.umask(_UMASK)

# Storage answers an expired caller with a 404 "Bucket not found" — it won't confirm a
# private bucket exists to someone who can't read it. Retrying once picks up the token
# the client refreshed underneath us.
_EXPIRED_MARKERS = ("bucket not found", "jwt expired", "invalid jwt")
_EXPIRED_HINT = "expired session (Storage reports an unauthenticated caller as a missing bucket)"

# Transient server-side failures worth one retry.
_TRANSIENT_MARKERS = ("500", "502", "503", "504", "timeout", "timed out", "connection")


class StorageError(RuntimeError):
    """A storage request failed, after any retry."""


def looks_like_expired_session(error: BaseException) -> bool:
    """True for the 404 ``Bucket not found`` Storage returns to an expired caller."""
    text = str(error).lower()
    return any(marker in text for marker in _EXPIRED_MARKERS)


def is_retryable(error: BaseException) -> bool:
    """True for failures a second attempt could plausibly fix.

    Deliberately narrow: a genuinely missing object must fail on the first attempt rather
    than doubling the request count across a whole experiment.
    """
    if looks_like_expired_session(error):
        return True
    text = str(error).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def describe_storage_error(error: BaseException) -> StorageError:
    """Wrap an error, naming the expired-session case the 404 otherwise hides."""
    if looks_like_expired_session(error):
        return StorageError(f"{_EXPIRED_HINT}: {error}")
    return StorageError(str(error))


def download_object(client: Any, object_path: str, *, bucket: str = IMAGES_BUCKET) -> bytes:
    """Download one object's bytes, resolving the bucket handle fresh on every call.

    Resolving per call is what keeps a long run working: it picks up the token the client
    refreshed on its own timer (see the module docstring). Retries once on an expired or
    transient failure, which also covers the narrow race where the client's refresh lands
    between resolving the handle and using it.
    """
    try:
        return _fetch(client, bucket, object_path)
    except Exception as exc:
        if not is_retryable(exc):
            raise describe_storage_error(exc) from exc
        try:
            return _fetch(client, bucket, object_path)
        except Exception as retry_exc:
            raise describe_storage_error(retry_exc) from retry_exc


def _fetch(client: Any, bucket: str, object_path: str) -> bytes:
    data = client.storage.from_(bucket).download(object_path)
    if data is None:
        raise ValueError("empty response from storage")
    return data


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def atomic_write_bytes(path: Path, data: bytes, *, mode: int = FRAME_MODE) -> None:
    """Write bytes so no reader — or later run — can ever see a partial file at ``path``.

    Load-bearing twice over: a resume must never skip a half-written frame, and two workers
    handed the same destination must never produce a torn file. The temp file is unique per
    call (``mkstemp``) and lands via an atomic ``os.replace``.

    ``mode`` is applied explicitly because ``mkstemp`` creates 0600 and ``os.replace``
    preserves it, which would otherwise make frames owner-only.
    """
    path.parent.mkdir(parents=True, exist_ok=True)  # mkdir(exist_ok) is race-safe
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".dl-", suffix=".tmp")
    try:
        try:
            handle = os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)  # fdopen never took ownership of the fd, so close it here
            raise
        with handle:  # owns the fd from here, closes it on exit
            handle.write(data)
        os.chmod(tmp, mode & ~_UMASK)
        os.replace(tmp, path)
    except BaseException:
        _unlink_quietly(tmp)  # never leave a temp file behind
        raise


def sweep_orphan_temps(out_dir: Path) -> int:
    """Remove `.dl-*.tmp` files left behind by a killed run; return how many.

    `atomic_write_bytes` cleans up its own temp on a caught error, but SIGKILL or power loss
    skips that handler, leaving stray files in the output directory forever.
    """
    images_root = Path(out_dir) / "images"
    if not images_root.exists():
        return 0
    removed = 0
    for tmp in images_root.rglob(".dl-*.tmp"):
        _unlink_quietly(str(tmp))
        removed += 1
    return removed


def already_downloaded(path: Path, expected_size: int | None = None) -> bool:
    """True if ``path`` holds a complete frame from an earlier run (resume check).

    With ``expected_size`` this is a real completeness check. Without it the best that can
    be said is "non-empty" — which does not detect a frame truncated by a pre-0.1.0b1
    release, since those wrote frames non-atomically (see `bloomctl cyl download --overwrite`).
    """
    if not path.is_file():
        return False
    size = path.stat().st_size
    if expected_size is not None:
        return size == expected_size
    return size > 0
