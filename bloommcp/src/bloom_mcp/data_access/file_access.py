"""The tables whose rows point to scan images and videos, and read_file's limits.

Claude names a table and row ids; bloommcp reads each row's path itself, so a path never
comes from Claude.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from storage3._sync.file_api import relative_path_to_parts

FileKind = Literal["image", "other"]


@dataclass(frozen=True)
class FileTable:
    path_column: str
    bucket: str
    # Columns returned with each file, so it can be told apart from the others.
    labels: tuple[str, ...]
    # How to find a scan's or experiment's rows on an indexed column.
    find_column: str
    find_hint: str = ""
    id_type: type = int
    # Image tables are converted for Claude; video tables only ever get a link.
    images: bool = True


FILE_TABLES: dict[str, FileTable] = {
    "cyl_images": FileTable(
        path_column="object_path",
        bucket="images",
        labels=("scan_id", "frame_number"),
        find_column="scan_id",
        find_hint="; sort the frames by frame_number yourself",
    ),
    "gravi_images": FileTable(
        path_column="object_path",
        bucket="graviscan-images",
        labels=("scan_id",),
        find_column="scan_id",
    ),
    "cyl_scan_videos": FileTable(
        path_column="path",
        bucket="videos",
        labels=("scan_id",),
        find_column="scan_id",
        id_type=str,
        images=False,
    ),
    "gravi_plate_videos": FileTable(
        path_column="object_path",
        bucket="graviscan-videos",
        labels=("experiment_id", "plate_id", "wave_number"),
        find_column="experiment_id",
        images=False,
    ),
}

MAX_FILES = 8
MAX_PATH_CHARS = 1024
# Larger originals are linked, not downloaded (plate_encode's limit; real plates <= 75 MB).
MAX_SOURCE_IMAGE_BYTES = 192 * 1024**2
# Claude's 5 MiB image limit counts base64, a third larger than the PNG.
MAX_IMAGE_BYTES = 3_900_000
MAX_CALL_IMAGE_BYTES = 15_000_000
SIGNED_URL_SECONDS = 3600

_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "tif", "tiff"})
# Characters a URL parser would decode or cut at, so the bucket could be left.
_UNSAFE = frozenset("%?#:\\")


def find(table: str) -> str:
    source = FILE_TABLES[table]
    return f"filter {source.find_column}{source.find_hint}"


def file_kind(path: str) -> FileKind:
    """image or other, from the path's extension."""
    name = path.rsplit("/", 1)[-1]
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return "image" if extension in _IMAGE_EXTENSIONS else "other"


def path_problem(path: str) -> str | None:
    """Why a stored path can't be read, or None when it stays inside its bucket."""
    if not path or path != path.strip():
        return "is empty or has spaces at an end"
    if len(path) > MAX_PATH_CHARS:
        return f"is longer than {MAX_PATH_CHARS} characters"
    if path.startswith("/") or any(c in _UNSAFE for c in path):
        return "has a character that could leave the bucket"
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "has an empty, '.' or '..' folder"
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        return "has a control character"
    # The storage client must request exactly the folders checked here.
    if tuple(relative_path_to_parts(path)) != tuple(parts):
        return "would be read as a different path"
    return None


def file_note(table: str) -> str | None:
    """How to open the files a table's rows point to, for list_tables."""
    if table not in FILE_TABLES:
        return None
    return (
        f"Each row's {FILE_TABLES[table].path_column} is a file ({find(table)}): open "
        f"it with read_file, passing table {table} and the rows' id values."
    )


def file_guide() -> str:
    """Every table read_file opens, what its files are and how to find its rows."""
    return "\n".join(
        f"- {t}: {s.path_column} in {s.bucket}; {find(t)}"
        for t, s in FILE_TABLES.items()
    )
