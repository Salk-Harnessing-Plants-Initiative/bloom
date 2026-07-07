"""Single-scan download (`bloomctl download --scan-id`)."""

from click.testing import CliRunner

import bloomctl.auth as auth
import bloomctl.download as dl
from bloomctl.cli import cli
from bloomctl.credentials import Credentials
from test_download_metadata import SCAN


class _FakeBucket:
    def download(self, object_path):
        return f"bytes::{object_path}".encode()


class _FakeStorage:
    def from_(self, bucket):
        assert bucket == "images"
        return _FakeBucket()


class _FakeClient:
    storage = _FakeStorage()


def test_fetch_scan_returns_single_row():
    captured = {}

    class _Q:
        def select(self, *a):
            return self

        def eq(self, col, val):
            captured["col"], captured["val"] = col, val
            return self

        def limit(self, n):
            return self

        def execute(self):
            return type("R", (), {"data": [SCAN]})()

    class _Client:
        def table(self, name):
            captured["table"] = name
            return _Q()

    scan = dl.fetch_scan(_Client(), 1)
    assert scan == SCAN
    assert captured == {"table": "cyl_scans_extended", "col": "scan_id", "val": 1}


def test_fetch_scan_missing_returns_none():
    class _Client:
        def table(self, name):
            class _Q:
                def select(self, *a):
                    return self

                def eq(self, *a):
                    return self

                def limit(self, n):
                    return self

                def execute(self):
                    return type("R", (), {"data": []})()

            return _Q()

    assert dl.fetch_scan(_Client(), 999) is None


def test_download_scan_writes_that_scan_only(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "bloomctl.credentials.load_credentials",
        lambda *a, **k: Credentials("https://x/api", "KEY", "u@s.edu", "pw"),
    )
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: _FakeClient())
    monkeypatch.setattr(dl, "fetch_scan", lambda client, scan_id: SCAN)
    monkeypatch.setattr(dl, "fetch_genotypes", lambda c, ids: {42: "Spring-32"})
    monkeypatch.setattr(
        dl, "fetch_images", lambda client, scan_id: [
            {"frame_number": 0, "object_path": "cyl-images/a.png"}
        ]
    )
    # fetch_scans must NOT be called in scan mode.
    monkeypatch.setattr(
        dl, "fetch_scans", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("fetch_scans must not run for --scan-id")
        ),
    )

    out = tmp_path / "out"
    result = CliRunner().invoke(cli, ["download", str(out), "--scan-id", "1"])

    assert result.exit_code == 0, result.output
    with (out / "scans.csv").open() as fh:
        import csv

        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["scan_id"] == "1"
    assert (out / "images/Wave2/Day14_2026-05-11/QR-1/0.png").exists()


def test_scan_not_found_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "bloomctl.credentials.load_credentials",
        lambda *a, **k: Credentials("https://x/api", "KEY", "u@s.edu", "pw"),
    )
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: _FakeClient())
    monkeypatch.setattr(dl, "fetch_scan", lambda client, scan_id: None)

    result = CliRunner().invoke(cli, ["download", str(tmp_path / "out"), "--scan-id", "999"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_scan_id_and_experiment_id_are_mutually_exclusive(tmp_path):
    result = CliRunner().invoke(
        cli, ["download", str(tmp_path / "out"), "--scan-id", "1", "--experiment-id", "17957"]
    )
    assert result.exit_code != 0
    assert "exactly one" in result.output.lower()


def test_scan_id_or_experiment_id_is_required(tmp_path):
    result = CliRunner().invoke(cli, ["download", str(tmp_path / "out")])
    assert result.exit_code != 0
    assert "exactly one" in result.output.lower()
