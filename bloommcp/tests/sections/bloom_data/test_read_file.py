"""read_file: images to view, text to read, links for the rest, within fixed limits."""

from __future__ import annotations

import base64
import json

import pytest
from fastmcp.tools.tool import ToolResult

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access import file_access as fa
from bloom_mcp.data_access.fake_file_reader import FakeFileReader
from bloom_mcp.sections.bloom_data import read_file as rf
from bloom_mcp.sections.bloom_data._page import MAX_REPLY_CHARS
from bloom_mcp.tools import _ports

PNG = b"\x89PNG\r\n\x1a\n"
SMALL = PNG + b"small"
TIFF = b"II*\0tiff-bytes"


@pytest.fixture
def files():
    fake = FakeFileReader(
        files={
            ("images", "exp/1/1.png"): PNG + b"original" * 100,
            ("images", "exp/1/2.png"): PNG + b"original-2" * 100,
            ("images", "exp/1/3.tif"): TIFF,
            ("scrna", "counts/a.json"): b'{"gene": "AT1G01010", "by": "ada@salk.edu"}',
            ("videos", "12.mp4"): b"\0" * 50,
            ("scrna", "d/x.h5ad"): b"\0" * 10,
        },
        resized={
            ("images", "exp/1/1.png"): SMALL,
            ("images", "exp/1/2.png"): SMALL + b"2",
        },
        broken={("images", "exp/broken.png")},
    )
    fake.files[("images", "exp/broken.png")] = PNG
    previous = _ports.file_reader()
    _ports.configure(file_reader=fake)
    yield fake
    _ports.configure(file_reader=previous)


def _read(bucket, *paths):
    out = rf.read_file({"bucket": bucket, "paths": list(paths)})
    if isinstance(out, ToolResult):
        images = [base64.b64decode(c.data) for c in out.content if c.type == "image"]
        return rf.ReadFileResult.model_validate(out.structured_content), images
    return out, []


def _refused(bucket, paths):
    with pytest.raises(BloomMCPError) as err:
        rf.read_file({"bucket": bucket, "paths": paths})
    assert err.value.code == "invalid_input"
    return err.value


def test_images_come_back_resized_in_order(files):
    result, images = _read("images", "exp/1/1.png", "exp/1/2.png")
    assert [f.kind for f in result.files] == ["image", "image"]
    assert images == [SMALL, SMALL + b"2"]
    assert result.files[0].size_bytes == len(files.files[("images", "exp/1/1.png")])
    assert ("download", "images", "exp/1/1.png") not in files.calls


def test_text_comes_back_with_emails_hidden(files):
    result, images = _read("scrna", "counts/a.json")
    (f,) = result.files
    assert f.kind == "text" and not f.truncated and images == []
    assert json.loads(f.text) == {"gene": "AT1G01010", "by": "[email hidden]"}


def test_videos_and_binary_files_get_a_link(files):
    result, _ = _read("videos", "12.mp4")
    (f,) = result.files
    assert f.kind == "link" and f.size_bytes == 50 and f.url and f.note is None
    result, _ = _read("scrna", "d/x.h5ad")
    assert result.files[0].kind == "link"
    assert ("download", "scrna", "d/x.h5ad") not in files.calls


def test_an_image_claude_cannot_view_gets_a_link(files):
    result, images = _read("images", "exp/1/3.tif")
    assert result.files[0].kind == "link" and images == []
    assert "format" in result.files[0].note


def test_one_failed_path_does_not_stop_the_rest(files):
    result, images = _read("images", "exp/missing.png", "exp/broken.png", "exp/1/1.png")
    missing, broken, ok = result.files
    assert missing.kind == "error" and "No file" in missing.note
    assert broken.kind == "error" and "try again" in broken.note
    assert ok.kind == "image" and images == [SMALL]


def test_a_large_text_file_is_linked_not_downloaded(files, monkeypatch):
    monkeypatch.setattr(fa, "MAX_TEXT_FILE_BYTES", 10)
    result, _ = _read("scrna", "counts/a.json")
    assert result.files[0].kind == "link"
    assert ("download", "scrna", "counts/a.json") not in files.calls


def test_a_large_original_image_is_linked_not_resized(files, monkeypatch):
    monkeypatch.setattr(fa, "MAX_SOURCE_IMAGE_BYTES", 10)
    result, images = _read("images", "exp/1/1.png")
    assert result.files[0].kind == "link" and images == []
    assert ("download_image", "images", "exp/1/1.png") not in files.calls


def test_text_is_cut_at_the_reply_limit_across_files(files):
    big = b"a" * (MAX_REPLY_CHARS - 10)
    files.files[("scrna", "big.txt")] = big
    result, _ = _read("scrna", "big.txt", "counts/a.json", "counts/a.json")
    first, second, third = result.files
    assert first.kind == "text" and not first.truncated
    assert second.kind == "text" and second.truncated and len(second.text) == 10
    assert third.kind == "error" and "reply limit" in third.note


def test_images_past_the_call_limit_get_links(files, monkeypatch):
    monkeypatch.setattr(fa, "MAX_CALL_IMAGE_BYTES", len(SMALL) + 1)
    result, images = _read("images", "exp/1/1.png", "exp/1/2.png")
    assert [f.kind for f in result.files] == ["image", "link"]
    assert images == [SMALL]
    assert "call of its own" in result.files[1].note


def test_an_image_too_large_even_when_shrunk_gets_a_link(files, monkeypatch):
    monkeypatch.setattr(fa, "MAX_IMAGE_BYTES", 3)
    result, images = _read("images", "exp/1/1.png")
    assert result.files[0].kind == "link" and images == []
    assert "even when shrunk" in result.files[0].note


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


def test_no_images_means_a_plain_result(files):
    out = rf.read_file({"bucket": "videos", "paths": ["12.mp4"]})
    assert isinstance(out, rf.ReadFileResult)
