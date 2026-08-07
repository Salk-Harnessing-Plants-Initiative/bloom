"""bloom#525 — long `cyl download` runs must survive a token refresh, and must resume.

The reported failure: a run downloads fine for ~1hr, then every remaining object 404s with
"Bucket not found". The cause was NOT the session expiring — supabase-py refreshes the JWT on
its own timer. The cause was caching `client.storage.from_("images")` above the download loop:
a bucket proxy captures the Authorization header at construction, so the cached handle kept
presenting the token it was born with. These tests pin that behaviour down.
"""

from __future__ import annotations

import os
import stat

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
MISSING = "{'statusCode': 404, 'error': not_found, 'message': Object not found}"


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
    """Minimal supabase-client double. `storage.from_()` hands back the same bucket each time."""

    def __init__(self, budget=None):
        self.bucket = _Bucket(budget)
        self.handles_issued = 0
        client = self

        class _Storage:
            def from_(self, name):
                client.handles_issued += 1
                return client.bucket

        self.storage = _Storage()


class _RotatingClient:
    """Models supabase-py's real behaviour around an auto-refresh.

    `storage.from_()` returns a proxy that captures the *current* token, exactly as
    `SyncBucketProxy` snapshots the Authorization header. The server only accepts the token
    that is current now, so a proxy captured before a refresh is dead.
    """

    def __init__(self):
        self.token = "token-0"
        self.handles_issued = 0
        client = self

        class _Proxy:
            def __init__(self, captured):
                self.captured = captured

            def download(self, object_path):
                if self.captured != client.token:
                    raise RuntimeError(EXPIRED)  # what Storage tells a stale caller
                return f"bytes::{object_path}".encode()

        class _Storage:
            def from_(self, name):
                client.handles_issued += 1
                return _Proxy(client.token)

        self.storage = _Storage()

    def auto_refresh(self):
        """What supabase-py's background timer does: rotate to a new valid token."""
        self.token = "token-1"


def _images(count: int) -> list[dict]:
    return [{"frame_number": i, "object_path": f"cyl-images/{i}.png"} for i in range(count)]


def _frame(tmp_path, number: int):
    return tmp_path / f"images/Wave2/Day14_2026-05-11/QR-1/{number}.png"


# --- the actual root cause --------------------------------------------------


def test_a_cached_bucket_handle_dies_when_the_client_refreshes():
    """Characterises the bug: this is exactly what the pre-fix code did."""
    client = _RotatingClient()
    cached = client.storage.from_("images")  # hoisted above the loop, as the old code did
    assert cached.download("cyl-images/0.png") == b"bytes::cyl-images/0.png"

    client.auto_refresh()  # the library rotates the token underneath us

    with pytest.raises(RuntimeError, match="Bucket not found"):
        cached.download("cyl-images/1.png")


def test_download_object_resolves_a_fresh_handle_so_it_survives_the_refresh():
    """The fix: resolve the bucket per call, and the refreshed token is picked up."""
    client = _RotatingClient()
    assert storage.download_object(client, "cyl-images/0.png") == b"bytes::cyl-images/0.png"

    client.auto_refresh()

    assert storage.download_object(client, "cyl-images/1.png") == b"bytes::cyl-images/1.png"


def test_a_whole_download_survives_a_refresh_mid_run(tmp_path, monkeypatch):
    """End to end: the reported 'downloads for a while then fails everything' shape."""
    client = _RotatingClient()
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(6))

    original = client.storage.from_

    def _refresh_after_three(name):
        if client.handles_issued == 3:
            client.auto_refresh()
        return original(name)

    client.storage.from_ = _refresh_after_three

    result = dl.download_images(client, [SCAN], tmp_path, workers=1)

    assert result.ok == 6 and result.failed == 0
    assert _frame(tmp_path, 5).read_bytes() == b"bytes::cyl-images/5.png"


def test_each_frame_gets_its_own_bucket_handle(tmp_path, monkeypatch):
    """Regression guard: any future caching of the handle reintroduces bloom#525."""
    client = _Client()
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(5))

    dl.download_images(client, [SCAN], tmp_path, workers=1)

    assert client.handles_issued == 5


