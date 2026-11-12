"""Which stored files read_file opens, and how it treats each kind."""

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
        ("counts/x_1_/genes.json", "other"),
        ("de/table.CSV", "other"),
        ("videos/1.webm", "other"),
        ("a/notes.txt", "other"),
        ("videos/12.mp4", "other"),
        ("data/x.h5ad", "other"),
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
        "  ",
        "/abs/path.png",
        "a/../b.png",
        "a/./b.png",
        "..",
        "a//b.png",
        "a\\b.png",
        "a/b\x00.png",
        "a/b\n.png",
        "x" * 1025,
    ],
)
def test_unsafe_paths_are_refused(path):
    assert fa.path_problem(path) is not None


@pytest.mark.parametrize(
    "path", ["a/b.png", "Exp 1/plant_2/3.png", "x" * 1024, "a..b/c.png"]
)
def test_plain_paths_pass(path):
    assert fa.path_problem(path) is None


def test_only_scan_image_and_video_buckets_are_readable():
    assert fa.READABLE_BUCKETS == {
        "images",
        "graviscan-images",
        "videos",
        "graviscan-videos",
    }


def test_every_file_column_is_on_a_readable_table_and_bucket():
    for (table, column), bucket in fa.FILE_COLUMNS.items():
        assert table_access.is_readable(table), table
        assert column not in table_access.HIDDEN_COLUMNS.get(table, frozenset()), column
        assert bucket in fa.READABLE_BUCKETS, bucket


def test_every_readable_bucket_has_a_path_column():
    assert set(fa.FILE_COLUMNS.values()) == set(fa.READABLE_BUCKETS)


def test_file_note_names_the_column_bucket_and_lookup():
    assert fa.file_note("cyl_images") == (
        "object_path is a file path in the images bucket (filter scan_id; sort the "
        "frames by frame_number yourself): open it with read_file."
    )
    assert fa.file_note("species") is None


def test_every_path_table_says_how_to_find_its_rows_on_an_indexed_column():
    assert set(fa.FILE_FIND) == {t for t, _ in fa.FILE_COLUMNS}
    for table, find in fa.FILE_FIND.items():
        column = find.split()[1].rstrip(";")
        assert column in table_indexes.SORTABLE_COLUMNS[table], (table, column)


def test_read_files_description_lists_every_path_column_from_the_catalog():
    from bloom_mcp.sections.bloom_data.read_file import read_file

    for (table, column), bucket in fa.FILE_COLUMNS.items():
        assert f"- {table}.{column} -> bucket {bucket}: " in read_file.__doc__
    assert "list_tables notes" in read_file.__doc__


def test_limits_hold_together():
    assert fa.MAX_IMAGE_BYTES <= fa.MAX_CALL_IMAGE_BYTES
    assert fa.MAX_PATHS == 8
