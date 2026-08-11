"""Task 4 — `bloomctl cyl download` metadata (scans.csv) contract."""

import csv

import pytest
from click.testing import CliRunner

import bloomctl.auth as auth
import bloomctl.cyl.download as dl
from bloomctl.cli import cli
from bloomctl.credentials import Credentials

SCAN = {
    "scan_id": 1,
    "qr_code": "QR-1",
    "scanner_id": 7,
    "species_id": 3,
    "species_name": "Pennycress",
    "species_genus": "Thlaspi",
    "species_species": "arvense",
    "uploaded_at": "2026-05-11T00:00:00Z",
    "wave_id": 11,
    "wave_number": 2,
    "wave_name": "Wave 2",
    "accession_id": 42,
    "date_scanned": "2026-05-11",
    "experiment_id": 17957,
    "experiment_name": "2026-05-11 GIFTOL",
    "germ_day": 1,
    "germ_day_color": "green",
    "phenotyper_id": 5,
    "plant_age_days": 14,
    "plant_id": 100,
}


def test_genotype_column_follows_accession_id():
    cols = dl.CSV_COLUMNS
    assert cols[cols.index("accession_id") + 1] == "genotype"


def test_build_scan_row_fills_genotype_and_relative_scan_path():
    row = dl.build_scan_row(SCAN, "Spring-32")
    assert row["genotype"] == "Spring-32"
    assert row["scan_path"] == "images/Wave2/Day14_2026-05-11/QR-1"
    assert row["scan_id"] == 1


def test_build_scan_row_blank_genotype_when_missing():
    assert dl.build_scan_row(SCAN, None)["genotype"] == ""


def test_write_scans_csv_roundtrip(tmp_path):
    path = tmp_path / "scans.csv"
    dl.write_scans_csv([dl.build_scan_row(SCAN, "Spring-32")], path)
    with path.open() as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == dl.CSV_COLUMNS
        rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["genotype"] == "Spring-32"


def test_a_failed_csv_write_leaves_the_previous_csv_intact(tmp_path, monkeypatch):
    """Opening scans.csv for writing would empty it before the first row was written.

    This runs again on every resume, so a full disk would take the previous run's metadata
    with it. A full disk fails at the write, not the open, which is what makes the order
    matter: by then the file it opened has already been emptied.
    """
    import errno
    from pathlib import Path

    path = tmp_path / "scans.csv"
    path.write_text("the scans.csv from the run before\n")

    real_open = Path.open

    class _FullDisk:
        """An opened file that accepted the truncation and then ran out of space."""

        def __init__(self, fh):
            self._fh = fh

        def write(self, *_args):
            raise OSError(errno.ENOSPC, "No space left on device")

        writelines = write

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self._fh.close()
            return False

        def __getattr__(self, name):
            return getattr(self._fh, name)

    def _no_space(self, mode="r", *args, **kwargs):
        fh = real_open(self, mode, *args, **kwargs)
        return _FullDisk(fh) if any(c in mode for c in "wa+") else fh

    monkeypatch.setattr(Path, "open", _no_space)

    with pytest.raises(OSError):
        dl.write_scans_csv([dl.build_scan_row(SCAN, "Spring-32")], path)

    monkeypatch.undo()
    assert path.read_text() == "the scans.csv from the run before\n"
    assert not list(tmp_path.glob(".dl-*.tmp")), "no temp file left behind"


def test_meta_only_writes_csv_and_skips_images(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "bloomctl.credentials.load_credentials",
        lambda *a, **k: Credentials("https://x/api", "KEY", "u@s.edu", "pw"),
    )
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: object())
    monkeypatch.setattr(dl, "fetch_scans", lambda *a, **k: [SCAN])
    monkeypatch.setattr(dl, "fetch_genotypes", lambda c, ids: {42: "Spring-32"})

    def _no_images(*a, **k):
        raise AssertionError("images must not download under --meta-only")

    monkeypatch.setattr(dl, "download_images", _no_images)

    out = tmp_path / "out"
    result = CliRunner().invoke(
        cli, ["cyl", "download", str(out), "--experiment_id", "17957", "--meta_only"]
    )

    assert result.exit_code == 0, result.output
    with (out / "scans.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["genotype"] == "Spring-32"
