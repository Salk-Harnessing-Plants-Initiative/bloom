"""The tables read_file opens, the stored paths it accepts, and how it treats each kind."""

from __future__ import annotations

import pytest

from bloom_mcp.data_access import file_access as fa
from bloom_mcp.data_access import table_access, table_indexes


@pytest.mark.parametrize(
    "path,kind",
    [
        ("exp/plant/1.png", "image"),
        ("a/B.JPG", "image"),
        ("a/frame.tiff", "image"),
        ("a/frame.gif", "other"),
        ("videos/1.webm", "other"),
        ("videos/12.mp4", "other"),
        ("noextension", "other"),
        ("folder.png/file", "other"),
    ],
)
def test_file_kind_by_extension(path, kind):
    assert fa.file_kind(path) == kind


@pytest.mark.parametrize(
    "path",
    [
        "",
        " a.png",
        "a.png ",
        "/abs/path.png",
        "a/../b.png",
        "a/./b.png",
        "..",
        "a//b.png",
        "a\\b.png",
        "a/b\x00.png",
        "a/b\x7f.png",
        "a/b\n.png",
        "x" * 1025,
        "%2e%2e/bloommcp-data/secret.png",
        "%2E%2E/run-logs/x.png",
        "a%2f..%2f..%2fscrna%2fx.png",
        "a/%2e%2e/%2e%2e/run-logs/x.png",
        "a/b.png?x",
        "x.h5ad#.png",
        "http:x/y.png",
        "a:b/c.png",
    ],
)
def test_paths_that_could_leave_the_bucket_are_refused(path):
    assert fa.path_problem(path) is not None


@pytest.mark.parametrize(
    "path",
    ["a/b.png", "Exp 1/plant_2/3.png", "x" * 1024, "a..b/c.png", "gravi-images/p.tif"],
)
def test_plain_paths_pass(path):
    assert fa.path_problem(path) is None


def test_only_scan_image_and_video_tables_are_opened():
    assert {t: s.bucket for t, s in fa.FILE_TABLES.items()} == {
        "cyl_images": "images",
        "gravi_images": "graviscan-images",
        "cyl_scan_videos": "videos",
        "gravi_plate_videos": "graviscan-videos",
    }


def test_every_file_table_is_readable_with_its_path_and_labels_visible():
    for table, source in fa.FILE_TABLES.items():
        assert table_access.is_readable(table), table
        hidden = table_access.HIDDEN_COLUMNS.get(table, frozenset())
        for column in (source.path_column, *source.labels):
            assert column not in hidden, (table, column)


def test_every_file_table_is_found_and_read_on_indexed_columns():
    for table, source in fa.FILE_TABLES.items():
        indexed = table_indexes.SORTABLE_COLUMNS[table]
        assert source.find_column in indexed, (table, source.find_column)
        assert "id" in indexed, table


def test_file_note_tells_claude_to_pass_row_ids():
    assert fa.file_note("cyl_images") == (
        "Each row's object_path is a file (filter scan_id; sort the frames by "
        "frame_number yourself): open it with read_file, passing table cyl_images and "
        "the rows' id values."
    )
    assert fa.file_note("species") is None


def test_read_files_description_lists_every_table_from_the_catalog():
    from bloom_mcp.sections.bloom_data.read_file import read_file

    for table, source in fa.FILE_TABLES.items():
        line = f"- {table}: {source.path_column} in {source.bucket}; filter "
        assert line + source.find_column in read_file.__doc__
    assert "list_tables notes" in read_file.__doc__


def test_limits_hold_together():
    # Claude's 5 MiB image limit is on base64, 4/3 of the PNG's bytes.
    assert fa.MAX_IMAGE_BYTES * 4 / 3 <= 5 * 1024 * 1024
    assert fa.MAX_IMAGE_BYTES <= fa.MAX_CALL_IMAGE_BYTES
    assert fa.MAX_FILES == 8


def test_a_path_the_storage_client_would_split_differently_is_refused(monkeypatch):
    monkeypatch.setattr(fa, "relative_path_to_parts", lambda path: ("other", "x.png"))
    assert fa.path_problem("a/b.png") == "would be read as a different path"
