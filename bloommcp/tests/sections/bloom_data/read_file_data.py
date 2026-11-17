"""Rows, stored files and a call helper shared by the read_file tests."""

from __future__ import annotations

import base64
import copy
import io
from contextlib import contextmanager

from fastmcp.tools import ToolResult
from PIL import Image

from bloom_mcp.data_access.fake_file_reader import FakeFileReader
from bloom_mcp.data_access.fake_table_reader import FakeTableReader
from bloom_mcp.sections.bloom_data import read_file as rf
from bloom_mcp.tools import _ports


def image(width: int, height: int, fmt: str, value: int = 100) -> bytes:
    out = io.BytesIO()
    Image.new("L", (width, height), value).save(out, format=fmt)
    return out.getvalue()


FRAME = image(2000, 1000, "PNG", 50)
SMALL = image(100, 50, "PNG", 7)
SITE = "https://bloom.example"
SCAN_PAGE = f"{SITE}/app/phenotypes/1/2/3/4/10"
OTHER_SCAN_PAGE = f"{SITE}/app/phenotypes/6/7/8/9/11"
PLATE_PAGE = f"{SITE}/app/plate-phenotypes/1/5/wave/2/P%207"
PLATE = image(3000, 2000, "TIFF", 200)
TRUNCATED = image(500, 500, "PNG", 9)[:200]
VIDEO_ID = "6f1c2a3b-4d5e-4f60-8a71-92b3c4d5e6f7"

ROWS = {
    "cyl_images": [
        {"id": i, "scan_id": 10, "frame_number": i, "object_path": p}
        for i, p in [
            (1, "exp/10/1.png"),
            (2, "exp/10/2.png"),
            (3, "exp/10/fake.png"),
            (4, None),
            (5, "%2e%2e/bloommcp-data/secret.png"),
            (6, "exp/10/missing.png"),
            (7, "exp/10/broken.png"),
            (8, "exp/10/truncated.png"),
            (9, "exp/10/3.png"),
        ]
    ]
    + [{"id": 10, "scan_id": 11, "frame_number": 1, "object_path": "exp/11/bad.png"}],
    "gravi_images": [{"id": 1, "scan_id": 20, "object_path": "gravi/p.tif"}],
    "cyl_scans_extended": [
        {
            "scan_id": 10,
            "species_id": 1,
            "experiment_id": 2,
            "wave_id": 3,
            "accession_id": 4,
        },
        {
            "scan_id": 11,
            "species_id": 6,
            "experiment_id": 7,
            "wave_id": 8,
            "accession_id": 9,
        },
    ],
    "gravi_scans_extended": [
        {
            "scan_id": 20,
            "species_id": 1,
            "experiment_id": 5,
            "wave_number": 2,
            "plate_id": "P 7",
        }
    ],
    "cyl_scan_videos": [{"id": VIDEO_ID, "scan_id": 10, "path": "videos/10.mp4"}],
    "gravi_plate_videos": [
        {
            "id": 1,
            "experiment_id": 3,
            "plate_id": "P1",
            "wave_number": 1,
            "object_path": "v/plate.mp4",
        }
    ],
}
FILES = {
    ("images", "exp/10/1.png"): FRAME,
    ("images", "exp/10/2.png"): SMALL,
    ("images", "exp/10/3.png"): FRAME,
    ("images", "exp/10/fake.png"): b"not an image",
    ("images", "exp/10/broken.png"): FRAME,
    ("images", "exp/10/truncated.png"): TRUNCATED,
    ("images", "exp/11/bad.png"): TRUNCATED,
    ("graviscan-images", "gravi/p.tif"): PLATE,
    ("videos", "videos/10.mp4"): b"\0" * 50,
    ("graviscan-videos", "v/plate.mp4"): b"\0" * 10,
}


@contextmanager
def installed():
    """Fake rows and files in place of Bloom's database and storage."""
    tables = FakeTableReader(copy.deepcopy(ROWS))
    files = FakeFileReader(dict(FILES), broken={("images", "exp/10/broken.png")})
    previous = (_ports.table_reader(), _ports.file_reader())
    _ports.configure(table_reader=tables, file_reader=files)
    try:
        yield tables, files
    finally:
        _ports.configure(table_reader=previous[0], file_reader=previous[1])


def read(table, *ids, **extra):
    """The result, the attached images and the label sent before each image."""
    out = rf.read_file({"table": table, "ids": list(ids), **extra})
    if not isinstance(out, ToolResult):
        return out, [], []
    images, labels = [], []
    for block, after in zip(out.content, out.content[1:]):
        if after.type == "image":
            assert block.type == "text"
            labels.append(block.text)
    for block in out.content:
        if block.type == "image":
            assert block.mimeType == "image/png"
            images.append(Image.open(io.BytesIO(base64.b64decode(block.data))))
    return rf.ReadFileResult.model_validate(out.structured_content), images, labels
