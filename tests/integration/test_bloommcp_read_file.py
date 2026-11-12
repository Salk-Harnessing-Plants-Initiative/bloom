"""Live check of bloommcp's read_file: bloom_agent downloads a stored PNG frame and a TIFF
plate scan and gets them back as PNG (scaled, or cropped at full resolution), and gets a
link for a video.

Unit counterpart: bloommcp/tests/sections/bloom_data/test_read_file.py.
"""

from __future__ import annotations

import json
import struct
import urllib.error
import urllib.request
import uuid
import zlib

import pytest

from tests.integration.conftest import BASE_URL, SERVICE_ROLE_KEY
from tests.integration.test_bloommcp_data_tools import _run_in_bloommcp, _unavailable

pytestmark = pytest.mark.integration

WIDE = 2000


def _png(width: int, height: int) -> bytes:
    """A grey 8-bit PNG, wider than read_file shows a whole image."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\0" + bytes([128]) * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _tiff(width: int, height: int) -> bytes:
    """A grey 8-bit uncompressed TIFF, like a plate scan."""
    pixels = bytes([128]) * (width * height)
    entries = [
        (256, 4, width),
        (257, 4, height),
        (258, 3, 8),
        (259, 3, 1),
        (262, 3, 1),
        (273, 4, 8 + 2 + 12 * 9 + 4),
        (277, 3, 1),
        (278, 4, height),
        (279, 4, len(pixels)),
    ]
    ifd = struct.pack("<H", len(entries))
    for tag, kind, value in entries:
        ifd += struct.pack("<HHII", tag, kind, 1, value)
    return b"II*\0" + struct.pack("<I", 8) + ifd + struct.pack("<I", 0) + pixels


def _storage(method: str, bucket: str, path: str, key: str, body: bytes = b"", kind=""):
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
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


@pytest.fixture(scope="module")
def stored():
    folder = f"ci-read-file-{uuid.uuid4().hex[:8]}"
    files = {
        ("images", f"{folder}/frame.png"): (_png(WIDE, 4), "image/png"),
        ("graviscan-images", f"{folder}/plate.tif"): (_tiff(WIDE, 4), "image/tiff"),
        ("videos", f"{folder}/scan.mp4"): (b"\0" * 64, "video/mp4"),
    }
    for (bucket, path), (body, kind) in files.items():
        status = _storage("POST", bucket, path, SERVICE_ROLE_KEY, body, kind)
        assert status == 200, f"upload to {bucket} failed: {status}"
    yield folder
    for bucket, path in files:
        _storage("DELETE", bucket, path, SERVICE_ROLE_KEY)


_SCRIPT = """
import base64, io, json
from PIL import Image
from bloom_mcp.sections.bloom_data.read_file import read_file
folder = {folder!r}
def run(bucket, paths, **extra):
    out = read_file({{"bucket": bucket, "paths": paths, **extra}})
    data = getattr(out, "structured_content", None) or out.model_dump(mode="json")
    images = [base64.b64decode(c.data) for c in getattr(out, "content", []) if c.type == "image"]
    widths = [Image.open(io.BytesIO(i)).size[0] for i in images]
    return {{"files": data["files"], "images": len(images), "widths": widths}}
print(json.dumps({{
    "image": run("images", [folder + "/frame.png", folder + "/missing.png"]),
    "plate": run("graviscan-images", [folder + "/plate.tif"]),
    "crop": run("graviscan-images", [folder + "/plate.tif"],
                crop={{"x": 100, "y": 0, "width": 1200, "height": 4}}),
    "video": run("videos", [folder + "/scan.mp4"]),
}}))
"""


@pytest.fixture(scope="module")
def results(stored) -> dict:
    return _run_in_bloommcp(_SCRIPT.format(folder=stored))


def test_an_image_comes_back_scaled(results):
    image, missing = results["image"]["files"]
    assert image["kind"] == "image", image
    assert results["image"]["images"] == 1
    assert results["image"]["widths"] == [1568]
    assert missing["kind"] == "error"


def test_a_tiff_plate_scan_comes_back_viewable(results):
    (f,) = results["plate"]["files"]
    assert f["kind"] == "image", f
    assert results["plate"]["widths"] == [1568]


def test_a_crop_keeps_full_resolution(results):
    (f,) = results["crop"]["files"]
    assert f["kind"] == "image" and f["image"]["width"] == WIDE
    assert results["crop"]["widths"] == [1200]


def test_a_video_gets_a_link(results):
    (f,) = results["video"]["files"]
    assert f["kind"] == "link" and f["size_bytes"] == 64
    assert "/storage/v1/object/sign/videos/" in f["url"]
