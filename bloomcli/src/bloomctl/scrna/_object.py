"""Where a dataset's h5ad lives in storage, and the gzipped form kept while it uploads."""

from __future__ import annotations

import gzip
import hashlib
import os
import re
from pathlib import Path
from typing import NamedTuple
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
    """Where gzipped forms and upload addresses wait until their object is stored."""
    from ..credentials import default_config_dir

    return default_config_dir() / "scrna-uploads"


def _gz_path(directory: Path, fingerprint: str) -> Path:
    return directory / f"{fingerprint}.h5ad.gz"


def _url_path(directory: Path, fingerprint: str) -> Path:
    return directory / f"{fingerprint}.upload"


def stage(src: Path, directory: Path) -> Staged:
    """Fingerprint ``src`` and gzip it in the same pass, keeping the gzipped form in ``directory``.

    A form already kept for this fingerprint is left as it is: a resumed upload has to send the
    bytes the server already holds part of, and gzip output is not guaranteed to be identical
    on another machine.
    """
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
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
        if kept.exists():
            tmp.unlink()
        else:
            os.replace(tmp, kept)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return Staged(fingerprint, kept, kept.stat().st_size)


def save_upload_url(directory: Path, fingerprint: str, url: str) -> None:
    _url_path(directory, fingerprint).write_text(url)


def load_upload_url(directory: Path, fingerprint: str) -> str | None:
    try:
        return _url_path(directory, fingerprint).read_text().strip() or None
    except FileNotFoundError:
        return None


def clear(directory: Path, fingerprint: str) -> None:
    """Forget an upload: its gzipped form and its address."""
    _gz_path(directory, fingerprint).unlink(missing_ok=True)
    _url_path(directory, fingerprint).unlink(missing_ok=True)
