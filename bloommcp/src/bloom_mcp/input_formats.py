"""Registry of accepted input file formats for uploaded analysis inputs.

Each registered format declares how to load its bytes into a DataFrame and how to
validate a **bounded** prefix/schema without a full parse, so a multi-GB upload is
never fully read just to confirm its type. Row-oriented formats (CSV/TSV/Excel/JSON)
peek the first ``PEEK_ROWS`` rows; columnar formats (Parquet/Feather) validate via the
footer schema and read no data rows.

Pickle is deliberately absent — unpickling untrusted bytes executes arbitrary code, so
it is not a registered (accepted) format.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, Optional

import pandas as pd

# Rows read by the bounded validators for row-oriented formats.
PEEK_ROWS = 50

_MB = 1024 * 1024
_GB = 1024 * _MB
# Counts matrices reach ~1-2 GB; the default cap targets them. Row-oriented,
# non-columnar formats (Excel/JSON) are never that large, so they cap lower.
DEFAULT_MAX_SIZE = 2 * _GB
DOCUMENT_MAX_SIZE = 200 * _MB

# Size limit for the simple ``/uploads`` endpoint, which loads the whole file into
# the server's memory before saving it. Files larger than this must use
# ``/uploads/sign`` instead, where the client uploads straight to Storage and the
# file never passes through the server's memory.
MAX_BUFFERED_UPLOAD_SIZE = DOCUMENT_MAX_SIZE


class FormatError(Exception):
    """Base for input-format failures, carrying a caller-safe message.

    Messages include only the caller-supplied filename and the format id — never a
    bucket key, object path, or storage traceback.
    """


class UnsupportedFormatError(FormatError):
    """The filename's extension is not a registered input format."""


class FileTooLargeError(FormatError):
    """The upload exceeds the registered format's maximum size."""


class InvalidFormatError(FormatError):
    """The bytes do not parse as the declared format."""


@dataclass(frozen=True)
class FormatSpec:
    """One accepted input format: how to load it and how to peek it.

    ``load`` returns the full DataFrame; ``peek`` returns a bounded validation frame
    (first rows for row-oriented formats, an empty schema frame for columnar ones) and
    raises when the bytes are not valid for the format.
    """

    id: str
    extensions: tuple[str, ...]
    mime: tuple[str, ...]
    max_size: int
    load: Callable[[bytes], pd.DataFrame]
    peek: Callable[[bytes], pd.DataFrame]


# ─── Loaders / bounded validators ─────────────────────────────────────────────


def _load_csv(data: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(data))


def _peek_csv(data: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(data), nrows=PEEK_ROWS)


def _load_tsv(data: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(data), sep="\t")


def _peek_tsv(data: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(data), sep="\t", nrows=PEEK_ROWS)


def _load_excel(data: bytes) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(data))


def _peek_excel(data: bytes) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(data), nrows=PEEK_ROWS)


def _load_parquet(data: bytes) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(data))


def _peek_parquet(data: bytes) -> pd.DataFrame:
    """Read only the Parquet footer schema — no data rows."""
    import pyarrow.parquet as pq

    schema = pq.read_schema(io.BytesIO(data))
    return pd.DataFrame(columns=list(schema.names))


def _load_feather(data: bytes) -> pd.DataFrame:
    return pd.read_feather(io.BytesIO(data))


def _peek_feather(data: bytes) -> pd.DataFrame:
    """Read only the Feather (Arrow IPC) schema — no data rows."""
    import pyarrow.ipc as ipc

    reader = ipc.open_file(io.BytesIO(data))
    return pd.DataFrame(columns=list(reader.schema.names))


def _load_json(data: bytes) -> pd.DataFrame:
    return pd.read_json(io.BytesIO(data), orient="records")


def _peek_json(data: bytes) -> pd.DataFrame:
    # JSON-records is not a GB-scale target format; peek the head after parse.
    return pd.read_json(io.BytesIO(data), orient="records").head(PEEK_ROWS)


