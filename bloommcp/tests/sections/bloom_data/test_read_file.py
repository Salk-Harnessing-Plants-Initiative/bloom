"""read_file: files by table row, never by a path from Claude; images as PNG, videos as links."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image
from read_file_data import FRAME, PLATE_PAGE, SCAN_PAGE, SITE, VIDEO_ID, installed, read

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access import file_access as fa
from bloom_mcp.data_access import image_convert as ic
from bloom_mcp.data_access.file_ports import FileReadError
from bloom_mcp.data_access.image_slots import NoSlot
from bloom_mcp.data_access.table_ports import DatabaseBusy
from bloom_mcp.sections.bloom_data import read_file as rf


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setenv("SITE_URL", SITE)
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
        "cyl_images id 1, scan_id 10, frame_number 1, shown at 1568 x 784",
        "cyl_images id 2, scan_id 10, frame_number 2",
    ]
    assert (first.image.width, first.image.height) == (2000, 1000)
    assert (
        first.size_bytes == len(FRAME) and first.url is None and first.page_url is None
    )
    assert first.image.full_scale is None and not first.image.more_pages
    assert first.image.scaled and not second.image.scaled and first.image.bits == 8
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
    assert labels == ["gravi_images id 1, scan_id 20, shown at 1568 x 1045"]


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


def test_there_is_no_page_parameter(fakes):
    _refused(table="gravi_images", ids=[1], page=1)


def test_missing_rows_rows_without_files_and_missing_files(fakes):
    result, images, _ = read("cyl_images", 99, 4, 6, 1)
    no_row, no_file, gone, ok = result.files
    assert no_row.kind == "error" and no_row.note == "No row with this id."
    assert no_file.note == "This row has no stored file."
    assert no_file.labels == {"scan_id": 10, "frame_number": 4}
    assert gone.note == "The file is missing from storage."
    assert ok.kind == "image" and len(images) == 1


def test_a_truncated_frame_fails_alone(fakes):
    result, images, _ = read("cyl_images", 8, 1)
    bad, ok = result.files
    assert bad.kind == "page" and "damaged" in bad.note and bad.page_url == SCAN_PAGE
    assert ok.kind == "image" and len(images) == 1


def test_a_file_that_is_not_an_image_gets_a_link(fakes):
    result, images, _ = read("cyl_images", 3)
    assert result.files[0].kind == "page" and images == []
    assert result.files[0].note.startswith("Can't be shown here:")


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


def test_images_never_get_a_download_link(fakes):
    _, files = fakes
    read("cyl_images", 1, 8)
    assert not [c for c in files.calls if c[0] == "signed_url"]


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
    (f,) = result.files
    assert f.kind == "page" and images == [] and f.url is None
    assert f.note == rf.TOO_LARGE_NOTE and f.page_url == SCAN_PAGE
    assert "upload it to this chat" in f.note and _downloads(files) == []


def test_an_image_too_large_to_decode_gets_a_link_to_the_original(fakes, monkeypatch):
    monkeypatch.setattr(ic, "MAX_DECODED_BYTES", 100)
    result, images, _ = read("gravi_images", 1)
    (f,) = result.files
    assert f.kind == "page" and images == [] and f.note == rf.TOO_LARGE_NOTE
    assert f.page_url == PLATE_PAGE


def test_images_past_the_call_limit_get_links_with_their_url(fakes, monkeypatch):
    size = len(ic.to_png(FRAME, crop=None, max_bytes=10**8).png)
    monkeypatch.setattr(fa, "MAX_CALL_IMAGE_BYTES", size + 1)
    result, images, _ = read("cyl_images", 1, 9)
    assert sorted(f.kind for f in result.files) == ["image", "page"]
    (link,) = [f for f in result.files if f.kind == "page"]
    assert link.note == rf.CALL_LIMIT_NOTE and link.page_url == SCAN_PAGE
    assert len(images) == 1
    assert link.image.width == 2000  # kept, so Claude can still plan a crop


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


def test_a_link_that_cannot_be_signed_is_an_error_not_a_link_without_url(
    fakes, monkeypatch
):
    _, files = fakes

    def no_link(*args):
        raise FileReadError("sign")

    monkeypatch.setattr(files, "signed_url", no_link)
    result, _, _ = read("cyl_scan_videos", VIDEO_ID)
    (f,) = result.files
    assert f.kind == "error" and f.url is None and f.note == rf.READ_FAILED_NOTE


def test_a_later_image_is_shrunk_to_fit_what_is_left(fakes, monkeypatch):
    full = len(ic.to_png(FRAME, crop=None, max_bytes=10**8).png)
    small = len(ic._png(ic._fit(Image.open(io.BytesIO(FRAME)), 1200)))
    monkeypatch.setattr(rf.slots, "MAX_IMAGE_SLOTS", 1)  # one at a time, in id order
    monkeypatch.setattr(fa, "MAX_CALL_IMAGE_BYTES", full + small)
    result, images, _ = read("cyl_images", 1, 9)
    assert [f.kind for f in result.files] == ["image", "image"]
    assert [i.size[0] for i in images] == [ic.MAX_SIDE, 1200]


def test_too_large_to_decode_is_not_reported_as_the_call_limit(fakes, monkeypatch):
    monkeypatch.setattr(fa, "MAX_CALL_IMAGE_BYTES", 1000)
    monkeypatch.setattr(ic, "MAX_DECODED_BYTES", 100)
    result, _, _ = read("cyl_images", 1)
    assert result.files[0].note == rf.TOO_LARGE_NOTE


def test_out_of_budget_after_scaling_is_the_call_limit(fakes, monkeypatch):
    monkeypatch.setattr(fa, "MAX_CALL_IMAGE_BYTES", 1000)
    result, images, _ = read("cyl_images", 1)
    assert result.files[0].note == rf.CALL_LIMIT_NOTE and images == []


def test_a_slot_missed_for_time_says_out_of_time(fakes, monkeypatch):
    monkeypatch.setattr(
        rf.slots, "image_slot", lambda deadline: (_ for _ in ()).throw(NoSlot("time"))
    )
    result, _, _ = read("cyl_images", 1)
    assert result.files[0].note == rf.OUT_OF_TIME_NOTE


def test_no_link_is_signed_after_the_deadline(fakes, monkeypatch):
    _, files = fakes
    # On time for the size check, late by the time the link would be signed.
    answers = iter([False] + [True] * 10)
    monkeypatch.setattr(rf._Call, "late", lambda self: next(answers))
    result, _, _ = read("cyl_scan_videos", VIDEO_ID)
    assert ("size", "videos", "videos/10.mp4") in files.calls
    assert result.files[0].note == rf.VIDEO_LATE_NOTE
    assert not [c for c in files.calls if c[0] == "signed_url"]


def test_a_download_larger_than_the_limit_is_not_converted(fakes, monkeypatch):
    _, files = fakes
    monkeypatch.setattr(files, "size", lambda bucket, path: 10)
    monkeypatch.setattr(fa, "MAX_SOURCE_IMAGE_BYTES", len(FRAME) - 1)
    result, images, _ = read("cyl_images", 1)
    assert result.files[0].note == rf.TOO_LARGE_NOTE and images == []


def test_a_16_bit_scan_reports_its_scale_peak_and_dimness(fakes):
    tables, files = fakes
    deep = io.BytesIO()
    Image.fromarray(np.full((8, 8), 4000, dtype=np.uint16)).save(deep, format="TIFF")
    files.files[("graviscan-images", "gravi/p.tif")] = deep.getvalue()
    result, _, _ = read("gravi_images", 1)
    info = result.files[0].image
    assert (info.full_scale, info.peak, info.dim, info.bits) == (65535, 4000, True, 16)


def test_a_multi_page_file_says_only_the_first_page_is_shown(fakes):
    _, files = fakes
    frames = [Image.new("L", (40, 30), 10), Image.new("L", (60, 50), 200)]
    out = io.BytesIO()
    frames[0].save(out, format="TIFF", save_all=True, append_images=frames[1:])
    files.files[("graviscan-images", "gravi/p.tif")] = out.getvalue()
    result, (image,), _ = read("gravi_images", 1)
    assert result.files[0].image.more_pages and np.asarray(image).max() == 10


def test_a_video_row_is_never_converted_even_with_an_image_name(fakes):
    tables, files = fakes
    tables.tables["gravi_plate_videos"][0]["object_path"] = "v/plate.png"
    files.files[("graviscan-videos", "v/plate.png")] = FRAME
    result, images, _ = read("gravi_plate_videos", 1)
    assert result.files[0].kind == "link" and images == []


def test_upper_case_uuids_find_their_row(fakes):
    result, _, _ = read("cyl_scan_videos", VIDEO_ID.upper())
    assert result.files[0].kind == "link" and result.files[0].id == VIDEO_ID


@pytest.mark.parametrize("row_id", ["²", "١٢", 0, -1, 2**63])
def test_ids_that_are_not_plain_positive_bigints_are_refused(fakes, row_id):
    _refused(table="cyl_images", ids=[row_id])


def test_labels_are_kept_short(fakes):
    tables, _ = fakes
    tables.tables["cyl_images"][0]["frame_number"] = "x" * 500
    _, _, labels = read("cyl_images", 1)
    assert len(labels[0]) < 200


def test_links_last_an_hour(fakes):
    result, _, _ = read("cyl_scan_videos", VIDEO_ID)
    assert result.files[0].url.endswith("t=3600")


def test_no_page_url_without_a_site(fakes, monkeypatch):
    monkeypatch.delenv("SITE_URL")
    result, _, _ = read("cyl_images", 8)
    assert result.files[0].kind == "page" and result.files[0].page_url is None


def test_a_failed_page_lookup_never_fails_the_read(fakes):
    tables, _ = fakes
    del tables.tables["cyl_scans_extended"]
    result, images, _ = read("cyl_images", 8, 1)
    assert [f.kind for f in result.files] == ["page", "image"] and len(images) == 1
    assert result.files[0].page_url is None


def test_pages_are_looked_up_once_per_call_and_only_when_needed(fakes):
    tables, _ = fakes
    read("cyl_images", 1, 2)
    assert "cyl_scans_extended" not in tables.calls
    read("cyl_images", 8, 3)
    assert tables.calls.count("cyl_scans_extended") == 1
