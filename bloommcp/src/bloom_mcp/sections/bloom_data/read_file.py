"""read_file — open files Bloom stores: images to view, text to read, links for the rest."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bloom_mcp.contract import ToolImage, WithImages, as_mcp_tool
from bloom_mcp.data_access import file_access as fa
from bloom_mcp.data_access.file_ports import FileNotFound, FileReader, FileReadError
from bloom_mcp.data_access.table_access import redact_emails
from bloom_mcp.tools import _ports

from ._common import fix_request, logs_call
from ._page import MAX_REPLY_CHARS

_COLUMNS = "; ".join(f"{t}.{c} ({b})" for (t, c), b in fa.FILE_COLUMNS.items())
PATH_REMEDY = f"Take paths from these columns, with their bucket: {_COLUMNS}."
NOTE = (
    "Images are attached after this JSON, in the order of `files` with kind image. They "
    "are for looking; measurements come from the trait tables. Give the user any link; "
    "it works for an hour."
)


class ReadFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket: str = Field(description="Bucket the paths are in.")
    paths: list[str] = Field(
        description=f"Up to {fa.MAX_PATHS} paths in that bucket, from a table's path column."
    )


class FileResult(BaseModel):
    path: str
    kind: Literal["image", "text", "link", "error"]
    size_bytes: int | None = None
    text: str | None = Field(default=None, description="A text file's contents.")
    truncated: bool = Field(default=False, description="Text cut at the reply limit.")
    url: str | None = Field(
        default=None, description="Download link, valid for an hour."
    )
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
    """Open files Bloom stores for scans and datasets: see images, read text files.

    Paths come from table columns; read the rows first. Images (PNG, JPEG, TIFF) come
    back shrunk to about 1,000 px for you to look at; JSON, CSV, TSV and TXT come back
    as text up to the reply limit; videos and other files come back as a download link
    with their size. Pass several frames of one scan in one call. Describe what you
    see, but take lengths, areas and counts from the traits, not the pictures.
    """
    _check(params)
    reader = _ports.file_reader()
    budget = _Budget()
    files = [_read(reader, params.bucket, p, budget) for p in params.paths]
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


def _read(reader: FileReader, bucket: str, path: str, budget: _Budget) -> FileResult:
    try:
        size = reader.size(bucket, path)
        kind = fa.file_kind(path)
        if kind == "image" and size <= fa.MAX_SOURCE_IMAGE_BYTES:
            return _image(reader, bucket, path, size, budget)
        if kind == "text" and size <= fa.MAX_TEXT_FILE_BYTES:
            return _text(reader, bucket, path, size, budget)
        note = "Too large to open here." if kind != "other" else None
        return _link(reader, bucket, path, size, note)
    except FileNotFound:
        return FileResult(
            path=path, kind="error", note=f"No file at this path in {bucket}."
        )
    except FileReadError:
        return FileResult(
            path=path, kind="error", note="Couldn't read this file; try again."
        )


def _image(
    reader: FileReader, bucket: str, path: str, size: int, budget: _Budget
) -> FileResult:
    data = reader.download_image(bucket, path, fa.IMAGE_WIDTH)
    mime = fa.image_mime(data)
    if mime is None:
        return _link(reader, bucket, path, size, "Not a format you can view.")
    if len(data) > min(fa.MAX_IMAGE_BYTES, budget.image_bytes):
        note = "Over this call's image limit; open it in a call of its own."
        if len(data) > fa.MAX_IMAGE_BYTES:
            note = "Too large to view even when shrunk."
        return _link(reader, bucket, path, size, note)
    budget.image_bytes -= len(data)
    budget.images.append(ToolImage(data, mime))
    return FileResult(path=path, kind="image", size_bytes=size)


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
