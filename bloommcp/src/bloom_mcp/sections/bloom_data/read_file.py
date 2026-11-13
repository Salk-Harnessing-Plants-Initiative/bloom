"""read_file — scan images Claude can view, and links to scan videos, by table row."""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from bloom_mcp.contract import ToolImage, WithImages, as_mcp_tool
from bloom_mcp.data_access import file_access as fa
from bloom_mcp.data_access import image_convert as ic
from bloom_mcp.data_access import image_slots as slots
from bloom_mcp.data_access.file_ports import FileNotFound, FileReader, FileReadError
from bloom_mcp.data_access.table_ports import Filter, Sort
from bloom_mcp.tools import _ports

from ._common import fix_request, logs_call, reports_read_errors

logger = logging.getLogger(__name__)

_UUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
_TABLES = ", ".join(fa.FILE_TABLES)
NOTE = (
    "Each image is attached after this JSON, preceded by a line naming its table, id "
    "and labels. Images are for looking; measurements come from the trait tables. "
    "Give the user any link; it works for an hour."
)
_PAGE_OR_UPLOAD = (
    "or give the user the scan's Bloom page (cyl_scans_extended's page link, or the "
    "plate experiment's) to save the preview there and upload it to this chat."
)
TOO_LARGE_NOTE = "Too large to show here: " + _PAGE_OR_UPLOAD
BUSY_NOTE = "Bloom is busy with other images: try again in a minute, " + _PAGE_OR_UPLOAD
OUT_OF_TIME_NOTE = (
    "Not read within this call's time: ask again for this id, " + _PAGE_OR_UPLOAD
)
CALL_LIMIT_NOTE = "Over this call's image limit; open it in a call of its own."
READ_FAILED_NOTE = "Couldn't read this file; try again."


class CropParam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0, description="Left edge, in the original's pixels.")
    y: int = Field(ge=0, description="Top edge, in the original's pixels.")
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class ReadFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str = Field(description=f"One of {_TABLES}.")
    ids: list[StrictInt | StrictStr] = Field(
        description=f"Up to {fa.MAX_FILES} of that table's row ids, from query_table."
    )
    crop: CropParam | None = Field(
        default=None,
        description="A region of each image at full resolution, in the original's "
        "pixels (image.width and image.height from a first read).",
    )
    page: int = Field(default=0, ge=0, description="Page of a multi-page TIFF, from 0.")


class ImageInfo(BaseModel):
    width: int = Field(description="The original page's width in pixels.")
    height: int
    mode: str = Field(description="The original's pixel mode, e.g. L, RGB, I;16.")
    more_pages: bool = Field(description="The file has a page after this one.")
    shown_width: int = Field(description="Width of the image attached.")
    shown_height: int
    full_scale: int | None = Field(
        description="For a deeper-than-8-bit image, the value shown as white (black is "
        "0), the same for every image of that depth; null for 8-bit images."
    )
    dim: bool = Field(description="A deep image far below its full scale: shown dark.")


class FileResult(BaseModel):
    id: int | str
    labels: dict[str, Any] = Field(
        default_factory=dict, description="The row's scan, frame or plate."
    )
    kind: Literal["image", "link", "error"]
    size_bytes: int | None = None
    url: str | None = Field(
        default=None, description="Download link to the original, valid for an hour."
    )
    image: ImageInfo | None = None
    note: str | None = None


class ReadFileResult(BaseModel):
    table: str
    files: list[FileResult]
    note: str


@dataclass
class _Work:
    """One row's file to read."""

    id: int | str
    labels: dict[str, Any]
    path: str
    bucket: str


@dataclass
class _Read:
    file: FileResult
    image: ToolImage | None = None


class _Call:
    def __init__(self, params: ReadFileParams) -> None:
        self.params = params
        self.reader: FileReader = _ports.file_reader()
        self.deadline = slots.deadline()
        self.image_bytes = fa.MAX_CALL_IMAGE_BYTES
        self.lock = threading.Lock()

    def late(self) -> bool:
        return time.monotonic() >= self.deadline