# --- retry policy -----------------------------------------------------------


def test_an_expired_looking_failure_is_retried_once():
    client = _Client(budget=0)
    with pytest.raises(storage.StorageError):
        storage.download_object(client, "cyl-images/0.png")
    assert client.bucket.calls == 2  # first attempt + one retry


def test_a_missing_object_is_not_retried():
    """A genuinely absent object must not double the request count across an experiment."""

    class _MissingBucket:
        def __init__(self):
            self.calls = 0

        def download(self, object_path):
            self.calls += 1
            raise RuntimeError(MISSING)

    client = _Client()
    client.bucket = _MissingBucket()

    with pytest.raises(storage.StorageError):
        storage.download_object(client, "cyl-images/0.png")
    assert client.bucket.calls == 1


def test_no_credentials_are_needed_to_recover(monkeypatch):
    """The fix must never re-authenticate — that was the re-auth-storm defect."""

    def _must_not_authenticate(creds):
        raise AssertionError("download must not re-authenticate")

    monkeypatch.setattr(auth, "make_authed_client", _must_not_authenticate)
    client = _RotatingClient()
    client.auto_refresh()
    assert storage.download_object(client, "cyl-images/0.png") == b"bytes::cyl-images/0.png"


def test_an_expired_session_is_named_instead_of_a_missing_bucket():
    client = _Client(budget=0)
    with pytest.raises(storage.StorageError) as excinfo:
        storage.download_object(client, "cyl-images/0.png")

    message = str(excinfo.value)
    assert "expired session" in message
    assert "Bucket not found" in message  # raw storage text still there to debug with


def test_an_unrelated_error_message_is_left_alone():
    class _BrokenBucket:
        def download(self, object_path):
            raise RuntimeError("disk on fire")

    client = _Client()
    client.bucket = _BrokenBucket()

    with pytest.raises(storage.StorageError) as excinfo:
        storage.download_object(client, "cyl-images/0.png")
    assert str(excinfo.value) == "disk on fire"


# --- file writing -----------------------------------------------------------


def test_downloaded_frames_are_group_and_world_readable(tmp_path):
    """mkstemp creates 0600; frames must not be owner-only next to a 0644 scans.csv."""
    dest = tmp_path / "frame.png"
    storage.atomic_write_bytes(dest, b"x")

    mode = stat.S_IMODE(dest.stat().st_mode)
    assert mode == 0o644 & ~storage._UMASK


def test_a_crash_mid_write_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    dest = tmp_path / "frame.png"
    dest.write_bytes(b"old-bytes")

    def _boom(fd, mode):
        os.close(fd)
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(os, "fdopen", _boom)

    with pytest.raises(OSError):
        storage.atomic_write_bytes(dest, b"new-bytes")

    assert dest.read_bytes() == b"old-bytes"
    assert list(tmp_path.glob(".dl-*")) == []


def test_a_failed_write_does_not_leak_a_file_descriptor(tmp_path, monkeypatch):
    """If os.fdopen raises it never took ownership of the fd — we must close it ourselves."""
    closed: list[int] = []
    real_close = os.close

    def _track_close(fd):
        closed.append(fd)
        real_close(fd)

    def _boom(fd, mode):
        raise OSError("fdopen failed")

    monkeypatch.setattr(os, "fdopen", _boom)
    monkeypatch.setattr(os, "close", _track_close)

    with pytest.raises(OSError):
        storage.atomic_write_bytes(tmp_path / "frame.png", b"x")

    assert len(closed) == 1  # the mkstemp fd was closed exactly once


def test_frames_are_written_atomically(tmp_path, monkeypatch):
    """A crash mid-write must not leave a partial frame that resume would then skip."""
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(1))

    def _boom(fd, mode):
        os.close(fd)
        raise OSError("crash")

    monkeypatch.setattr(os, "fdopen", _boom)

    result = dl.download_images(_Client(), [SCAN], tmp_path, workers=1)

    assert result.failed == 1
    assert not _frame(tmp_path, 0).exists()


# --- resume -----------------------------------------------------------------


