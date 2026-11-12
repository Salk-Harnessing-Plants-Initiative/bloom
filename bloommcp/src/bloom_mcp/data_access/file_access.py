"""Which stored files Claude may read, how each kind is returned, and the limits."""

from __future__ import annotations

from typing import Literal

FileKind = Literal["image", "other"]

# Cylinder and plate scan images and videos. Other buckets (pipeline logs, single-cell
# files, bloommcp's own outputs) are left out.
READABLE_BUCKETS = frozenset(
    {"images", "graviscan-images", "videos", "graviscan-videos"}
)

# The table columns that hold a path in each bucket, for the tool and table notes.
FILE_COLUMNS: dict[tuple[str, str], str] = {
    ("cyl_images", "object_path"): "images",
    ("gravi_images", "object_path"): "graviscan-images",
    ("cyl_scan_videos", "path"): "videos",
    ("gravi_plate_videos", "object_path"): "graviscan-videos",
}

# How to find a table's rows for one item, so its paths can be read.
FILE_FIND: dict[str, str] = {
    "cyl_images": "filter scan_id; sort the frames by frame_number yourself",
    "gravi_images": "filter scan_id",
    "cyl_scan_videos": "filter scan_id",
    "gravi_plate_videos": "filter experiment_id",
}

MAX_PATHS = 8
MAX_PATH_CHARS = 1024
# Larger originals are linked, not downloaded and converted.
MAX_SOURCE_IMAGE_BYTES = 300_000_000
# Claude's limit for one image, and what one call may return in all.
MAX_IMAGE_BYTES = 5_000_000
MAX_CALL_IMAGE_BYTES = 20_000_000
SIGNED_URL_SECONDS = 3600

_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "tif", "tiff"})


def file_kind(path: str) -> FileKind:
    """image or other, from the path's extension."""
    name = path.rsplit("/", 1)[-1]
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension in _IMAGE_EXTENSIONS:
        return "image"
    return "other"


def path_problem(path: str) -> str | None:
    """Why `path` can't be read, or None when it is a plain path inside a bucket."""
    if not path or not path.strip():
        return "is empty"
    if len(path) > MAX_PATH_CHARS:
        return f"is longer than {MAX_PATH_CHARS} characters"
    if path.startswith("/") or "\\" in path:
        return "must be relative to the bucket, with / between folders"
    if any(part in ("", ".", "..") for part in path.split("/")):
        return "has an empty, '.' or '..' folder"
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        return "has a control character"
    return None


def file_note(table: str) -> str | None:
    """How to read the files a table's rows point to, for list_tables."""
    columns = [(c, b) for (t, c), b in FILE_COLUMNS.items() if t == table]
    if not columns:
        return None
    parts = [f"{c} is a file path in the {b} bucket" for c, b in columns]
    return "; ".join(parts) + f" ({FILE_FIND[table]}): open it with read_file."


def file_guide() -> str:
    """Every path column with its bucket and how to find its rows, for read_file."""
    return "\n".join(
        f"- {t}.{c} -> bucket {b}: {FILE_FIND[t]}" for (t, c), b in FILE_COLUMNS.items()
    )
