"""Reading one gene's stored expression file into a value per cell."""

from __future__ import annotations

import pytest

from bloom_mcp.data_access import expression_counts as ec
from bloom_mcp.data_access.fake_file_reader import FakeFileReader

PATH = "counts/ds_3_/AT4G28110.json"


def _files(body: bytes, path: str = PATH) -> FakeFileReader:
    return FakeFileReader({(ec.BUCKET, path): body})


def test_an_object_file_fills_the_cells_it_names_and_zeros_the_rest():
    files = _files(b'{"1": 1.5, "3": 2}')
    assert ec.gene_values(files, PATH, 4) == [0.0, 1.5, 0.0, 2.0]


def test_an_older_list_file_is_read_the_same():
    files = _files(b'[{"1": 1.5}, {"3": 2}]')
    assert ec.gene_values(files, PATH, 4) == [0.0, 1.5, 0.0, 2.0]


def test_it_reads_from_the_scrna_bucket_at_the_stored_path():
    files = _files(b"{}")
    ec.gene_values(files, PATH, 2)
    assert files.calls == [("size", "scrna", PATH), ("download", "scrna", PATH)]


@pytest.mark.parametrize("key", ["4", "-1", "x", "1.0", "٣"])
def test_a_cell_outside_the_dataset_fails_the_gene(key):
    with pytest.raises(ec.CountsProblem, match="doesn't match this dataset"):
        ec.gene_values(_files(f'{{"{key}": 1}}'.encode()), PATH, 4)


@pytest.mark.parametrize(
    "body,match",
    [
        (b"not json", "not valid JSON"),
        (b'"text"', "not a map"),
        (b"[1, 2]", "not a map"),
        (b'{"1": "high"}', "non-number"),
        (b'{"1": true}', "non-number"),
        (b'{"1": NaN}', "non-finite"),
        (b'{"1": 1e400}', "non-finite"),
        (b'{"1": 1' + b"0" * 400 + b"}", "non-finite"),
    ],
)
def test_a_damaged_file_fails_the_gene(body, match):
    with pytest.raises(ec.CountsProblem, match=match):
        ec.gene_values(_files(body), PATH, 4)


def test_a_path_that_could_leave_the_bucket_is_not_read():
    files = _files(b"{}", path="../secrets.json")
    with pytest.raises(ec.CountsProblem, match="not read"):
        ec.gene_values(files, "../secrets.json", 4)
    assert files.calls == []


def test_a_missing_file_says_so():
    with pytest.raises(ec.CountsProblem, match="missing"):
        ec.gene_values(FakeFileReader(), PATH, 4)


def test_a_failed_read_says_to_try_again():
    files = FakeFileReader(broken={(ec.BUCKET, PATH)})
    with pytest.raises(ec.CountsProblem, match="try again"):
        ec.gene_values(files, PATH, 4)


def test_a_file_over_the_cap_is_not_downloaded(monkeypatch):
    monkeypatch.setattr(ec, "MAX_COUNTS_BYTES", 10)
    files = _files(b'{"1": 1.5, "3": 2.25}')
    with pytest.raises(ec.CountsProblem, match="over the"):
        ec.gene_values(files, PATH, 4)
    assert [c[0] for c in files.calls] == ["size"]


def test_a_huge_cell_key_fails_the_gene_with_a_short_note():
    key = "1" * 5000
    with pytest.raises(ec.CountsProblem, match="doesn't match this dataset") as raised:
        ec.gene_values(_files(f'{{"{key}": 1}}'.encode()), PATH, 4)
    assert len(str(raised.value)) < 200


def test_a_cell_key_with_leading_zeros_is_that_cell():
    assert ec.gene_values(_files(b'{"001": 2}'), PATH, 4) == [0.0, 2.0, 0.0, 0.0]
