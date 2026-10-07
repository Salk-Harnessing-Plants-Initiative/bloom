"""Tests for fetch-published (argo/scrna/analysis/bloom_scrna_analysis/fetch.py): load-dataset's
copy of a run's published .h5ad back onto the shared disk. A local folder stands in for S3."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "argo/scrna/analysis"))

from bloom_scrna_analysis import fetch


def test_the_published_file_is_copied_to_dest(tmp_path):
    published = tmp_path / "runs_output" / "run-1"
    (published / "h5ad").mkdir(parents=True)
    (published / "h5ad" / "col0.h5ad").write_bytes(b"final")
    dest = tmp_path / "shared" / "runs" / "run-1" / "h5ad" / "col0.h5ad"
    assert fetch.main([str(published), "h5ad/col0.h5ad", str(dest)]) == 0
    assert dest.read_bytes() == b"final"
    assert [p.name for p in dest.parent.iterdir()] == ["col0.h5ad"], "no partial left"


def test_a_file_never_published_exits_6_and_writes_nothing(tmp_path):
    dest = tmp_path / "shared" / "col0.h5ad"
    code = fetch.main([str(tmp_path / "runs_output"), "h5ad/col0.h5ad", str(dest)])
    assert code == fetch.EXIT_NOT_PUBLISHED == 6
    assert not dest.exists()


def test_the_wrong_number_of_arguments_is_a_usage_error(capsys):
    assert fetch.main(["only-one"]) == 2
    assert "fetch-published" in capsys.readouterr().err
