"""Checking a genome's FASTA and GTF, and preparing the gzipped form Bloom stores.

Bloom keeps each file gzipped. A plain file is gzipped into a temporary folder, with a fixed
timestamp so the same file always gives the same bytes; a gzipped one is checked end to end
and sent as it is. The SHA-256 and size are of the bytes sent.
"""

from __future__ import annotations

import gzip
import hashlib
import re
import shutil
import zlib
from dataclasses import dataclass
from pathlib import Path

# The genome-references bucket's per-file limit.
MAX_OBJECT_BYTES = 500 * 1024 * 1024

# The database's rule for a genome name; it is also a folder name in storage and on the cluster.
NAME_RULE = re.compile(r"^(?!.*__)[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
NAME_HELP = (
    "1 to 64 letters, digits, '.', '_' or '-', starting with a letter or digit, with no '__'"
)

GZIP_MAGIC = b"\x1f\x8b"
READ_BYTES = 1024 * 1024
# How far into a file the first record is looked for.
HEAD_LINES = 10000
GTF_COLUMNS = 9


class FileProblem(ValueError):
    """A file that is not what its option says it is."""


@dataclass(frozen=True)
class Prepared:
    path: Path
    sha256: str
    size: int


def is_gzipped(path: Path) -> bool:
    with path.open("rb") as fh:
        return fh.read(2) == GZIP_MAGIC


def _open_text(path: Path):
    if is_gzipped(path):
        return gzip.open(path, "rt", errors="replace")
    return path.open("r", errors="replace")


def _first_record(path: Path, skip_comments: bool) -> str | None:
    """The first non-blank line (and, for a GTF, the first that is not a comment)."""
    try:
        with _open_text(path) as fh:
            for _, line in zip(range(HEAD_LINES), fh):
                line = line.rstrip("\r\n")
                if not line.strip() or (skip_comments and line.startswith("#")):
                    continue
                return line
    except (OSError, EOFError, zlib.error) as exc:
        raise FileProblem(f"{path.name} could not be read: {exc}") from exc
    return None


def check_fasta(path: Path) -> None:
    first = _first_record(path, skip_comments=False)
    if first is None or not first.startswith(">"):
        raise FileProblem(f"{path.name} is not a FASTA file: it does not start with a '>' line")


def check_gtf(path: Path) -> None:
    first = _first_record(path, skip_comments=True)
    if first is None:
        raise FileProblem(f"{path.name} is not a GTF file: it has no records")
    columns = first.split("\t")
    if len(columns) != GTF_COLUMNS:
        raise FileProblem(
            f"{path.name} is not a GTF file: its first record has {len(columns)} "
            f"tab-separated columns, not {GTF_COLUMNS}"
        )


def _check_gzip_whole(path: Path) -> None:
    """Read a gzipped file to the end, so a truncated or damaged one is refused before sending."""
    try:
        with gzip.open(path, "rb") as fh:
            while fh.read(READ_BYTES):
                pass
    except (OSError, EOFError, zlib.error) as exc:
        raise FileProblem(f"{path.name} is a damaged gzip file: {exc}") from exc


def _gzip_to(source: Path, dest: Path) -> None:
    with source.open("rb") as src, dest.open("wb") as raw:
        # No name and a fixed time in the header: the same file always gives the same bytes.
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
            shutil.copyfileobj(src, gz, READ_BYTES)


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(READ_BYTES), b""):
            sha.update(block)
    return sha.hexdigest()


def prepare(source: Path, workdir: Path, stored_name: str) -> Prepared:
    """The gzipped form of ``source`` to send, with its SHA-256 and size."""
    if is_gzipped(source):
        _check_gzip_whole(source)
        path = source
    else:
        path = workdir / stored_name
        _gzip_to(source, path)
    size = path.stat().st_size
    if size > MAX_OBJECT_BYTES:
        raise FileProblem(
            f"{source.name} is {size / 1024**2:,.0f} MB gzipped; Bloom stores genome files of "
            f"at most {MAX_OBJECT_BYTES / 1024**2:,.0f} MB"
        )
    if size == 0:
        raise FileProblem(f"{source.name} is empty")
    return Prepared(path=path, sha256=_digest(path), size=size)
