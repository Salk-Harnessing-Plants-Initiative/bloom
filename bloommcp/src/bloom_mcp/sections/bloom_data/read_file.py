"""read_file — open files Bloom stores: images to view, text to read, links for the rest."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bloom_mcp.contract import ToolImage, WithImages, as_mcp_tool
from bloom_mcp.data_access import file_access as fa
from bloom_mcp.data_access import image_convert as ic
from bloom_mcp.data_access.file_ports import FileNotFound, FileReader, FileReadError
from bloom_mcp.data_access.table_access import redact_emails
from bloom_mcp.tools import _ports

from ._common import fix_request, logs_call
from ._page import MAX_REPLY_CHARS

_COLUMNS = "; ".join(f"{t}.{c} ({b})" for (t, c), b in fa.FILE_COLUMNS.items())
PATH_REMEDY = f"Take paths from these columns, with their bucket: {_COLUMNS}."
NOTE = (
    "Images are attached after this JSON, in the order of `files` with kind image, as "
    "lossless PNG. They are for looking; measurements come from the trait tables. Give "
    "the user any link; it works for an hour."
)


# An image too large to show: the user can save the website's preview and upload it.
TOO_LARGE_NOTE = (
    "Too large to show here. Give the user the Bloom page for this scan or plate "
    "(list_tables notes say how to build it) and ask them to save the preview shown "
    "there and upload it to this chat. `url` downloads the full original, e.g. for Fiji."
)


class CropParam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0, description="Left edge, in the original's pixels.")
    y: int = Field(ge=0, description="Top edge, in the original's pixels.")
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class ReadFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket: str = Field(description="Bucket the paths are in.")
    paths: list[str] = Field(
        description=f"Up to {fa.MAX_PATHS} paths in that bucket, from a table's path column."
    )
    crop: CropParam | None = Field(
        default=None,
        description="A region of each image at full resolution, in the original's "
        "pixels (see image.width and image.height from a first read).",
    )
    page: int = Field(default=0, ge=0, description="Page of a multi-page TIFF, from 0.")


class ImageInfo(BaseModel):
    width: int = Field(description="The original's width in pixels.")
    height: int
    mode: str = Field(description="The original's pixel mode, e.g. L, RGB, I;16.")
    pages: int
    shown_width: int = Field(description="Width of the image attached.")
    shown_height: int
    contrast_stretched: bool = Field(
        description="A deeper-than-8-bit image stretched from its darkest to brightest "
        "pixel to be visible; brightness is not comparable between images."
    )


class FileResult(BaseModel):
    path: str
    kind: Literal["image", "text", "link", "error"]
    size_bytes: int | None = None
    text: str | None = Field(default=None, description="A text file's contents.")
    truncated: bool = Field(default=False, description="Text cut at the reply limit.")
    url: str | None = Field(
        default=None, description="Download link to the original, valid for an hour."
    )
    image: ImageInfo | None = None
    note: str | None = None


class ReadFileResult(BaseModel):
    bucket: str
    files: list[FileResult]
    note: str


class _Budget:
    """What is left of the call's text and image allowance."""

    def __init__(self) -> None:
        self.chars = MAX_REPLY_CHARS
        self.image_bytes = fa.MAX_CALL_IMAGE_BYTES
        self.images: list[ToolImage] = []


@as_mcp_tool(input_model=ReadFileParams, output_model=ReadFileResult)
@logs_call
def read_file(params: ReadFileParams) -> ReadFileResult | WithImages:
    """Open files Bloom stores for scans and datasets: see images, read text files."""
    _check(params)
    reader = _ports.file_reader()
    budget = _Budget()
    files = [_read(reader, params, p, budget) for p in params.paths]
    result = ReadFileResult(bucket=params.bucket, files=files, note=NOTE)
    return WithImages(result, budget.images) if budget.images else result


def _check(params: ReadFileParams) -> None:
    if params.bucket not in fa.READABLE_BUCKETS:
        allowed = ", ".join(sorted(fa.READABLE_BUCKETS))
        raise fix_request(
            f"'{params.bucket}' is not a bucket Bloom's data tools can read; they read "
            f"{allowed}.",
            PATH_REMEDY,
        )
    if not 1 <= len(params.paths) <= fa.MAX_PATHS:
        raise fix_request(
            f"Pass 1 to {fa.MAX_PATHS} paths; got {len(params.paths)}.",
            f"Split the paths into calls of up to {fa.MAX_PATHS}.",
        )
    for path in params.paths:
        if problem := fa.path_problem(path):
            raise fix_request(f"The path '{path[:100]}' {problem}.", PATH_REMEDY)


