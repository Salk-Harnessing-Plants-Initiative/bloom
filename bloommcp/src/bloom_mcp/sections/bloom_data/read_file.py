"""read_file — open scan images Claude can view, and links to scan videos."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bloom_mcp.contract import ToolImage, WithImages, as_mcp_tool
from bloom_mcp.data_access import file_access as fa
from bloom_mcp.data_access import image_convert as ic
from bloom_mcp.data_access import image_slots as slots
from bloom_mcp.data_access.file_ports import FileNotFound, FileReader, FileReadError
from bloom_mcp.tools import _ports

from ._common import fix_request, logs_call

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

_PAGE_OR_UPLOAD = (
    "or give the user the Bloom page for this scan or plate (list_tables notes say how "
    "to build it) to save the preview there and upload it to this chat."
)
BUSY_NOTE = "Bloom is busy with other images: try again in a minute, " + _PAGE_OR_UPLOAD
OUT_OF_TIME_NOTE = (
    "Not converted within this call's time: ask again for this path, " + _PAGE_OR_UPLOAD
)
CALL_LIMIT_NOTE = "Over this call's image limit; open it in a call of its own."


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
    kind: Literal["image", "link", "error"]
    size_bytes: int | None = None
    url: str | None = Field(
        default=None, description="Download link to the original, valid for an hour."
    )
    image: ImageInfo | None = None
    note: str | None = None


class ReadFileResult(BaseModel):
    bucket: str
    files: list[FileResult]
    note: str


@dataclass
class _Read:
    """One path's result, and its PNG when it is an image to attach."""

    file: FileResult
    png: bytes | None = None


@as_mcp_tool(input_model=ReadFileParams, output_model=ReadFileResult)
@logs_call
def read_file(params: ReadFileParams) -> ReadFileResult | WithImages:
    """Open cylinder and plate scan images to view, and get links to scan videos."""
    _check(params)
    reader = _ports.file_reader()
    call_deadline = slots.deadline()
    with ThreadPoolExecutor(max_workers=slots.MAX_IMAGE_SLOTS) as pool:
        reads = list(
            pool.map(lambda p: _read(reader, params, p, call_deadline), params.paths)
        )
    files, images = _attach(reader, params.bucket, reads)
    result = ReadFileResult(bucket=params.bucket, files=files, note=NOTE)
    return WithImages(result, images) if images else result


def _attach(
    reader: FileReader, bucket: str, reads: list[_Read]
) -> tuple[list[FileResult], list[ToolImage]]:
    """Attaches images in path order until the call's image limit; links the rest."""
    left, files, images = fa.MAX_CALL_IMAGE_BYTES, [], []
    for read in reads:
        if read.png is None:
            files.append(read.file)
        elif len(read.png) > left:
            f = read.file
            files.append(_link(reader, bucket, f.path, f.size_bytes, CALL_LIMIT_NOTE))
        else:
            left -= len(read.png)
            images.append(ToolImage(read.png, "image/png"))
            files.append(read.file)
    return files, images


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
    reader: FileReader, params: ReadFileParams, path: str, call_deadline: float
) -> _Read:
    bucket = params.bucket
    try:
        size = reader.size(bucket, path)
        if fa.file_kind(path) != "image":
            return _Read(_link(reader, bucket, path, size, None))
        if size > fa.MAX_SOURCE_IMAGE_BYTES:
            return _Read(_link(reader, bucket, path, size, TOO_LARGE_NOTE))
        try:
            with slots.image_slot(call_deadline):
                return _image(reader, params, path, size)
        except slots.NoSlot as exc:
            note = BUSY_NOTE if exc.reason == "busy" else OUT_OF_TIME_NOTE
            return _Read(_link(reader, bucket, path, size, note))
    except FileNotFound:
        note = f"No file at this path in {bucket}."
        return _Read(FileResult(path=path, kind="error", note=note))
    except FileReadError:
        note = "Couldn't read this file; try again."
        return _Read(FileResult(path=path, kind="error", note=note))


def _image(reader: FileReader, params: ReadFileParams, path: str, size: int) -> _Read:
    bucket, crop = params.bucket, params.crop
    try:
        converted = ic.to_png(
            reader.download(bucket, path),
            page=params.page,
            crop=ic.Crop(**crop.model_dump()) if crop else None,
            max_bytes=fa.MAX_IMAGE_BYTES,
        )
    except ic.BadImageRequest as exc:
        return _Read(
            FileResult(path=path, kind="error", size_bytes=size, note=f"{exc}.")
        )
    except ic.ImageTooLarge:
        return _Read(_link(reader, bucket, path, size, TOO_LARGE_NOTE))
    except ic.ImageUnreadable:
        return _Read(
            _link(reader, bucket, path, size, "Not an image that can be shown.")
        )
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
    file = FileResult(path=path, kind="image", size_bytes=size, url=url, image=info)
    return _Read(file, converted.png)


def _link(
    reader: FileReader, bucket: str, path: str, size: int | None, note: str | None
) -> FileResult:
    url = reader.signed_url(bucket, path, fa.SIGNED_URL_SECONDS)
    return FileResult(path=path, kind="link", size_bytes=size, url=url, note=note)


read_file.__doc__ = f"""Open cylinder and plate scan images to view, and get links to scan videos.

Paths come from these table columns (list_tables notes say the same per table). Read
the rows first, then pass up to {fa.MAX_PATHS} of their paths with the bucket:
{fa.file_guide()}
Example, a scan's frames: query_table cyl_images with scan_id eq the scan, then pass
the object_path values with bucket images.

Images (PNG, JPEG, TIFF, including 16-bit and multi-page) are converted on the server
to lossless PNG, scaled so the longer side is at most {ic.MAX_SIDE} px, the most you
can see. For detail, read again with `crop`: that region comes at the original's full
resolution. Videos come back as a download link with their size. If an image's note
says Bloom is busy, out of time or the image is too large, tell the user and offer the
Bloom page to save the preview and upload it. Describe what you see, but take lengths,
areas and counts from the traits, not the pictures.
"""
