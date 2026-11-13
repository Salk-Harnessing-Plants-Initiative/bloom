"""read_file: files by table row, never by a path from Claude; images as PNG, videos as links."""

from __future__ import annotations

import numpy as np
import pytest
from read_file_data import FRAME, VIDEO_ID, installed, read

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access import file_access as fa
from bloom_mcp.data_access import image_convert as ic
from bloom_mcp.data_access.file_ports import FileReadError
from bloom_mcp.data_access.table_ports import DatabaseBusy
from bloom_mcp.sections.bloom_data import read_file as rf


@pytest.fixture
def fakes():
    with installed() as both:
        yield both


def _refused(**params):
    with pytest.raises(BloomMCPError) as err:
        rf.read_file(params)
    assert err.value.code == "invalid_input"
    return err.value


def _downloads(files):
    return [c for c in files.calls if c[0] == "download"]


def test_frames_come_back_as_png_in_order_labelled_with_their_row(fakes):
    result, images, labels = read("cyl_images", 1, 2)
    first, second = result.files
    assert [f.kind for f in result.files] == ["image", "image"]
    assert [i.size for i in images] == [(ic.MAX_SIDE, 784), (100, 50)]
    assert (first.id, first.labels) == (1, {"scan_id": 10, "frame_number": 1})
    assert labels == [
        "cyl_images id 1, scan_id 10, frame_number 1",
        "cyl_images id 2, scan_id 10, frame_number 2",
    ]
    assert (first.image.width, first.image.height) == (2000, 1000)
    assert first.size_bytes == len(FRAME) and first.url and "exp/10/1.png" in first.url
    assert first.image.full_scale is None and not first.image.more_pages
    assert np.asarray(images[1]).max() == 7


def test_the_path_comes_from_the_row_not_from_claude(fakes):
    tables, files = fakes
    read("cyl_images", 1)
    assert tables.calls == ["cyl_images"]
    assert files.calls[0] == ("size", "images", "exp/10/1.png")
    with pytest.raises(BloomMCPError):
        rf.read_file({"table": "cyl_images", "ids": [1], "paths": ["../x.png"]})


def test_a_stored_path_that_could_leave_the_bucket_is_never_read(fakes):
    _, files = fakes
    result, images, _ = read("cyl_images", 5, 1)
    escaped, ok = result.files
    assert escaped.kind == "error" and "safely" in escaped.note and escaped.url is None
    assert ok.kind == "image" and len(images) == 1
    assert all("secret" not in c[2] for c in files.calls)


def test_a_tiff_plate_scan_comes_back_viewable(fakes):
    result, (image,), labels = read("gravi_images", 1)
    assert result.files[0].kind == "image" and result.files[0].image.mode == "L"
    assert image.format == "PNG" and np.asarray(image).max() == 200
    assert labels == ["gravi_images id 1, scan_id 20"]


def test_a_crop_is_cut_from_the_original_at_full_resolution(fakes):
    crop = {"x": 100, "y": 100, "width": 500, "height": 400}
    result, (image,), labels = read("gravi_images", 1, crop=crop)
    assert image.size == (500, 400)
    assert (result.files[0].image.width, result.files[0].image.height) == (3000, 2000)
    assert labels == ["gravi_images id 1, scan_id 20, crop x 100 y 100 500 x 400"]


def test_a_crop_outside_one_image_is_reported_for_that_image(fakes):
    crop = {"x": 0, "y": 0, "width": 1000, "height": 1000}
    result, images, _ = read("cyl_images", 2, 1, crop=crop)
    small, large = result.files
    assert small.kind == "error" and "past the image's 100 x 50" in small.note
    assert large.kind == "image" and [i.size for i in images] == [(1000, 1000)]


def test_a_page_that_does_not_exist_is_reported(fakes):
    result, _, _ = read("gravi_images", 1, page=3)
    assert result.files[0].kind == "error" and "page 3" in result.files[0].note


def test_missing_rows_rows_without_files_and_missing_files(fakes):
    result, images, _ = read("cyl_images", 99, 4, 6, 1)
    no_row, no_file, gone, ok = result.files
    assert no_row.kind == "error" and no_row.note == "No row with this id."
    assert no_file.note == "This row has no file yet."
    assert no_file.labels == {"scan_id": 10, "frame_number": 4}
    assert gone.note == "The file is missing from storage."
    assert ok.kind == "image" and len(images) == 1


def test_a_truncated_frame_fails_alone(fakes):
    result, images, _ = read("cyl_images", 8, 1)
    bad, ok = result.files
    assert bad.kind == "link" and "damaged" in bad.note
    assert ok.kind == "image" and len(images) == 1


def test_a_file_that_is_not_an_image_gets_a_link(fakes):
    result, images, _ = read("cyl_images", 3)
    assert result.files[0].kind == "link" and images == []
    assert result.files[0].note.startswith("Can't be shown:")