# ─── Registry ─────────────────────────────────────────────────────────────────

_FORMATS: tuple[FormatSpec, ...] = (
    FormatSpec("csv", (".csv",), ("text/csv",), DEFAULT_MAX_SIZE, _load_csv, _peek_csv),
    FormatSpec(
        "tsv",
        (".tsv",),
        ("text/tab-separated-values",),
        DEFAULT_MAX_SIZE,
        _load_tsv,
        _peek_tsv,
    ),
    FormatSpec(
        "excel",
        (".xlsx",),
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",),
        DOCUMENT_MAX_SIZE,
        _load_excel,
        _peek_excel,
    ),
    FormatSpec(
        "parquet",
        (".parquet",),
        ("application/vnd.apache.parquet", "application/octet-stream"),
        DEFAULT_MAX_SIZE,
        _load_parquet,
        _peek_parquet,
    ),
    FormatSpec(
        "feather",
        (".feather",),
        ("application/vnd.apache.arrow.file", "application/octet-stream"),
        DEFAULT_MAX_SIZE,
        _load_feather,
        _peek_feather,
    ),
    FormatSpec(
        "json",
        (".json",),
        ("application/json",),
        DOCUMENT_MAX_SIZE,
        _load_json,
        _peek_json,
    ),
)

_BY_EXT: dict[str, FormatSpec] = {ext: spec for spec in _FORMATS for ext in spec.extensions}
_BY_ID: dict[str, FormatSpec] = {spec.id: spec for spec in _FORMATS}


def registered_extensions() -> list[str]:
    """Sorted list of accepted file extensions (e.g. ``[".csv", ".parquet", ...]``)."""
    return sorted(_BY_EXT)


def get_format_by_filename(filename: str) -> Optional[FormatSpec]:
    """Return the :class:`FormatSpec` matching ``filename``'s extension, or ``None``."""
    ext = PurePosixPath(filename).suffix.lower()
    return _BY_EXT.get(ext)


def get_format(format_id: str) -> Optional[FormatSpec]:
    """Return the :class:`FormatSpec` with ``id == format_id``, or ``None``."""
    return _BY_ID.get(format_id)


def _resolve(filename: str) -> FormatSpec:
    spec = get_format_by_filename(filename)
    if spec is None:
        raise UnsupportedFormatError(
            f"unsupported input format for {filename!r}; accepted extensions: "
            f"{', '.join(registered_extensions())}"
        )
    return spec


def validate_upload(filename: str, data: bytes) -> pd.DataFrame:
    """Bounded validation of ``data`` against ``filename``'s registered format.

    Returns the peeked frame (first rows for row-oriented formats, an empty
    schema-only frame for columnar ones). Raises:

    - :class:`UnsupportedFormatError` if the extension is not registered,
    - :class:`FileTooLargeError` if ``data`` exceeds the format's max size,
    - :class:`InvalidFormatError` if the bytes do not parse as the format.
    """
    spec = _resolve(filename)
    if len(data) > spec.max_size:
        raise FileTooLargeError(f"upload exceeds the {spec.id} size limit")
    try:
        return spec.peek(data)
    except FormatError:
        raise
    except Exception as exc:  # noqa: BLE001 - map any parse failure to a caller-safe error
        raise InvalidFormatError(f"bytes do not parse as {spec.id}") from exc


def load_frame(filename: str, data: bytes) -> pd.DataFrame:
    """Fully load ``data`` into a DataFrame using ``filename``'s registered loader.

    Raises :class:`UnsupportedFormatError` / :class:`InvalidFormatError` on failure.
    """
    spec = _resolve(filename)
    try:
        return spec.load(data)
    except Exception as exc:  # noqa: BLE001 - map any parse failure to a caller-safe error
        raise InvalidFormatError(f"bytes do not parse as {spec.id}") from exc
