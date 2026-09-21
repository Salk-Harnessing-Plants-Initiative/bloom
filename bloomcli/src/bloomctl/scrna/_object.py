"""Where a dataset's h5ad lives in storage, and the gzipped form kept while it uploads."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, NamedTuple
from uuid import uuid4

BUCKET = "scrna"
FOLDER = "h5ad"

# The storage service's per-file limit (FILE_SIZE_LIMIT in both compose files).
MAX_OBJECT_BYTES = 500 * 1024 * 1024

HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"
READ_BYTES = 1024 * 1024

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


class Staged(NamedTuple):
    fingerprint: str
    gz_path: Path
    size: int


def object_path(fingerprint: str) -> str:
    """The object's path inside the bucket: `h5ad/{sha256}.h5ad.gz`."""
    if not _FINGERPRINT.match(fingerprint):
        raise ValueError(f"not a SHA-256 fingerprint: {fingerprint!r}")
    return f"{FOLDER}/{fingerprint}.h5ad.gz"


def is_hdf5(path: Path) -> bool:
    with path.open("rb") as fh:
        return fh.read(len(HDF5_SIGNATURE)) == HDF5_SIGNATURE


def fingerprint_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(READ_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def staging_dir() -> Path:
    """Where gzipped forms and upload records wait until their object is stored."""
    from ..credentials import default_config_dir

    return default_config_dir() / "scrna-uploads"


def _gz_path(directory: Path, fingerprint: str) -> Path:
    return directory / f"{fingerprint}.h5ad.gz"


def _kept_path(directory: Path, fingerprint: str) -> Path:
    return directory / f"{fingerprint}.gz.json"


def _upload_path(directory: Path, fingerprint: str) -> Path:
    return directory / f"{fingerprint}.upload"


def _read_json(path: Path) -> dict[str, Any] | None:
    """A record written here, or None when it is absent or no longer readable."""
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _write_json(path: Path, record: dict[str, Any]) -> None:
    """Written through a temp file, so a reader never sees half a record."""
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(record))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _digest_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(READ_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_the_form_it_claims(directory: Path, fingerprint: str) -> bool:
    """Whether the kept gzipped form is still the one written for this fingerprint.

    It is reused rather than rewritten, and an upload resumed against it can never be
    replaced once stored — so its own digest is checked before a byte of it is sent.
    """
    gz_path = _gz_path(directory, fingerprint)
    record = _read_json(_kept_path(directory, fingerprint))
    if record is None or not gz_path.is_file():
        return False
    try:
        if gz_path.stat().st_size != record.get("size"):
            return False
    except OSError:
        return False
    return _digest_of(gz_path) == record.get("gz_sha256")


def stage(src: Path, directory: Path) -> Staged:
    """Fingerprint ``src`` and gzip it in the same pass, keeping the gzipped form in ``directory``.

    A form kept from an interrupted run is reused when it is still exactly what was written for
    that fingerprint: a resumed upload has to send the bytes the server already holds part of,
    and gzip output is not guaranteed to be identical on another machine. One that no longer
    checks out is rewritten.
    """
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    tmp = directory / f".stage-{uuid4().hex}.tmp"
    digest = hashlib.sha256()
    try:
        with src.open("rb") as fh, tmp.open("wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
                for block in iter(lambda: fh.read(READ_BYTES), b""):
                    digest.update(block)
                    gz.write(block)
        fingerprint = digest.hexdigest()
        kept = _gz_path(directory, fingerprint)
        if _is_the_form_it_claims(directory, fingerprint):
            tmp.unlink()
        else:
            size = tmp.stat().st_size
            gz_sha256 = _digest_of(tmp)
            os.replace(tmp, kept)
            os.chmod(kept, 0o600)
            _write_json(_kept_path(directory, fingerprint), {"size": size, "gz_sha256": gz_sha256})
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return Staged(fingerprint, kept, kept.stat().st_size)


def save_upload(directory: Path, fingerprint: str, upload_id: str, size: int, api_url: str) -> None:
    """Remember an upload in progress: which one, for how many bytes, on which server."""
    _write_json(
        _upload_path(directory, fingerprint),
        {"id": upload_id, "size": size, "api_url": api_url},
    )
    os.chmod(_upload_path(directory, fingerprint), 0o600)


def load_upload(
    directory: Path,
    fingerprint: str,
    *,
    api_url: str | None = None,
    size: int | None = None,
) -> dict[str, Any] | None:
    """An upload worth resuming, or None.

    A record for another server is never offered: resuming it would send this session's token
    there. Neither is one for a different number of bytes — the gzipped form was rewritten, so
    the server's upload is for other bytes and could never be completed.
    """
    record = _read_json(_upload_path(directory, fingerprint))
    if record is None or not record.get("id"):
        return None
    if api_url is not None and record.get("api_url") != api_url:
        return None
    if size is not None and record.get("size") != size:
        return None
    return record


def forget_upload(directory: Path, fingerprint: str) -> None:
    """Forget the server's upload but keep the gzipped form: the next run starts a fresh one.

    An upload the server holds in full but never turned into an object cannot be finished --
    the protocol will not re-finalise it -- so keeping its record repeats that failure forever.
    """
    _upload_path(directory, fingerprint).unlink(missing_ok=True)


def clear(directory: Path, fingerprint: str) -> None:
    """Forget an upload: its gzipped form, what identified it, and its address."""
    for path in (
        _gz_path(directory, fingerprint),
        _kept_path(directory, fingerprint),
        _upload_path(directory, fingerprint),
    ):
        path.unlink(missing_ok=True)
