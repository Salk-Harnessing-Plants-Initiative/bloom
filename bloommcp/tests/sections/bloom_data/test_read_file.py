"""read_file: images as lossless PNG, text to read, links for the rest, within limits."""

from __future__ import annotations

import base64
import io
import json

import numpy as np
import pytest
from fastmcp.tools.tool import ToolResult
from PIL import Image

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access import file_access as fa
from bloom_mcp.data_access import image_convert as ic
from bloom_mcp.data_access.fake_file_reader import FakeFileReader
from bloom_mcp.sections.bloom_data import read_file as rf
from bloom_mcp.sections.bloom_data._page import MAX_REPLY_CHARS
from bloom_mcp.tools import _ports


def _image(width: int, height: int, fmt: str, value: int = 100) -> bytes:
    out = io.BytesIO()
    Image.new("L", (width, height), value).save(out, format=fmt)
    return out.getvalue()


FRAME = _image(2000, 1000, "PNG", 50)
PLATE = _image(3000, 2000, "TIFF", 200)


@pytest.fixture
def files():
    fake = FakeFileReader(
        files={
            ("images", "exp/1/1.png"): FRAME,
            ("images", "exp/1/2.png"): _image(100, 50, "PNG", 7),
            ("graviscan-images", "gravi/p.tif"): PLATE,
            ("images", "exp/1/fake.png"): b"not an image",
            ("scrna", "counts/a.json"): b'{"gene": "AT1G01010", "by": "ada@salk.edu"}',
            ("videos", "12.mp4"): b"\0" * 50,
            ("scrna", "d/x.h5ad"): b"\0" * 10,
            ("images", "exp/broken.png"): FRAME,
        },
        broken={("images", "exp/broken.png")},
    )
    previous = _ports.file_reader()
    _ports.configure(file_reader=fake)
    yield fake
    _ports.configure(file_reader=previous)


def _read(bucket, *paths, **extra):
    out = rf.read_file({"bucket": bucket, "paths": list(paths), **extra})
    if isinstance(out, ToolResult):
        images = [
            Image.open(io.BytesIO(base64.b64decode(c.data)))
            for c in out.content
            if c.type == "image"
        ]
        assert all(c.mimeType == "image/png" for c in out.content if c.type == "image")
        return rf.ReadFileResult.model_validate(out.structured_content), images
    return out, []


def _refused(bucket, paths, **extra):
    with pytest.raises(BloomMCPError) as err:
        rf.read_file({"bucket": bucket, "paths": paths, **extra})
    assert err.value.code == "invalid_input"
    return err.value


def test_images_come_back_as_png_in_order_with_the_originals_size(files):
    result, images = _read("images", "exp/1/1.png", "exp/1/2.png")
    first, second = result.files
    assert [f.kind for f in result.files] == ["image", "image"]
    assert [i.size for i in images] == [(ic.MAX_SIDE, 784), (100, 50)]
    assert (first.image.width, first.image.height) == (2000, 1000)
    assert (first.image.shown_width, first.image.shown_height) == (ic.MAX_SIDE, 784)
    assert first.url and "exp/1/1.png" in first.url
    assert np.asarray(images[1]).max() == 7


def test_a_tiff_plate_scan_comes_back_viewable(files):
    result, (image,) = _read("graviscan-images", "gravi/p.tif")
    assert result.files[0].kind == "image" and result.files[0].image.mode == "L"
    assert image.format == "PNG" and np.asarray(image).max() == 200


def test_a_crop_is_cut_from_the_original_at_full_resolution(files):
    crop = {"x": 100, "y": 100, "width": 500, "height": 400}
    result, (image,) = _read("graviscan-images", "gravi/p.tif", crop=crop)
    assert image.size == (500, 400)
    assert (result.files[0].image.width, result.files[0].image.height) == (3000, 2000)


def test_a_crop_outside_one_image_is_reported_for_that_image(files):
    crop = {"x": 0, "y": 0, "width": 1000, "height": 1000}
    result, images = _read("images", "exp/1/2.png", "exp/1/1.png", crop=crop)
    small, large = result.files
    assert small.kind == "error" and "past the image's 100 x 50" in small.note
    assert large.kind == "image" and [i.size for i in images] == [(1000, 1000)]