@as_mcp_tool(input_model=ReadFileParams, output_model=ReadFileResult)
@logs_call
@reports_read_errors
def read_file(params: ReadFileParams) -> ReadFileResult | WithImages:
    """Open cylinder and plate scan images to view, and get links to scan videos."""
    source = _check(params)
    ids = list(dict.fromkeys(params.ids))
    rows = _rows(params.table, source, ids)
    call = _Call(params)
    reads: list[_Read | None] = []
    work: list[tuple[int, _Work]] = []
    for row_id in ids:
        found = _work(source, rows.get(row_id), row_id)
        reads.append(found if isinstance(found, _Read) else None)
        if isinstance(found, _Work):
            work.append((len(reads) - 1, found))
    with ThreadPoolExecutor(max_workers=slots.MAX_IMAGE_SLOTS) as pool:
        done = pool.map(lambda w: _read(call, w), [w for _, w in work])
        for (index, _), read in zip(work, done):
            reads[index] = read
    files = [r.file for r in reads if r]
    images = [r.image for r in reads if r and r.image]
    result = ReadFileResult(table=params.table, files=files, note=NOTE)
    return WithImages(result, images) if images else result


def _check(params: ReadFileParams) -> fa.FileTable:
    source = fa.FILE_TABLES.get(params.table)
    if source is None:
        raise fix_request(
            f"read_file opens files from {_TABLES}, not '{params.table}'.",
            "Read that table's rows with query_table, then pass their id values.",
        )
    if not 1 <= len(params.ids) <= fa.MAX_FILES:
        raise fix_request(
            f"Pass 1 to {fa.MAX_FILES} ids; got {len(params.ids)}.",
            f"Split the ids into calls of up to {fa.MAX_FILES}.",
        )
    params.ids = [_as_id(i, source.id_type) for i in params.ids]
    for row_id in params.ids:
        if not _valid_id(row_id, source.id_type):
            kind = "a uuid" if source.id_type is str else "a whole number"
            raise fix_request(
                f"{params.table} ids are {kind}; got {str(row_id)[:60]!r}.",
                "Pass the id column's values from query_table.",
            )
    return source


def _as_id(row_id: int | str, id_type: type) -> int | str:
    """A whole-number id passed as text, as Claude sometimes does, read as a number."""
    if id_type is int and isinstance(row_id, str) and row_id.isdigit():
        return int(row_id)
    return row_id


def _valid_id(row_id: int | str, id_type: type) -> bool:
    if id_type is str:
        return isinstance(row_id, str) and bool(_UUID.match(row_id))
    return isinstance(row_id, int) and not isinstance(row_id, bool)


def _rows(table: str, source: fa.FileTable, ids: list) -> dict[Any, dict[str, Any]]:
    columns = ["id", source.path_column, *source.labels]
    rows = _ports.table_reader().select(
        table, columns, [Filter("id", "in", ids)], [Sort("id")], len(ids), 0
    )
    return {r["id"]: r for r in rows}


def _work(source: fa.FileTable, row: dict | None, row_id: int | str) -> _Work | _Read:
    if row is None:
        return _error(row_id, {}, "No row with this id.")
    labels = {c: row.get(c) for c in source.labels}
    path = row.get(source.path_column)
    if not path:
        return _error(row_id, labels, "This row has no file yet.")
    if fa.path_problem(path):
        return _error(row_id, labels, "This row's stored path can't be opened safely.")
    return _Work(row_id, labels, path, source.bucket)


def _error(
    row_id: int | str, labels: dict, note: str, size: int | None = None
) -> _Read:
    return _Read(
        FileResult(id=row_id, labels=labels, kind="error", size_bytes=size, note=note)
    )


def _read(call: _Call, work: _Work) -> _Read:
    """One row's file; never raises, so one file can't fail the others."""
    try:
        if call.late():
            return _error(work.id, work.labels, OUT_OF_TIME_NOTE)
        size = call.reader.size(work.bucket, work.path)
        if fa.file_kind(work.path) != "image":
            return _link(call, work, size, None)
        if size > fa.MAX_SOURCE_IMAGE_BYTES:
            return _link(call, work, size, TOO_LARGE_NOTE)
        try:
            with slots.image_slot(call.deadline):
                return _image(call, work, size)
        except slots.NoSlot as exc:
            note = BUSY_NOTE if exc.reason == "busy" else OUT_OF_TIME_NOTE
            return _error(work.id, work.labels, note, size)
    except FileNotFound:
        return _error(work.id, work.labels, "The file is missing from storage.")
    except FileReadError:
        return _error(work.id, work.labels, READ_FAILED_NOTE)
    except Exception as exc:  # noqa: BLE001 — logged by type; the other files still return
        logger.warning("read_file failed on one file: %s", type(exc).__name__)
        return _error(work.id, work.labels, READ_FAILED_NOTE)


