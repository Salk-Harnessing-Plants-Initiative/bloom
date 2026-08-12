"""A long `cyl download` has to keep working after the client renews its token, and has to
pick up where it left off if it stops early.

The failure these cover: a download runs fine for about an hour, then every remaining frame
comes back "bucket not found". A bucket handle keeps the token it was created with, so one
held for the whole run stops working the moment that token is replaced.
"""

from __future__ import annotations

import errno
import stat
from pathlib import Path

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
    """Serves `budget` objects, then fails the way an expired token does."""

    def __init__(self, budget: int | None = None):
        self.budget = budget
        self.calls = 0

    def download(self, object_path):
        self.calls += 1
        if self.budget is not None and self.calls > self.budget:
            raise RuntimeError(EXPIRED)
        return f"bytes::{object_path}".encode()


class _Client:
    """Stand-in client whose bucket records how many downloads it was asked for."""

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
    """A client that renews its token, like the real one does.

    Asking for a bucket handle gives you one tied to whatever the token is at that moment,
    and only the current token is accepted — so a handle taken before a renewal is dead.
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
                    raise RuntimeError(EXPIRED)
                return f"bytes::{object_path}".encode()

        class _Storage:
            def from_(self, name):
                client.handles_issued += 1
                return _Proxy(client.token)

        self.storage = _Storage()

    def auto_refresh(self):
        """Renew the token, as the client does on its own in the background."""
        self.token = "token-1"


def _images(count: int) -> list[dict]:
    return [{"frame_number": i, "object_path": f"cyl-images/{i}.png"} for i in range(count)]


def _frame(tmp_path, number: int):
    return tmp_path / f"images/Wave2/Day14_2026-05-11/QR-1/{number}.png"


# --- token renewal --------------------------------------------------


def test_a_cached_bucket_handle_dies_when_the_client_refreshes():
    """Holding one handle for the whole run — what the old code did — stops working."""
    client = _RotatingClient()
    cached = client.storage.from_("images")  # taken once, up front
    assert cached.download("cyl-images/0.png") == b"bytes::cyl-images/0.png"

    client.auto_refresh()

    with pytest.raises(RuntimeError, match="Bucket not found"):
        cached.download("cyl-images/1.png")


def test_download_object_resolves_a_fresh_handle_so_it_survives_the_refresh():
    """Asking for a handle each time picks up the renewed token."""
    client = _RotatingClient()
    assert storage.download_object(client, "cyl-images/0.png") == b"bytes::cyl-images/0.png"

    client.auto_refresh()

    assert storage.download_object(client, "cyl-images/1.png") == b"bytes::cyl-images/1.png"


def test_a_whole_download_survives_a_refresh_mid_run(tmp_path, monkeypatch):
    """The whole command keeps going across a renewal, not just the helper."""
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
    """If a handle is ever cached again, this is what catches it."""
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
    """An object that isn't there should fail once, not be asked for twice."""

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
    """Recovering from a renewal must not require signing in again."""

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


def test_a_frame_gets_the_same_permissions_as_any_other_file(tmp_path):
    """Frames must be as readable as the scans.csv sitting beside them, at any umask."""
    frame = tmp_path / "frame.png"
    storage.atomic_write_bytes(frame, b"x")
    ordinary = tmp_path / "scans.csv"
    ordinary.write_text("x")

    assert stat.S_IMODE(frame.stat().st_mode) == stat.S_IMODE(ordinary.stat().st_mode)


def test_a_crash_mid_write_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    dest = tmp_path / "frame.png"
    dest.write_bytes(b"old-bytes")

    monkeypatch.setattr(
        Path, "write_bytes", lambda self, data: (_ for _ in ()).throw(OSError("crash mid-write"))
    )

    with pytest.raises(OSError):
        storage.atomic_write_bytes(dest, b"new-bytes")

    assert dest.read_bytes() == b"old-bytes"
    assert list(tmp_path.glob(".dl-*")) == []


def test_a_failed_write_names_the_file_asked_for_not_the_temp_one(tmp_path, monkeypatch):
    """The temp file is gone by the time anyone reads the error it is named in."""
    dest = tmp_path / "frame.png"

    def _full_disk(self, data):
        raise OSError(errno.ENOSPC, "No space left on device", str(self))

    monkeypatch.setattr(Path, "write_bytes", _full_disk)

    with pytest.raises(OSError) as caught:
        storage.atomic_write_bytes(dest, b"bytes")

    assert caught.value.filename == str(dest), "the temp name is an implementation detail"
    assert ".tmp" not in str(caught.value)
    assert caught.value.errno == errno.ENOSPC, "the full-disk stop reads this"


def test_frames_are_written_atomically(tmp_path, monkeypatch):
    """A crash mid-write must not leave a partial frame that resume would then skip."""
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(1))
    monkeypatch.setattr(
        Path, "write_bytes", lambda self, data: (_ for _ in ()).throw(OSError("crash"))
    )

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


def test_a_truncated_frame_is_taken_on_trust_because_no_size_is_known(tmp_path):
    """Pins the limit of what resume can promise, so the docs cannot drift past it.

    Nothing tells `bloomctl` how long a frame should be — `cyl_images` records the path,
    not the length — so a fragment left by `0.1.0a3` counts as present. The CHANGELOG
    says to download such a directory afresh; this is the code that makes that necessary.
    """
    truncated = tmp_path / "frame.png"
    truncated.write_bytes(b"partial")

    assert storage.already_downloaded(truncated)


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
    """On a fully-resumed run the useful number is how many frames are there, not how many were fetched."""
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


