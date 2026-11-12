"""Which stored files read_file opens, and how it treats each kind."""

from __future__ import annotations

import pytest

from bloom_mcp.data_access import file_access as fa
from bloom_mcp.data_access import table_access


@pytest.mark.parametrize(
    "path,kind",
    [
        ("exp/plant/1.png", "image"),
        ("a/B.JPG", "image"),
        ("a/frame.tiff", "image"),
        ("counts/x_1_/genes.json", "text"),
        ("de/table.CSV", "text"),
        ("a/b.tsv", "text"),
        ("a/notes.txt", "text"),
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


def test_logs_and_bloommcp_outputs_are_not_readable():
    assert "run-logs" not in fa.READABLE_BUCKETS
    assert "bloommcp-data" not in fa.READABLE_BUCKETS


def test_every_file_column_is_on_a_readable_table_and_bucket():
    for (table, column), bucket in fa.FILE_COLUMNS.items():
        assert table_access.is_readable(table), table
        assert column not in table_access.HIDDEN_COLUMNS.get(table, frozenset()), column
        assert bucket in fa.READABLE_BUCKETS, bucket


def test_every_readable_bucket_has_a_path_column():
    assert set(fa.FILE_COLUMNS.values()) == set(fa.READABLE_BUCKETS)


def test_file_note_names_the_column_and_bucket():
    assert fa.file_note("cyl_images") == (
        "object_path is a file path in the images bucket: open it with read_file."
    )
    assert fa.file_note("species") is None


def test_limits_hold_together():
    assert fa.MAX_IMAGE_BYTES <= fa.MAX_CALL_IMAGE_BYTES
    assert fa.MAX_PATHS == 8