def _image(call: _Call, work: _Work, size: int) -> _Read:
    data = call.reader.download(work.bucket, work.path)
    if len(data) > fa.MAX_SOURCE_IMAGE_BYTES:
        return _link(call, work, size, TOO_LARGE_NOTE)
    with call.lock:
        limit = min(fa.MAX_IMAGE_BYTES, call.image_bytes)
    crop = call.params.crop
    try:
        converted = ic.to_png(
            data,
            page=call.params.page,
            crop=ic.Crop(**crop.model_dump()) if crop else None,
            max_bytes=limit,
        )
    except ic.BadImageRequest as exc:
        return _error(work.id, work.labels, f"{exc}.", size)
    except ic.ImageTooLarge:
        note = CALL_LIMIT_NOTE if limit < fa.MAX_IMAGE_BYTES else TOO_LARGE_NOTE
        return _link(call, work, size, note)
    except ic.ImageUnreadable as exc:
        return _link(call, work, size, f"Can't be shown: {exc}.")
    with call.lock:
        if len(converted.png) > call.image_bytes:
            return _link(call, work, size, CALL_LIMIT_NOTE)
        call.image_bytes -= len(converted.png)
    file = FileResult(
        id=work.id,
        labels=work.labels,
        kind="image",
        size_bytes=size,
        url=_url(call, work),
        image=_info(converted),
    )
    return _Read(file, ToolImage(converted.png, "image/png", _label(call, work)))


def _info(converted: ic.Converted) -> ImageInfo:
    return ImageInfo(
        width=converted.width,
        height=converted.height,
        mode=converted.mode,
        more_pages=converted.more_pages,
        shown_width=converted.shown_width,
        shown_height=converted.shown_height,
        full_scale=converted.full_scale,
        dim=converted.dim,
    )


def _label(call: _Call, work: _Work) -> str:
    parts = [f"{call.params.table} id {work.id}"]
    parts += [f"{k} {v}" for k, v in work.labels.items() if v is not None]
    if call.params.page:
        parts.append(f"page {call.params.page}")
    if crop := call.params.crop:
        parts.append(f"crop x {crop.x} y {crop.y} {crop.width} x {crop.height}")
    return ", ".join(parts)


def _url(call: _Call, work: _Work) -> str | None:
    """A link to the original; an image is still sent if signing fails."""
    if call.late():
        return None
    try:
        return call.reader.signed_url(work.bucket, work.path, fa.SIGNED_URL_SECONDS)
    except FileReadError:
        return None


def _link(call: _Call, work: _Work, size: int, note: str | None) -> _Read:
    url = _url(call, work)
    if url is None:
        return _error(work.id, work.labels, note or READ_FAILED_NOTE, size)
    file = FileResult(
        id=work.id, labels=work.labels, kind="link", size_bytes=size, url=url, note=note
    )
    return _Read(file)


read_file.__doc__ = f"""Open cylinder and plate scan images to view, and get links to scan videos.

First read the rows with query_table, then pass the table and up to {fa.MAX_FILES} of
their id values (list_tables notes say the same per table):
{fa.file_guide()}
Example, a scan's frames: query_table cyl_images with scan_id eq the scan, then
read_file with table cyl_images and those rows' ids. Skip experiments that are deleted
(see list_tables notes).

Images (PNG, JPEG, TIFF, including 16-bit and multi-page) are converted on the server
to PNG without compression loss, scaled so the longer side is at most {ic.MAX_SIDE} px.
For detail, read again with `crop`: a region up to {ic.MAX_SIDE} px comes at the
original's full resolution. Deeper-than-8-bit images are reduced to 8 bits from a fixed
full scale (see image.full_scale). Videos come back as a download link with their size.
If a file's note says Bloom is busy, out of time or the image is too large, tell the
user and offer what the note suggests. Describe what you see, but take lengths, areas
and counts from the traits, not the pictures.
"""