# --- what counts as worth retrying ------------------------------------------


class _ApiError(RuntimeError):
    """Shaped like storage3's StorageApiError: carries a status alongside the message."""

    def __init__(self, message, status):
        super().__init__(f"{{'statusCode': {status}, 'error': e, 'message': {message}}}")
        self.status = status


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch):
    """Keep the backoff from slowing the suite; the delay itself is asserted separately."""
    monkeypatch.setattr(storage.time, "sleep", lambda seconds: None)


@pytest.mark.parametrize(
    ("error", "expected", "why"),
    [
        (_ApiError("Too Many Requests", 429), True, "rate limiting is the whole reason to wait"),
        (_ApiError("Internal Server Error", 500), True, "server-side, may pass on a retry"),
        (_ApiError("Bad Gateway", 502), True, "server-side"),
        (_ApiError("Service Unavailable", 503), True, "server-side"),
        (_ApiError("Object not found", 404), False, "genuinely absent; do not double requests"),
        (_ApiError("Invalid key", 400), False, "client-side, a retry changes nothing"),
        (
            _ApiError("Object not found: cyl-images/scan_500/frame_3.png", 404),
            False,
            "a path containing 500 must not look like a server error",
        ),
    ],
)
def test_retry_decision_uses_the_status_not_the_message(error, expected, why):
    assert storage.is_retryable(error) is expected, why


@pytest.mark.parametrize(
    "name", ["ReadTimeout", "ConnectTimeout", "PoolTimeout", "WriteTimeout", "ConnectError"]
)
def test_network_failures_are_retryable_despite_having_no_message(name):
    """These stringify to '' — there is no text to match, so the type has to decide."""
    import httpx

    error = getattr(httpx, name)("")
    assert str(error) == ""
    assert storage.is_retryable(error) is True


def test_a_dropped_connection_is_retryable():
    import httpx

    error = httpx.RemoteProtocolError("Server disconnected without sending a response.")
    assert storage.is_retryable(error) is True


def test_an_expired_session_is_retryable_even_though_its_status_is_404():
    """Storage reports an expired caller as a missing bucket, so only the message tells them apart."""
    assert storage.is_retryable(_ApiError("Bucket not found", 404)) is True


def test_a_timeout_is_logged_with_its_type_not_an_empty_string(tmp_path, monkeypatch):
    import httpx

    class _TimingOutBucket:
        def download(self, object_path):
            raise httpx.ReadTimeout("")

    client = _Client()
    client.bucket = _TimingOutBucket()
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(1))

    result = dl.download_images(client, [SCAN], tmp_path, workers=1)

    assert result.failed == 1
    assert result.frames[0].error == "ReadTimeout", "an empty error= line is unactionable"


def test_the_retry_waits_before_trying_again(monkeypatch):
    """An immediate second request from every worker is what overwhelms a struggling server."""
    slept: list[float] = []
    monkeypatch.setattr(storage.time, "sleep", lambda seconds: slept.append(seconds))

    client = _Client(budget=0)
    with pytest.raises(storage.StorageError):
        storage.download_object(client, "cyl-images/0.png")

    assert slept == [storage.RETRY_DELAY_SECONDS]


def test_the_retry_asks_for_a_new_bucket_handle():
    """Reusing the first handle for the retry would defeat the point of retrying at all.

    A handle that was issued before the token changed stays broken however many times it is
    used, so recovering means asking for another one.
    """

    class _StaleFirstHandle:
        def __init__(self):
            self.handles = 0

        def from_(self, name):
            self.handles += 1
            issued = self.handles

            class _Proxy:
                def download(self, object_path):
                    if issued == 1:
                        raise RuntimeError(EXPIRED)
                    return b"fetched with a fresh handle"

            return _Proxy()

    client = _Client()
    client.storage = _StaleFirstHandle()

    assert storage.download_object(client, "cyl-images/0.png") == b"fetched with a fresh handle"
    assert client.storage.handles == 2, "the retry must resolve its own handle"


def test_the_sweep_reaches_temp_files_outside_the_images_tree(tmp_path):
    """`scans.csv` and `download_log.txt` write atomically too, beside themselves in the root.

    Sweeping only `images/` left those two behind for good after a hard kill.
    """
    (tmp_path / "images" / "Wave2").mkdir(parents=True)
    beside_a_frame = tmp_path / "images" / "Wave2" / ".dl-deadbeef.tmp"
    beside_the_csv = tmp_path / ".dl-cafebabe.tmp"
    beside_a_frame.write_bytes(b"half a frame")
    beside_the_csv.write_bytes(b"half a scans.csv")

    removed = storage.sweep_orphan_temps(tmp_path)

    assert removed == 2
    assert not beside_a_frame.exists() and not beside_the_csv.exists()


def test_the_sweep_leaves_the_real_output_alone(tmp_path):
    """It runs at the start of every download, over a directory holding finished work."""
    (tmp_path / "images").mkdir()
    csv = tmp_path / "scans.csv"
    csv.write_text("scan_id\n1\n")
    frame = tmp_path / "images" / "0.png"
    frame.write_bytes(b"a real frame")

    assert storage.sweep_orphan_temps(tmp_path) == 0
    assert csv.read_text() == "scan_id\n1\n"
    assert frame.read_bytes() == b"a real frame"
