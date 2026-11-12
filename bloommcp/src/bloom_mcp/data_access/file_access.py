"""Which stored files Claude may read, how each kind is returned, and the limits."""

from __future__ import annotations

from typing import Literal

FileKind = Literal["image", "text", "other"]

# Buckets of scan images, videos and single-cell files. Pipeline logs (run-logs) and
# bloommcp's own outputs (bloommcp-data, read through get_download_links) are left out.
READABLE_BUCKETS = frozenset(
    {"images", "graviscan-images", "scrna", "videos", "graviscan-videos"}
)

# The table columns that hold a path in each bucket, for the tool and table notes.
FILE_COLUMNS: dict[tuple[str, str], str] = {
    ("cyl_images", "object_path"): "images",
    ("gravi_images", "object_path"): "graviscan-images",
    ("cyl_scan_videos", "path"): "videos",
    ("gravi_plate_videos", "object_path"): "graviscan-videos",
    ("scrna_counts", "counts_object_path"): "scrna",
    ("scrna_de", "file_path"): "scrna",
    ("scrna_enrichment_runs", "file_path"): "scrna",
}

MAX_PATHS = 8
MAX_PATH_CHARS = 1024
# Images are shrunk to this width by Storage's image service before they are read.
IMAGE_WIDTH = 1000
# Larger originals are linked, not resized.
MAX_SOURCE_IMAGE_BYTES = 50_000_000
MAX_IMAGE_BYTES = 3_000_000
MAX_CALL_IMAGE_BYTES = 12_000_000
# Larger text files are linked, not downloaded.
MAX_TEXT_FILE_BYTES = 20_000_000
SIGNED_URL_SECONDS = 3600

_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "tif", "tiff"})
_TEXT_EXTENSIONS = frozenset({"json", "csv", "tsv", "txt"})
# Image formats Claude can view, by their first bytes.
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def file_kind(path: str) -> FileKind:
    """image, text or other, from the path's extension."""
    name = path.rsplit("/", 1)[-1]
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension in _IMAGE_EXTENSIONS:
        return "image"
    if extension in _TEXT_EXTENSIONS:
        return "text"
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


def image_mime(data: bytes) -> str | None:
    """The image's type when it is one Claude can view, else None."""
    for signature, mime in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def file_note(table: str) -> str | None:
    """How to read the files a table's rows point to, for list_tables."""
    columns = [(c, b) for (t, c), b in FILE_COLUMNS.items() if t == table]
    if not columns:
        return None
    parts = [f"{c} is a file path in the {b} bucket" for c, b in columns]
    return "; ".join(parts) + ": open it with read_file."