def test_already_downloaded_ignores_empty_and_missing_files(tmp_path):
    missing = tmp_path / "missing.png"
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    complete = tmp_path / "complete.png"
    complete.write_bytes(b"x")

    assert not storage.already_downloaded(missing)
    assert not storage.already_downloaded(empty)
    assert storage.already_downloaded(complete)


def test_already_downloaded_checks_the_expected_size_when_one_is_known(tmp_path):
    """A truncated frame from a pre-atomic-write release must not pass as complete."""
    truncated = tmp_path / "frame.png"
    truncated.write_bytes(b"partial")

    assert storage.already_downloaded(truncated)  # size unknown: only "non-empty"
    assert not storage.already_downloaded(truncated, expected_size=815689)
    assert storage.already_downloaded(truncated, expected_size=len(b"partial"))


def test_download_images_skips_frames_already_on_disk(tmp_path, monkeypatch):
    client = _Client()
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(3))
    existing = _frame(tmp_path, 1)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"from-an-earlier-run")

    result = dl.download_images(client, [SCAN], tmp_path, workers=1)

    assert result.total == 3 and result.downloaded == 2 and result.skipped == 1
    assert result.ok == 3 and result.failed == 0
    assert client.bucket.calls == 2  # the existing frame was never re-fetched
    assert existing.read_bytes() == b"from-an-earlier-run"


def test_download_images_re_downloads_a_truncated_zero_byte_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(1))
    stub = _frame(tmp_path, 0)
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_bytes(b"")

    result = dl.download_images(_Client(), [SCAN], tmp_path, workers=1)

    assert result.skipped == 0 and result.downloaded == 1
    assert stub.read_bytes() == b"bytes::cyl-images/0.png"


def test_download_images_overwrite_refetches_everything(tmp_path, monkeypatch):
    client = _Client()
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(2))
    stale_frame = _frame(tmp_path, 0)
    stale_frame.parent.mkdir(parents=True, exist_ok=True)
    stale_frame.write_bytes(b"stale")

    result = dl.download_images(client, [SCAN], tmp_path, overwrite=True, workers=1)

    assert result.skipped == 0 and client.bucket.calls == 2
    assert stale_frame.read_bytes() == b"bytes::cyl-images/0.png"


def test_download_log_marks_skipped_frames(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(2))
    existing = _frame(tmp_path, 0)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"already-here")

    result = dl.download_images(_Client(), [SCAN], tmp_path, workers=1)
    log = tmp_path / "download_log.txt"
    dl.write_download_log(result, log)

    text = log.read_text()
    assert "SKIP scan=1 frame=0" in text
    assert "2/2 frames present" in text
    assert "1 downloaded this run, 1 already on disk" in text


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

    first = CliRunner().invoke(
        cli, ["cyl", "download", str(out), "--experiment-id", "17957", "--workers", "1"]
    )
    assert first.exit_code != 0
    assert "Re-running the same command" in first.output

    fresh = _Client()
    _patch_cli(monkeypatch, fresh)
    second = CliRunner().invoke(
        cli, ["cyl", "download", str(out), "--experiment-id", "17957", "--workers", "1"]
    )

    assert second.exit_code == 0, second.output
    assert "1 already on disk" in second.output
    assert fresh.bucket.calls == 1  # only the frame that had failed
    assert (out / "download_log.txt").read_text().count("SKIP") == 1


def test_a_fully_resumed_run_reports_frames_present_not_zero_downloaded(tmp_path, monkeypatch):
    """'Downloaded 0/414000' read as total failure; the count that matters is what's present."""
    out = tmp_path / "out"
    _patch_cli(monkeypatch, _Client())
    CliRunner().invoke(
        cli, ["cyl", "download", str(out), "--experiment-id", "17957", "--workers", "1"]
    )

    again = CliRunner().invoke(
        cli, ["cyl", "download", str(out), "--experiment-id", "17957", "--workers", "1"]
    )

    assert again.exit_code == 0, again.output
    assert "2/2 frames present" in again.output
    assert "0 downloaded this run, 2 already on disk" in again.output