def test_a_page_past_the_last_is_reported(files):
    result, _ = _read("graviscan-images", "gravi/p.tif", page=3)
    assert result.files[0].kind == "error" and "page 3" in result.files[0].note


def test_a_file_that_is_not_an_image_gets_a_link(files):
    result, images = _read("images", "exp/1/fake.png")
    assert result.files[0].kind == "link" and images == []


def test_text_comes_back_with_emails_hidden(files):
    result, images = _read("scrna", "counts/a.json")
    (f,) = result.files
    assert f.kind == "text" and not f.truncated and images == []
    assert json.loads(f.text) == {"gene": "AT1G01010", "by": "[email hidden]"}


def test_videos_and_binary_files_get_a_link_without_download(files):
    result, _ = _read("videos", "12.mp4")
    (f,) = result.files
    assert f.kind == "link" and f.size_bytes == 50 and f.url and f.note is None
    result, _ = _read("scrna", "d/x.h5ad")
    assert result.files[0].kind == "link"
    assert not [c for c in files.calls if c[0] == "download"]


def test_one_failed_path_does_not_stop_the_rest(files):
    result, images = _read("images", "exp/missing.png", "exp/broken.png", "exp/1/2.png")
    missing, broken, ok = result.files
    assert missing.kind == "error" and "No file" in missing.note
    assert broken.kind == "error" and "try again" in broken.note
    assert ok.kind == "image" and len(images) == 1


def test_a_large_text_file_is_linked_not_downloaded(files, monkeypatch):
    monkeypatch.setattr(fa, "MAX_TEXT_FILE_BYTES", 10)
    result, _ = _read("scrna", "counts/a.json")
    assert result.files[0].kind == "link"
    assert ("download", "scrna", "counts/a.json") not in files.calls


def test_a_large_original_image_is_linked_not_downloaded(files, monkeypatch):
    monkeypatch.setattr(fa, "MAX_SOURCE_IMAGE_BYTES", 10)
    result, images = _read("images", "exp/1/1.png")
    assert result.files[0].kind == "link" and images == []
    assert ("download", "images", "exp/1/1.png") not in files.calls


def test_text_is_cut_at_the_reply_limit_across_files(files):
    files.files[("scrna", "big.txt")] = b"a" * (MAX_REPLY_CHARS - 10)
    result, _ = _read("scrna", "big.txt", "counts/a.json", "counts/a.json")
    first, second, third = result.files
    assert first.kind == "text" and not first.truncated
    assert second.kind == "text" and second.truncated and len(second.text) == 10
    assert third.kind == "error" and "reply limit" in third.note


def test_images_past_the_call_limit_get_links(files, monkeypatch):
    size = len(ic.to_png(FRAME, page=0, crop=None, max_bytes=10**8).png)
    monkeypatch.setattr(fa, "MAX_CALL_IMAGE_BYTES", size + 1)
    result, images = _read("images", "exp/1/1.png", "exp/1/1.png")
    assert [f.kind for f in result.files] == ["image", "link"] and len(images) == 1
    assert "call of its own" in result.files[1].note


def test_a_bucket_off_the_list_is_refused(files):
    err = _refused("run-logs", ["a.txt"])
    assert "images" in err.message and files.calls == []


@pytest.mark.parametrize("count", [0, fa.MAX_PATHS + 1])
def test_path_count_is_limited(files, count):
    _refused("images", [f"p/{i}.png" for i in range(count)])
    assert files.calls == []


def test_an_unsafe_path_refuses_the_whole_call_before_reading(files):
    err = _refused("images", ["exp/1/1.png", "../secret.png"])
    assert "cyl_images.object_path" in err.remedy
    assert files.calls == []


@pytest.mark.parametrize(
    "extra",
    [{"page": -1}, {"crop": {"x": 0, "y": 0, "width": 0, "height": 5}}],
)
def test_bad_page_or_crop_values_are_refused(files, extra):
    _refused("images", ["exp/1/1.png"], **extra)
    assert files.calls == []


def test_no_images_means_a_plain_result(files):
    out = rf.read_file({"bucket": "videos", "paths": ["12.mp4"]})
    assert isinstance(out, rf.ReadFileResult)
