"""bloom#525 — long `cyl download` runs must survive an expiring session and resume.

Reproduces the reported shape: a run downloads fine for a while, then every
remaining object 404s with "Bucket not found" because the JWT lapsed. Covers the
self-renewing storage session, the skip-already-downloaded resume path, and the
error text that no longer claims the bucket is missing.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from test_download_metadata import SCAN

import bloomctl.auth as auth
import bloomctl.cyl._storage as storage
import bloomctl.cyl.download as dl
from bloomctl.cli import cli
from bloomctl.credentials import Credentials

CREDS = Credentials("https://x/api", "KEY", "u@s.edu", "pw")
EXPIRED = "{'statusCode': 404, 'error': Bucket not found, 'message': Bucket not found}"


class _Bucket:
    """Storage bucket that serves `budget` objects, then behaves like an expired session."""

    def __init__(self, budget: int | None = None):
        self.budget = budget
        self.calls = 0

    def download(self, object_path):
        self.calls += 1
        if self.budget is not None and self.calls > self.budget:
            raise RuntimeError(EXPIRED)
        return f"bytes::{object_path}".encode()


class _Client:
    def __init__(self, budget=None):
        self.bucket = _Bucket(budget)
        self.storage = type("S", (), {"from_": lambda _self, name: self.bucket})()


def _images(count: int) -> list[dict]:
    return [{"frame_number": i, "object_path": f"cyl-images/{i}.png"} for i in range(count)]


def _frame(tmp_path, number: int):
    return tmp_path / f"images/Wave2/Day14_2026-05-11/QR-1/{number}.png"


# --- StorageSession ---------------------------------------------------------


def test_session_refreshes_proactively_after_the_configured_number_of_objects(monkeypatch):
    clients = [_Client(), _Client()]
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: clients[1])

    session = storage.StorageSession(clients[0], CREDS, refresh_every=3)
    for i in range(4):
        session.download(f"cyl-images/{i}.png")

    # The 4th object trips the counter: three on the first client, one on the renewed one.
    assert session.refreshes == 1
    assert clients[0].bucket.calls == 3
    assert clients[1].bucket.calls == 1


def test_session_without_credentials_never_refreshes(monkeypatch):
    def _must_not_authenticate(creds):
        raise AssertionError("must not re-authenticate without credentials")

    monkeypatch.setattr(auth, "make_authed_client", _must_not_authenticate)
    session = storage.StorageSession(_Client(), refresh_every=1)

    for i in range(3):
        session.download(f"cyl-images/{i}.png")
    assert session.refreshes == 0


def test_session_refreshes_and_retries_once_when_an_object_fails(monkeypatch):
    """The reactive backstop: an expiry that lands off the proactive cadence."""
    stale, fresh = _Client(budget=0), _Client()
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: fresh)

    session = storage.StorageSession(stale, CREDS, refresh_every=10_000)
    data = session.download("cyl-images/0.png")

    assert data == b"bytes::cyl-images/0.png"
    assert session.refreshes == 1


def test_session_reports_an_expired_session_instead_of_a_missing_bucket(monkeypatch):
    """Both attempts fail: the raised message must not read as missing data."""
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: _Client(budget=0))

    session = storage.StorageSession(_Client(budget=0), CREDS)
    with pytest.raises(storage.StorageError) as excinfo:
        session.download("cyl-images/0.png")

    message = str(excinfo.value)
    assert "expired session" in message
    assert "Bucket not found" in message  # the raw storage text is still there to debug with


def test_session_leaves_an_unrelated_error_message_alone():
    class _BrokenBucket:
        def download(self, object_path):
            raise RuntimeError("500 storage error")

    client = _Client()
    client.bucket = _BrokenBucket()
    session = storage.StorageSession(client)

    with pytest.raises(storage.StorageError) as excinfo:
        session.download("cyl-images/0.png")
    assert str(excinfo.value) == "500 storage error"


def test_session_reports_the_original_error_when_re_authentication_itself_fails(monkeypatch):
    monkeypatch.setattr(
        auth, "make_authed_client", lambda creds: (_ for _ in ()).throw(auth.AuthError("no dice"))
    )
    session = storage.StorageSession(_Client(budget=0), CREDS)

    with pytest.raises(storage.StorageError) as excinfo:
        session.download("cyl-images/0.png")
    assert "Bucket not found" in str(excinfo.value)


def test_already_downloaded_ignores_empty_and_missing_files(tmp_path):
    missing = tmp_path / "missing.png"
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    complete = tmp_path / "complete.png"
    complete.write_bytes(b"x")

    assert not storage.already_downloaded(missing)
    assert not storage.already_downloaded(empty)
    assert storage.already_downloaded(complete)


# --- download_images: surviving the expiry ----------------------------------


def test_download_images_survives_a_session_expiring_mid_run(tmp_path, monkeypatch):
    """The reported failure: without a refresh, every frame after the 3rd 404s."""
    stale, fresh = _Client(budget=3), _Client()
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: fresh)
    monkeypatch.setattr(dl, "fetch_images", lambda client, scan_id: _images(6))

    result = dl.download_images(stale, [SCAN], tmp_path, creds=CREDS, refresh_every=10_000)

    assert result.ok == 6 and result.failed == 0
    assert _frame(tmp_path, 5).read_bytes() == b"bytes::cyl-images/5.png"


def test_download_images_without_credentials_still_fails_the_frames(tmp_path, monkeypatch):
    """Without creds there is nothing to refresh — the frames fail, but readably."""
    monkeypatch.setattr(dl, "fetch_images", lambda client, scan_id: _images(4))

    result = dl.download_images(_Client(budget=2), [SCAN], tmp_path)

    assert result.ok == 2 and result.failed == 2
    assert "expired session" in result.frames[3].error


def test_download_images_refreshes_the_client_used_for_metadata_reads(tmp_path, monkeypatch):
    """`fetch_images` runs on the same session, so it must be renewed too."""
    fresh = _Client()
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: fresh)
    seen: list[object] = []

    def _fetch_images(client, scan_id):
        seen.append(client)
        if len(seen) == 1:
            raise RuntimeError(EXPIRED)
        return _images(1)

    monkeypatch.setattr(dl, "fetch_images", _fetch_images)

    result = dl.download_images(_Client(), [SCAN], tmp_path, creds=CREDS)

    assert result.ok == 1
    assert seen[1] is fresh


# --- download_images: resume ------------------------------------------------


def test_download_images_skips_frames_already_on_disk(tmp_path, monkeypatch):
    client = _Client()
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(3))
    existing = _frame(tmp_path, 1)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"from-an-earlier-run")

    result = dl.download_images(client, [SCAN], tmp_path)

    assert result.total == 3 and result.downloaded == 2 and result.skipped == 1
    assert result.ok == 3 and result.failed == 0
    assert client.bucket.calls == 2  # the existing frame was never re-fetched
    assert existing.read_bytes() == b"from-an-earlier-run"


def test_download_images_re_downloads_a_truncated_zero_byte_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(1))
    stub = _frame(tmp_path, 0)
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_bytes(b"")

    result = dl.download_images(_Client(), [SCAN], tmp_path)

    assert result.skipped == 0 and result.downloaded == 1
    assert stub.read_bytes() == b"bytes::cyl-images/0.png"


def test_download_images_overwrite_refetches_everything(tmp_path, monkeypatch):
    client = _Client()
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(2))
    stale_frame = _frame(tmp_path, 0)
    stale_frame.parent.mkdir(parents=True, exist_ok=True)
    stale_frame.write_bytes(b"stale")

    result = dl.download_images(client, [SCAN], tmp_path, overwrite=True)

    assert result.skipped == 0 and client.bucket.calls == 2
    assert stale_frame.read_bytes() == b"bytes::cyl-images/0.png"


def test_download_log_marks_skipped_frames(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(2))
    existing = _frame(tmp_path, 0)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"already-here")

    result = dl.download_images(_Client(), [SCAN], tmp_path)
    log = tmp_path / "download_log.txt"
    dl.write_download_log(result, log)

    text = log.read_text()
    assert "SKIP scan=1 frame=0" in text
    assert "Summary: 1 downloaded, 1 already present, 0 failed, 2 total" in text


def test_frames_are_written_atomically(tmp_path, monkeypatch):
    """A crash mid-write must not leave a partial frame that resume would then skip."""
    from pathlib import Path

    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(1))
    monkeypatch.setattr(
        Path, "write_bytes", lambda self, data: (_ for _ in ()).throw(OSError("crash"))
    )

    result = dl.download_images(_Client(), [SCAN], tmp_path)

    assert result.failed == 1
    assert not _frame(tmp_path, 0).exists()


# --- download-for-predict ---------------------------------------------------


def _predict_images(count: int) -> list[dict]:
    return [
        {"id": i, "frame_number": i, "object_path": f"cyl-images/{i}.png"} for i in range(count)
    ]


def test_predict_frames_survive_an_expiring_session(tmp_path, monkeypatch):
    import bloomctl.cyl.download_for_predict as dfp

    stale, fresh = _Client(budget=2), _Client()
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: fresh)
    session = storage.StorageSession(stale, CREDS, refresh_every=10_000)

    result, frame_bytes = dfp.download_frames_for_predict(
        session.client, SCAN, _predict_images(5), tmp_path, session=session
    )

    assert result.ok == 5 and result.failed == 0
    assert len(frame_bytes) == 5  # the checksum still covers every frame


def test_one_batch_session_makes_the_refresh_cadence_cumulative_across_scans(tmp_path, monkeypatch):
    """A batch's frames-per-scan is small; only a shared session ever reaches the cadence."""
    import bloomctl.cyl.download_for_predict as dfp

    monkeypatch.setattr(auth, "make_authed_client", lambda creds: _Client())
    session = storage.StorageSession(_Client(), CREDS, refresh_every=3)

    for scan_index in range(2):
        dfp.download_frames_for_predict(
            session.client,
            SCAN,
            _predict_images(2),
            tmp_path / f"scan_{scan_index}",
            session=session,
        )

    assert session.refreshes == 1


