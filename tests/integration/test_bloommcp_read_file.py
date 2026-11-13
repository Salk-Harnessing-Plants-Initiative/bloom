"""Live check of bloommcp's read_file: given table rows, bloom_agent reads their stored
frames (an 8-bit PNG and a 16-bit TIFF) back as PNG, scaled or cropped at full
resolution, links a video, and reports a missing file in storage's own words.

Unit counterpart: bloommcp/tests/sections/bloom_data/test_read_file.py.
"""

from __future__ import annotations

import struct
import urllib.error
import urllib.request
import uuid
import zlib

import pytest

from tests.integration.conftest import (
    BASE_URL,
    POSTGRES_DB,
    POSTGRES_HOST_PORT,
    POSTGRES_PASSWORD,
    POSTGRES_USER,
    SERVICE_ROLE_KEY,
)
from tests.integration.test_bloommcp_data_tools import _run_in_bloommcp, _unavailable

pytestmark = pytest.mark.integration

WIDE = 2000


def _png(width: int, height: int) -> bytes:
    """An 8-bit grey PNG whose pixel value is its column number mod 256."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = bytes(x % 256 for x in range(width))
    rows = b"".join(b"\0" + row for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _tiff16(width: int, height: int, value: int) -> bytes:
    """A flat 16-bit grey uncompressed TIFF, like a plate scan."""
    pixels = struct.pack(f"<{width * height}H", *([value] * (width * height)))
    entries = [
        (256, 4, width), (257, 4, height), (258, 3, 16), (259, 3, 1), (262, 3, 1),
        (273, 4, 8 + 2 + 12 * 9 + 4), (277, 3, 1), (278, 4, height), (279, 4, len(pixels)),
    ]  # fmt: skip
    ifd = struct.pack("<H", len(entries))
    for tag, kind, val in entries:
        ifd += struct.pack("<HHII", tag, kind, 1, val)
    return b"II*\0" + struct.pack("<I", 8) + ifd + struct.pack("<I", 0) + pixels


def _storage(method: str, bucket: str, path: str, body: bytes = b"", kind=""):
    headers = {
        "apikey": SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
    }
    if kind:
        headers["Content-Type"] = kind
    request = urllib.request.Request(
        f"{BASE_URL}/api/storage/v1/object/{bucket}/{path}",
        data=body or None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except urllib.error.URLError as exc:
        _unavailable(f"Storage unreachable at {BASE_URL}: {exc.reason}")


def _connect():
    try:
        import psycopg  # type: ignore
    except ImportError:
        _unavailable("psycopg is not installed")
    try:
        return psycopg.connect(
            f"host=127.0.0.1 port={POSTGRES_HOST_PORT} dbname={POSTGRES_DB} "
            f"user={POSTGRES_USER} password={POSTGRES_PASSWORD}",
            autocommit=True,
        )
    except psycopg.OperationalError as exc:
        _unavailable(f"Postgres unreachable: {exc}")


@pytest.fixture(scope="module")
def stored():
    folder = f"ci-read-file-{uuid.uuid4().hex[:8]}"
    files = {
        ("images", f"{folder}/frame.png"): (_png(WIDE, 4), "image/png"),
        ("images", f"{folder}/plate.tif"): (_tiff16(WIDE, 4, 32768), "image/tiff"),
        ("videos", f"{folder}/scan.mp4"): (b"\0" * 64, "video/mp4"),
    }
    uploaded = []
    conn = _connect()
    scan_id = None
    try:
        for (bucket, path), (body, kind) in files.items():
            status = _storage("POST", bucket, path, body, kind)
            assert status == 200, f"upload to {bucket} failed: {status}"
            uploaded.append((bucket, path))
        with conn.cursor() as cur:
            cur.execute("INSERT INTO cyl_scans DEFAULT VALUES RETURNING id")
            scan_id = cur.fetchone()[0]
            ids = {}
            for frame, name in [(1, "frame.png"), (2, "plate.tif"), (3, "gone.png")]:
                cur.execute(
                    "INSERT INTO cyl_images (scan_id, frame_number, object_path) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (scan_id, frame, f"{folder}/{name}"),
                )
                ids[name] = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO cyl_scan_videos (scan_id, path) VALUES (%s, %s) RETURNING id",
                (scan_id, f"{folder}/scan.mp4"),
            )
            ids["scan.mp4"] = str(cur.fetchone()[0])
        yield ids
    finally:
        if scan_id is not None:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM cyl_scan_videos WHERE scan_id = %s", (scan_id,)
                )
                cur.execute("DELETE FROM cyl_images WHERE scan_id = %s", (scan_id,))
                cur.execute("DELETE FROM cyl_scans WHERE id = %s", (scan_id,))
        conn.close()
        for bucket, path in uploaded:
            _storage("DELETE", bucket, path)


_SCRIPT = """
import base64, io, json
from PIL import Image
from bloom_mcp.sections.bloom_data.read_file import read_file
ids = {ids!r}
def run(table, row_ids, **extra):
    out = read_file({{"table": table, "ids": row_ids, **extra}})
    data = getattr(out, "structured_content", None) or out.model_dump(mode="json")
    images = [Image.open(io.BytesIO(base64.b64decode(c.data)))
              for c in getattr(out, "content", []) if c.type == "image"]
    return {{"files": data["files"], "sizes": [i.size for i in images],
             "first": [i.getpixel((0, 0)) for i in images]}}
print(json.dumps({{
    "frames": run("cyl_images", [ids["frame.png"], ids["plate.tif"], ids["gone.png"]]),
    "crop": run("cyl_images", [ids["frame.png"]],
                crop={{"x": 100, "y": 0, "width": 1200, "height": 4}}),
    "video": run("cyl_scan_videos", [ids["scan.mp4"]]),
}}))
"""


@pytest.fixture(scope="module")
def results(stored) -> dict:
    return _run_in_bloommcp(_SCRIPT.format(ids=stored))


def test_frames_come_back_scaled_and_labelled(results):
    frame, plate, gone = results["frames"]["files"]
    assert frame["kind"] == "image" and frame["labels"]["frame_number"] == 1
    assert plate["kind"] == "image" and plate["image"]["full_scale"] == 65535
    assert [w for w, _ in results["frames"]["sizes"]] == [1568, 1568]
    assert results["frames"]["first"][1] == 128


def test_a_missing_file_is_reported_in_storages_words(results):
    gone = results["frames"]["files"][2]
    assert (
        gone["kind"] == "error" and gone["note"] == "The file is missing from storage."
    )


def test_a_crop_keeps_full_resolution_and_position(results):
    (f,) = results["crop"]["files"]
    assert f["kind"] == "image" and f["image"]["width"] == WIDE
    assert results["crop"]["sizes"] == [[1200, 4]] and results["crop"]["first"] == [100]


def test_a_video_gets_a_link(results):
    (f,) = results["video"]["files"]
    assert f["kind"] == "link" and f["size_bytes"] == 64
    assert "/storage/v1/object/sign/videos/" in f["url"]
