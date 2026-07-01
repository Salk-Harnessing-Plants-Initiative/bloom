"""Task 5 — `bloomctl download` image download via Supabase Storage."""

from click.testing import CliRunner

import bloomctl.auth as auth
import bloomctl.download as dl
from bloomctl.cli import cli
from bloomctl.credentials import Credentials
from test_download_metadata import SCAN


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

    written = dl.download_images(_FakeClient(), [SCAN], tmp_path)

    assert written == 2
    frame = tmp_path / "images/Wave2/Day14_2026-05-11/QR-1/0.png"
    assert frame.read_bytes() == b"bytes::cyl-images/a.png"


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
