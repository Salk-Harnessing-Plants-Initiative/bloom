"""Task 5 — `bloomctl download` image download via Supabase Storage."""

from click.testing import CliRunner
from test_download_metadata import SCAN

import bloomctl.auth as auth
import bloomctl.download as dl
from bloomctl.cli import cli
from bloomctl.credentials import Credentials


def test_image_dest_preserves_real_extension(tmp_path):
    image = {"frame_number": 0, "object_path": "cyl-images/cyl-image_1_abc.png"}
    dest = dl.image_dest(tmp_path, SCAN, image)
    assert dest == tmp_path / "images/Wave2/Day14_2026-05-11/QR-1/0.png"


def test_image_dest_defaults_png_when_no_extension(tmp_path):
    image = {"frame_number": 2, "object_path": "cyl-images/no-ext"}
    assert dl.image_dest(tmp_path, SCAN, image).suffix == ".png"


class _FakeBucket:
    def download(self, object_path):
        return f"bytes::{object_path}".encode()


class _FakeStorage:
    def from_(self, bucket):
        assert bucket == "images"
        return _FakeBucket()


class _FakeClient:
    storage = _FakeStorage()


def test_download_images_writes_frames(tmp_path, monkeypatch):
    images = [
        {"frame_number": 0, "object_path": "cyl-images/a.png"},
        {"frame_number": 1, "object_path": "cyl-images/b.png"},
    ]
    monkeypatch.setattr(dl, "fetch_images", lambda client, scan_id: images)

    result = dl.download_images(_FakeClient(), [SCAN], tmp_path)

    assert result.ok == 2 and result.failed == 0 and result.total == 2
    frame = tmp_path / "images/Wave2/Day14_2026-05-11/QR-1/0.png"
    assert frame.read_bytes() == b"bytes::cyl-images/a.png"


class _FlakyBucket:
    """Downloads succeed except for object paths containing 'boom'."""

    def download(self, object_path):
        if "boom" in object_path:
            raise RuntimeError("500 storage error")
        return f"bytes::{object_path}".encode()


class _FlakyClient:
    storage = type("S", (), {"from_": lambda self, b: _FlakyBucket()})()


def test_download_images_records_failures_and_keeps_going(tmp_path, monkeypatch):
    images = [
        {"frame_number": 0, "object_path": "cyl-images/a.png"},
        {"frame_number": 1, "object_path": "cyl-images/boom.png"},  # fails
        {"frame_number": 2, "object_path": "cyl-images/c.png"},
    ]
    monkeypatch.setattr(dl, "fetch_images", lambda client, scan_id: images)

    result = dl.download_images(_FlakyClient(), [SCAN], tmp_path)

    # One bad frame does not abort the run: the other two still download.
    assert result.ok == 2 and result.failed == 1 and result.total == 3
    assert (tmp_path / "images/Wave2/Day14_2026-05-11/QR-1/0.png").exists()
    assert (tmp_path / "images/Wave2/Day14_2026-05-11/QR-1/2.png").exists()
    assert not (tmp_path / "images/Wave2/Day14_2026-05-11/QR-1/1.png").exists()

    log = tmp_path / "download_log.txt"
    dl.write_download_log(result, log)
    text = log.read_text()
    assert "FAIL scan=1 frame=1" in text
    assert "500 storage error" in text
    assert "Summary: 2 downloaded, 1 failed, 3 total" in text


def test_partial_download_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "bloomctl.credentials.load_credentials",
        lambda *a, **k: Credentials("https://x/api", "KEY", "u@s.edu", "pw"),
    )
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: _FlakyClient())
    monkeypatch.setattr(dl, "fetch_scans", lambda *a, **k: [SCAN])
    monkeypatch.setattr(dl, "fetch_genotypes", lambda c, ids: {42: "Spring-32"})
    monkeypatch.setattr(
        dl, "fetch_images", lambda client, scan_id: [
            {"frame_number": 0, "object_path": "cyl-images/boom.png"}
        ]
    )

    out = tmp_path / "out"
    result = CliRunner().invoke(cli, ["download", str(out), "--experiment-id", "17957"])

    # Partial download -> non-zero exit, but scans.csv + log are still written.
    assert result.exit_code != 0
    assert "frames failed" in result.output
    assert (out / "scans.csv").exists()
    assert (out / "download_log.txt").exists()


def test_full_download_writes_csv_and_images(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "bloomctl.credentials.load_credentials",
        lambda *a, **k: Credentials("https://x/api", "KEY", "u@s.edu", "pw"),
    )
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: _FakeClient())
    monkeypatch.setattr(dl, "fetch_scans", lambda *a, **k: [SCAN])
    monkeypatch.setattr(dl, "fetch_genotypes", lambda c, ids: {42: "Spring-32"})
    monkeypatch.setattr(
        dl,
        "fetch_images",
        lambda client, scan_id: [
            {"frame_number": 0, "object_path": "cyl-images/a.png"}
        ],
    )

    out = tmp_path / "out"
    result = CliRunner().invoke(cli, ["download", str(out), "--experiment-id", "17957"])

    assert result.exit_code == 0, result.output
    assert (out / "scans.csv").exists()
    assert (out / "images/Wave2/Day14_2026-05-11/QR-1/0.png").exists()