# --- CLI --------------------------------------------------------------------


def _patch_cli(monkeypatch, client):
    monkeypatch.setattr("bloomctl.credentials.load_credentials", lambda *a, **k: CREDS)
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: client)
    monkeypatch.setattr(dl, "fetch_scans", lambda *a, **k: [SCAN])
    monkeypatch.setattr(dl, "fetch_genotypes", lambda c, ids: {42: "Spring-32"})
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(2))


def test_cli_rerun_after_a_partial_download_resumes(tmp_path, monkeypatch):
    """End to end: a run that fails halfway, re-run, completes without re-fetching."""
    out = tmp_path / "out"
    stale = _Client(budget=1)
    _patch_cli(monkeypatch, stale)

    first = CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "17957"])
    assert first.exit_code != 0
    assert "Re-running the same command" in first.output

    fresh = _Client()
    _patch_cli(monkeypatch, fresh)
    second = CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "17957"])

    assert second.exit_code == 0, second.output
    assert "1 already present" in second.output
    assert fresh.bucket.calls == 1  # only the frame that had failed
    assert (out / "download_log.txt").read_text().count("SKIP") == 1


def test_cli_passes_credentials_so_a_long_run_can_refresh(tmp_path, monkeypatch):
    """Regression guard for the root cause: the command must hand `creds` down."""
    seen: dict = {}
    _patch_cli(monkeypatch, _Client())
    real = dl.download_images

    def _spy(client, scans, out_dir, **kwargs):
        seen.update(kwargs)
        return real(client, scans, out_dir, **kwargs)

    monkeypatch.setattr(dl, "download_images", _spy)
    result = CliRunner().invoke(
        cli, ["cyl", "download", str(tmp_path / "out"), "--experiment-id", "17957"]
    )

    assert result.exit_code == 0, result.output
    assert seen["creds"] == CREDS
