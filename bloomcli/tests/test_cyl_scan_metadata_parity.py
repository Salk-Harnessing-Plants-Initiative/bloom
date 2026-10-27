"""bloomctl's scans.csv metadata matches the web trait export's parity fixture.

The web trait export (OpenSpec change add-cyl-trait-csv-export, bloom#865) writes the
same 22 metadata columns as `bloomctl cyl download`, so the two files are
interchangeable. The web side is tested against
web/lib/cyl-trait-export/__fixtures__/scan-metadata-parity.json, which was generated
from bloomctl. This test keeps bloomctl to the same fixture: if either side changes the
columns or how a value is written, one of the two suites fails, and the fixture is
regenerated on purpose.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from bloomctl.cyl.download import CSV_COLUMNS, build_scan_row, write_scans_csv

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "web"
    / "lib"
    / "cyl-trait-export"
    / "__fixtures__"
    / "scan-metadata-parity.json"
)
PARITY = json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_columns_match_the_web_export() -> None:
    assert CSV_COLUMNS == PARITY["columns"]


@pytest.mark.parametrize(
    "case", PARITY["cases"], ids=[c["name"] for c in PARITY["cases"]]
)
def test_scans_csv_cells_match_the_web_export(case: dict, tmp_path: Path) -> None:
    path = tmp_path / "scans.csv"
    write_scans_csv([build_scan_row(case["row"], case["genotype"])], path)
    with path.open(encoding="utf-8", newline="") as f:
        header, *rows = list(csv.reader(f))
    assert header == PARITY["columns"]
    assert rows == [case["expected"]]