def test_any_failure_inside_one_file_leaves_the_others(fakes, monkeypatch):
    _, files = fakes
    download = files.download

    def fails_on_three(bucket, path):
        if path == "exp/10/3.png":
            raise RuntimeError("storage client changed")
        return download(bucket, path)

    monkeypatch.setattr(files, "download", fails_on_three)
    result, images, _ = read("cyl_images", 9, 1)
    assert [f.kind for f in result.files] == ["error", "image"]
    assert result.files[0].note == rf.READ_FAILED_NOTE and len(images) == 1


def test_an_image_is_kept_when_its_link_cannot_be_signed(fakes, monkeypatch):
    _, files = fakes

    def no_link(*args):
        raise FileReadError("sign")

    monkeypatch.setattr(files, "signed_url", no_link)
    result, images, _ = read("cyl_images", 1)
    assert result.files[0].kind == "image" and result.files[0].url is None
    assert len(images) == 1


def test_videos_get_a_link_without_download(fakes):
    _, files = fakes
    result, _, _ = read("cyl_scan_videos", VIDEO_ID)
    (f,) = result.files
    assert f.kind == "link" and f.size_bytes == 50 and f.url and f.note is None
    assert f.labels == {"scan_id": 10}
    result, _, _ = read("gravi_plate_videos", 1)
    assert result.files[0].labels == {
        "experiment_id": 3,
        "plate_id": "P1",
        "wave_number": 1,
    }
    assert _downloads(files) == []


def test_a_large_original_image_is_linked_not_downloaded(fakes, monkeypatch):
    _, files = fakes
    monkeypatch.setattr(fa, "MAX_SOURCE_IMAGE_BYTES", len(FRAME) - 1)
    result, images, _ = read("cyl_images", 1)
    assert result.files[0].kind == "link" and images == []
    assert result.files[0].note == rf.TOO_LARGE_NOTE
    assert _downloads(files) == []


def test_an_image_with_too_many_pixels_points_to_the_bloom_page(fakes, monkeypatch):
    monkeypatch.setattr(ic, "MAX_PIXELS", 100)
    result, images, _ = read("gravi_images", 1)
    assert result.files[0].kind == "link" and images == []
    assert result.files[0].note == rf.TOO_LARGE_NOTE


def test_images_past_the_call_limit_get_links_with_their_url(fakes, monkeypatch):
    size = len(ic.to_png(FRAME, page=0, crop=None, max_bytes=10**8).png)
    monkeypatch.setattr(fa, "MAX_CALL_IMAGE_BYTES", size + 1)
    result, images, _ = read("cyl_images", 1, 9)
    assert sorted(f.kind for f in result.files) == ["image", "link"]
    (link,) = [f for f in result.files if f.kind == "link"]
    assert link.note == rf.CALL_LIMIT_NOTE and link.url and len(images) == 1


def test_repeated_ids_are_read_once(fakes):
    _, files = fakes
    result, images, _ = read("cyl_images", 1, 1)
    assert len(result.files) == 1 and len(images) == 1 and len(_downloads(files)) == 1


def test_numeric_ids_passed_as_text_are_accepted(fakes):
    result, images, _ = read("cyl_images", "1")
    assert result.files[0].id == 1 and len(images) == 1


def test_a_table_off_the_catalog_is_refused(fakes):
    _, files = fakes
    err = _refused(table="cyl_scans", ids=[1])
    assert "cyl_images" in err.message and files.calls == []


@pytest.mark.parametrize("count", [0, fa.MAX_FILES + 1])
def test_id_count_is_limited(fakes, count):
    _refused(table="cyl_images", ids=list(range(1, count + 1)))


def test_eight_ids_are_accepted(fakes):
    result, _, _ = read("cyl_images", *range(1, 9))
    assert len(result.files) == 8


@pytest.mark.parametrize(
    "table,row_id",
    [("cyl_images", "1 or 1=1"), ("cyl_images", True), ("cyl_scan_videos", 3)],
)
def test_ids_of_the_wrong_kind_are_refused(fakes, table, row_id):
    tables, _ = fakes
    _refused(table=table, ids=[row_id])
    assert tables.calls == []


@pytest.mark.parametrize(
    "extra", [{"page": -1}, {"crop": {"x": 0, "y": 0, "width": 0, "height": 5}}]
)
def test_bad_page_or_crop_values_are_refused(fakes, extra):
    _refused(table="cyl_images", ids=[1], **extra)


def test_a_database_failure_is_reported_like_other_reads(fakes):
    tables, _ = fakes
    tables.fail_with = DatabaseBusy("no slot")
    with pytest.raises(BloomMCPError) as err:
        rf.read_file({"table": "cyl_images", "ids": [1]})
    assert err.value.code != "internal_error"


def test_no_images_means_a_plain_result(fakes):
    out = rf.read_file({"table": "cyl_scan_videos", "ids": [VIDEO_ID]})
    assert isinstance(out, rf.ReadFileResult)