def _read(
    reader: FileReader, params: ReadFileParams, path: str, budget: _Budget
) -> FileResult:
    bucket = params.bucket
    try:
        size = reader.size(bucket, path)
        kind = fa.file_kind(path)
        if kind == "image" and size <= fa.MAX_SOURCE_IMAGE_BYTES:
            return _image(reader, params, path, size, budget)
        if kind == "text" and size <= fa.MAX_TEXT_FILE_BYTES:
            return _text(reader, bucket, path, size, budget)
        notes = {"image": TOO_LARGE_NOTE, "text": "Too large to open here."}
        return _link(reader, bucket, path, size, notes.get(kind))
    except FileNotFound:
        return FileResult(
            path=path, kind="error", note=f"No file at this path in {bucket}."
        )
    except FileReadError:
        return FileResult(
            path=path, kind="error", note="Couldn't read this file; try again."
        )


def _image(
    reader: FileReader, params: ReadFileParams, path: str, size: int, budget: _Budget
) -> FileResult:
    bucket, crop = params.bucket, params.crop
    limit = min(fa.MAX_IMAGE_BYTES, budget.image_bytes)
    try:
        converted = ic.to_png(
            reader.download(bucket, path),
            page=params.page,
            crop=ic.Crop(**crop.model_dump()) if crop else None,
            max_bytes=limit,
        )
    except ic.BadImageRequest as exc:
        return FileResult(path=path, kind="error", size_bytes=size, note=f"{exc}.")
    except ic.ImageTooLarge:
        note = TOO_LARGE_NOTE
        if limit < fa.MAX_IMAGE_BYTES:
            note = "Over this call's image limit; open it in a call of its own."
        return _link(reader, bucket, path, size, note)
    except ic.ImageUnreadable:
        return _link(reader, bucket, path, size, "Not an image that can be shown.")
    budget.image_bytes -= len(converted.png)
    budget.images.append(ToolImage(converted.png, "image/png"))
    info = ImageInfo(
        width=converted.width,
        height=converted.height,
        mode=converted.mode,
        pages=converted.pages,
        shown_width=converted.shown_width,
        shown_height=converted.shown_height,
        contrast_stretched=converted.contrast_stretched,
    )
    url = reader.signed_url(bucket, path, fa.SIGNED_URL_SECONDS)
    return FileResult(path=path, kind="image", size_bytes=size, url=url, image=info)


def _text(
    reader: FileReader, bucket: str, path: str, size: int, budget: _Budget
) -> FileResult:
    if budget.chars <= 0:
        return FileResult(
            path=path,
            kind="error",
            size_bytes=size,
            note="The reply limit was used up; open this path in a call of its own.",
        )
    text = redact_emails(
        reader.download(bucket, path).decode("utf-8", errors="replace")
    )
    truncated = len(text) > budget.chars
    text = text[: budget.chars]
    budget.chars -= len(text)
    return FileResult(
        path=path, kind="text", size_bytes=size, text=text, truncated=truncated
    )


def _link(
    reader: FileReader, bucket: str, path: str, size: int, note: str | None
) -> FileResult:
    url = reader.signed_url(bucket, path, fa.SIGNED_URL_SECONDS)
    return FileResult(path=path, kind="link", size_bytes=size, url=url, note=note)


read_file.__doc__ = f"""Open files Bloom stores for scans and datasets: see images, read text files.

Paths come from these table columns (list_tables notes say the same per table). Read
the rows first, then pass up to {fa.MAX_PATHS} of their paths with the bucket:
{fa.file_guide()}
Example, a scan's frames: query_table cyl_images with scan_id eq the scan, then pass
the object_path values with bucket images.

Images (PNG, JPEG, TIFF, including 16-bit and multi-page) are converted on the server
to lossless PNG, scaled so the longer side is at most {ic.MAX_SIDE} px, the most you
can see. For detail, read again with `crop`: that region comes at the original's full
resolution. JSON, CSV, TSV and TXT come back as text up to the reply limit; videos and
other files come back as a download link with their size. Describe what you see, but
take lengths, areas and counts from the traits, not the pictures.
"""
