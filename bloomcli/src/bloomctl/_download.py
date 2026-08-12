"""The download mechanism, shared by every instrument's download command.

Nothing here names a database table or column, and nothing branches on which instrument is
calling. What belongs in a command's own module: its queries, its CSV columns, its on-disk
path layout, and the loop that walks its rows. What belongs here: everything about *doing*
the download safely — atomic writes, resume, bounded concurrency, collision detection,
progress and logging.

That line is the rule for adding to this file. A change that needs to know a column name, or
needs an ``if instrument == ...``, belongs in the caller instead.
"""

from __future__ import annotations

import errno
import itertools
import json
import os
import threading
import time
import unicodedata
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, NamedTuple
from uuid import uuid4

import click

from ._storage import already_downloaded, atomic_write_bytes, download_object

RETRY_HINT = "Some frames are failing — re-run this command afterwards and it will retry them."


# How far the rate must fall below the window's before the window is started again. Well clear
# of network variation; the drop it looks for is orders of magnitude.
BURST_DROP_FACTOR = 10


# Progress lines the rate and time-remaining estimate are averaged over (about a minute).
RATE_WINDOW_SAMPLES = 12


# Nothing more can be written: the disk is full, or the quota that stands in for it is spent.
# Shared lab storage is usually quota-limited, where the kernel reports EDQUOT and never
# ENOSPC. Looked up rather than named, because EDQUOT is absent on some platforms.
OUT_OF_SPACE = frozenset(
    code for code in (errno.ENOSPC, getattr(errno, "EDQUOT", None)) if code is not None
)


# Frames are fetched one HTTP request each, so downloading several at once mostly overlaps
# waiting. 8 is a modest default that speeds up a large experiment without hammering the server.
DEFAULT_WORKERS = 8


# How often a long run reports what it has done. Frequent enough that the command never looks
# hung, rare enough that a log file doesn't fill up with it.
PROGRESS_INTERVAL_SECONDS = 5.0


# Upper limit on concurrent downloads, applied both to the flag and inside download_images so
# no caller can open an unbounded number of connections.
MAX_WORKERS = 64


def safe_component(value: Any) -> str:
    """Turn a database value into a single safe path segment.

    Every part of a frame's destination comes from the database (`qr_code`, `date_scanned`,
    `frame_number`, ...). Stripping separators keeps an odd value from steering the write
    somewhere else on disk — note that joining an absolute path would otherwise discard the
    output directory entirely.
    """
    # A colon matters on Windows: "C:name" is drive-relative, so joining it throws away the
    # output directory, and "name:stream" writes into a hidden stream instead of the file.
    # Note: whitespace is deliberately preserved. `qr_code` is UNIQUE per wave, so "QR-1" and
    # "QR-1 " are two different plants, and trimming would merge their frames into one directory.
    cleaned = str(value).replace("\\", "_").replace("/", "_").replace(":", "_").replace("\0", "_")
    if cleaned in {"", ".", ".."} or set(cleaned) == {"."}:
        return "_"
    return cleaned


def contained_dest(out_dir: Path, relative: str) -> Path:
    """Join ``relative`` onto ``out_dir``, refusing anything that would escape it.

    A second check behind `safe_component`, so nothing can reach a write outside the output
    directory. The check is on the path as written, without consulting the filesystem:
    resolving it would follow symlinks, which would reject the perfectly ordinary case of
    `images/` pointing at another disk.
    """
    normalized = os.path.normpath(relative)
    if os.path.isabs(normalized) or normalized.split(os.sep)[0] == os.pardir:
        raise ValueError(f"refusing to write outside {out_dir}: {relative}")
    return Path(out_dir) / normalized


# Records which experiment an output directory holds, so a later run into the same directory
# can tell whether the frames already there belong to what it is about to download.
MANIFEST_NAME = ".bloomctl-download.json"


def read_manifest(out_dir: Path) -> dict[str, Any] | None:
    """The selector recorded in ``out_dir``, or None if there isn't a readable one."""
    path = Path(out_dir) / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_manifest(out_dir: Path, identity: dict[str, Any]) -> None:
    """Record what this directory holds, so a later run can check before resuming into it."""
    path = Path(out_dir) / MANIFEST_NAME
    body = json.dumps(identity, indent=2, sort_keys=True, default=str) + "\n"
    atomic_write_bytes(path, body.encode("utf-8"))


def selector_of(keys: tuple[str, ...], options: dict[str, Any]) -> dict[str, Any]:
    """The subset of ``options`` that decides which rows a run downloads.

    Each command passes its own ``keys``. Resolved ids belong in here rather than the names
    they were typed as, so selecting the same thing by name on one run and by id on the next
    still counts as the same download.
    """
    return {key: options.get(key) for key in keys}


def holds_an_unidentified_download(out_dir: Path) -> bool:
    """True if the directory already has frames but no record of which download they are.

    Without the record there is no way to tell whether an incoming download belongs here, and
    guessing wrong mixes two experiments' images into one tree — resume then skips frames that
    look present but belong to the other one.
    """
    return (Path(out_dir) / "images").exists() and read_manifest(out_dir) is None


def describe_manifest_mismatch(existing: dict[str, Any] | None, selector: dict[str, Any]) -> str:
    """List how this run's selection differs from the one the directory holds, or "" if it matches.

    One selection per directory. Re-running the same command resumes; downloading a different
    selection here would leave two sets of images in one tree with a `scans.csv` describing
    only the newer one.
    """
    if existing is None:
        return ""
    return "; ".join(
        f"{key} was {existing.get(key)!r}, now {value!r}"
        for key, value in selector.items()
        if existing.get(key) != value
    )


@dataclass
class FrameResult:
    """Outcome of one frame download."""

    scan_id: Any
    frame_number: Any
    object_path: str
    ok: bool
    error: str = ""
    skipped: bool = False  # already on disk from an earlier run
    unlisted: bool = False  # set on a scan whose frame list could not be fetched at all
    no_frames: bool = False  # set on a scan that listed cleanly but has no images
    note: str = ""  # something worth saying about a download that still succeeded

    @property
    def scan_level(self) -> bool:
        """True when this records a problem with a whole scan rather than one frame."""
        return self.unlisted or self.no_frames


@dataclass
class DownloadResult:
    """Aggregate outcome of a `download_images` run."""

    frames: list[FrameResult]
    disk_full: bool = False  # the run stopped early: nowhere left to write

    @property
    def ok(self) -> int:
        """Frames present on disk after the run (downloaded now or already there)."""
        return sum(1 for f in self.frames if f.ok)

    @property
    def downloaded(self) -> int:
        return sum(1 for f in self.frames if f.ok and not f.skipped)

    @property
    def skipped(self) -> int:
        return sum(1 for f in self.frames if f.skipped)

    @property
    def failed(self) -> int:
        """Frames known to be missing. Whole-scan problems are counted separately, since how
        many frames they involve is unknown."""
        return sum(1 for f in self.frames if not f.ok and not f.scan_level)

    @property
    def scans_unlisted(self) -> int:
        """Scans whose frame list couldn't be fetched, so an unknown number of frames is missing."""
        return sum(1 for f in self.frames if f.unlisted)

    @property
    def scans_without_frames(self) -> int:
        """Scans that listed cleanly but hold no images at all."""
        return sum(1 for f in self.frames if f.no_frames)

    @property
    def total(self) -> int:
        """Frames that were actually listed."""
        return sum(1 for f in self.frames if not f.scan_level)

    @property
    def incomplete(self) -> bool:
        """True if this run failed to fetch something it should have fetched.

        A scan with no images recorded is noted in the log but is not a failure: there is
        nothing to download, so every re-run would report it again and the command could
        never succeed.
        """
        return bool(self.failed or self.scans_unlisted)


class Fetched(NamedTuple):
    """What became of one object fetch."""

    ok: bool
    skipped: bool
    error: str
    note: str = ""


def download_to(
    client: Any,
    object_path: str,
    dest: Path,
    *,
    bucket: str,
    expected_size: int | None = None,
    stop: threading.Event | None = None,
) -> Fetched:
    """Fetch one object to ``dest``. Never raises.

    Any failure (missing object, server error, write error) comes back as ``error`` instead of
    an exception, so one bad image can't abort a run. Safe to call from a worker thread.

    An image already complete on disk is reported as skipped without making a request, which is
    what makes an interrupted run cheap to pick back up. ``expected_size`` makes that a real
    completeness check where the database records a size; without it, all that can be said is
    that the file isn't empty.

    A recorded size that disagrees with what storage actually serves comes back as a note. It is
    not an error — the bytes arrived intact — but it has to be visible, because the resume check
    will never be satisfied by that object and every future run will fetch it again. Silently
    re-downloading a whole experiment on every run is the failure this note prevents.

    ``stop`` is set when the disk fills. Images that have not started yet are recorded as failed
    without being fetched — there is nowhere to put them, and a large experiment would otherwise
    pull hundreds of gigabytes only to throw them away. The few already in flight run to
    completion, so a little work carries on past the point the disk fills, bounded by the number
    of workers.
    """
    if stop is not None and stop.is_set():
        return Fetched(False, False, "nowhere left to write — nothing further was downloaded")
    try:
        if already_downloaded(dest, expected_size):
            return Fetched(True, True, "")
        data = download_object(client, object_path, bucket=bucket)
        atomic_write_bytes(dest, data)
        note = ""
        if expected_size is not None and len(data) != expected_size:
            note = (
                f"downloaded {len(data)} bytes but the database records {expected_size}; "
                f"re-runs will fetch this one again until the recorded size is corrected"
            )
        return Fetched(True, False, "", note)
    except OSError as exc:
        # A full disk isn't this image's problem, it's every remaining image's.
        if exc.errno in OUT_OF_SPACE and stop is not None:
            stop.set()
        return Fetched(False, False, str(exc))
    except Exception as exc:  # per-image: record and continue
        return Fetched(False, False, str(exc))


# --- bounded concurrency ----------------------------------------------------


def run_bounded(
    work: list[Any],
    run_one: Callable[[Any], FrameResult],
    workers: int,
    *,
    window_factor: int = 4,
    on_done: Callable[[Any], None] | None = None,
) -> list[FrameResult]:
    """Run ``run_one`` over ``work`` across ``workers`` threads, a window at a time.

    Queueing every frame up front would cost hundreds of MB on a large experiment before the
    first byte arrives. Keeping a limited number in flight uses flat memory and still keeps
    every thread busy. Results are stored by position, so the order matches ``work``.

    ``on_done`` receives each finished item. Completed work is collected here, on the
    calling thread, so it needs no locking of its own.
    """
    results: list[Any] = [None] * len(work)
    window = max(workers * window_factor, workers)
    pending: dict[Future, int] = {}
    remaining = enumerate(work)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index, item in itertools.islice(remaining, window):
            pending[pool.submit(run_one, item)] = index
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                outcome = future.result()
                results[pending.pop(future)] = outcome
                if on_done is not None:
                    on_done(outcome)
            for index, item in itertools.islice(remaining, len(done)):
                pending[pool.submit(run_one, item)] = index
    return results


def fetch_all(
    work: list[Any],
    run_one: Callable[[Any], FrameResult],
    *,
    workers: int,
    on_done: Callable[[FrameResult], None] | None = None,
) -> list[FrameResult]:
    """Run every item, concurrently or sequentially, whichever the worker count asks for.

    Never starts more threads than there are items, than were asked for, or than the limit.
    """
    n = min(workers, MAX_WORKERS, len(work)) if work else 0
    if n <= 1:
        outcomes = []
        for item in work:
            outcome = run_one(item)
            outcomes.append(outcome)
            if on_done is not None:
                on_done(outcome)
        return outcomes
    return run_bounded(work, run_one, n, on_done=on_done)


class CollidingFrames(ValueError):
    """Two or more frames would be written to the same file."""


def filesystem_folds_case(out_dir: Path) -> bool:
    """Whether this filesystem treats two names differing only by case as the same file.

    Has to be measured rather than assumed: macOS is case-insensitive by default and Linux is
    not, and `os.path.normcase` reports nothing useful on either (it only folds on Windows).
    """
    root = Path(out_dir)
    probe = root / ".bloomctl-case-probe"
    try:
        root.mkdir(parents=True, exist_ok=True)
        if probe.exists():
            return False  # someone else's file; leave it alone rather than delete it
        probe.touch()
        try:
            return (root / ".BLOOMCTL-CASE-PROBE").exists()
        finally:
            probe.unlink(missing_ok=True)
    except OSError:
        return False  # can't tell — don't invent collisions


def _path_key(path: Path, fold: bool) -> str:
    """A comparison key for a destination, matching how the filesystem compares names."""
    text = os.path.normcase(str(path))
    if fold:
        # macOS also normalises unicode, so compose before folding.
        text = unicodedata.normalize("NFC", text).casefold()
    return text


def find_collisions(
    out_dir: Path,
    work: list[Any],
    dest_of: Callable[[Any], Path],
    describe: Callable[[Any], str],
) -> list[str]:
    """Describe every pair of items that would land on the same file.

    Destinations are built from database values, several of which can be empty — and two rows
    with an empty value are not caught by a uniqueness constraint. Two rows can therefore share
    a filename, and without this check the second is quietly skipped as already-downloaded and
    its image never arrives.

    The comparison is on the real destination path, compared the way this filesystem compares
    names: on macOS `st0-001` and `ST0-001` are two database values but one file, so matching
    the raw strings would miss exactly the collisions that matter. On a case-sensitive
    filesystem they are genuinely different files and are left alone.

    ``dest_of`` and ``describe`` come from the caller, so the path layout and the wording of
    the clash stay with the instrument that owns them.
    """
    fold = filesystem_folds_case(Path(out_dir))
    seen: dict[str, tuple[str, Path]] = {}
    clashes: list[str] = []
    for item in work:
        try:
            dest = dest_of(item)
        except (KeyError, TypeError, ValueError):
            continue  # malformed row; reported per-image during the download
        key = _path_key(dest, fold)
        if key in seen:
            first_label, first_dest = seen[key]
            clashes.append(
                f"{first_label} ({first_dest}) and {describe(item)} ({dest}) are the same file"
            )
        else:
            seen[key] = (describe(item), dest)
    return clashes


class ProgressReporter:
    """Prints how far a run has got, at most every ``interval`` seconds.

    A large download takes hours; without this it prints one line and then goes quiet, and
    there is no way to tell a working run from a stuck one. Goes to stderr so the paths and
    summary on stdout stay usable in a script.
    """

    def __init__(
        self, *, interval: float | None = None, now=time.monotonic, noun: str = "frames"
    ):
        self._interval = PROGRESS_INTERVAL_SECONDS if interval is None else interval
        self._now = now
        self._noun = noun
        self._last = 0.0
        self._phase = ""
        self._samples: deque[tuple[float, int]] = deque(maxlen=RATE_WINDOW_SAMPLES)
        self._failures_seen = 0
        self._mentioned_retry = False

    def __call__(self, phase: str, done: int, total: int, failed: int = 0) -> None:
        moment = self._now()
        # Always show the first and last of a phase, so short runs still say something and a
        # finished phase never sits at 97%.
        edge = phase != self._phase or done == total
        if not edge and moment - self._last < self._interval:
            return
        if phase != self._phase:
            self._samples.clear()  # each phase moves at its own pace
        self._last = moment
        self._phase = phase
        rate, seconds_left = self._pace(moment, done, total)
        arrived = failed - self._failures_seen
        self._failures_seen = failed
        line = format_progress(
            phase, done, total, failed, rate=rate, seconds_left=seconds_left,
            new_failures=arrived, noun=self._noun,
        )
        click.echo(f"  {line}", err=True)
        if failed and not self._mentioned_retry:  # once, when failures first appear
            self._mentioned_retry = True
            click.echo(f"  {RETRY_HINT}", err=True)

    def _pace(self, moment: float, done: int, total: int) -> tuple[float | None, float | None]:
        """Frames per second and seconds remaining, over a recent window.

        A window rather than the whole run, so a connection that changes speed shows up within
        a minute instead of being averaged away over hours.
        """
        latest = (moment, done)
        # A resumed run skips thousands of frames in seconds. Once it reaches frames that have
        # to be fetched, the window is still quoting that burst, so start the window again.
        if len(self._samples) >= 2:
            just_now = _rate_between(self._samples[-1], latest)
            window = _rate_between(self._samples[0], latest)
            if just_now is not None and window is not None and just_now * BURST_DROP_FACTOR < window:
                self._samples.clear()
        self._samples.append(latest)
        rate = _rate_between(self._samples[0], latest)
        if rate is None:
            return None, None
        remaining = max(total - done, 0)
        return rate, (remaining / rate if remaining else None)


def format_progress(
    phase: str,
    done: int,
    total: int,
    failed: int = 0,
    *,
    rate: float | None = None,
    seconds_left: float | None = None,
    new_failures: int = 0,
    noun: str = "frames",
) -> str:
    """One progress line, e.g. ``349/60,336 frames (0.6%)  6.3/s  ~2h39m left, 33 failed (+12)``.

    ``(+N)`` counts failures since the last line, so its absence means they have stopped.
    """
    unit = "scans" if phase == "listing" else noun
    prefix = f"Listing {noun}: " if phase == "listing" else ""
    # Held below 100 until the last frame lands: rounding alone reads 100.0% with frames
    # still outstanding on any run of a few thousand.
    share = min(done * 100 / total, 99.9) if total and done < total else (100.0 if total else 0)
    percent = f" ({share:.1f}%)" if total else ""
    pace = f"  {format_rate(rate)}" if rate else ""
    left = f"  ~{format_duration(seconds_left)} left" if seconds_left else ""
    problem = f", {failed:,} failed" if failed else ""
    arrived = f" (+{new_failures:,})" if failed and new_failures > 0 else ""
    return f"{prefix}{done:,}/{total:,} {unit}{percent}{pace}{left}{problem}{arrived}"


def _one_line(text: Any) -> str:
    """Collapse whitespace so one record can never span more than one line.

    `object_path` and the error text both come from the server, and a multi-line error (an
    httpx message, say) would otherwise emit continuation lines that read like frame records.
    """
    return " ".join(str(text).split())


def write_download_log(result: DownloadResult, path: Path, *, noun: str = "frame") -> None:
    """Write a per-item download log (one line each) with a summary footer.

    ``noun`` names the unit of work, so a plate run logs captures rather than frames.

    Written atomically, so a failed write leaves the previous log intact.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for f in result.frames:
        if f.unlisted:
            lines.append(
                f"UNLISTED scan={f.scan_id} ({noun} count unknown)  error={_one_line(f.error)}"
            )
            continue
        if f.no_frames:
            lines.append(f"NOFRAMES scan={f.scan_id} (no images recorded for this scan)")
            continue
        status = "SKIP" if f.skipped else "OK  " if f.ok else "FAIL"
        line = f"{status} scan={f.scan_id} {noun}={f.frame_number} {_one_line(f.object_path)}"
        if not f.ok:
            line += f"  error={_one_line(f.error)}"
        if f.note:
            line += f"  note={_one_line(f.note)}"
        lines.append(line)
    summary = (
        f"\nSummary: {result.ok}/{result.total} {noun}s present "
        f"({result.downloaded} downloaded this run, {result.skipped} already on disk), "
        f"{result.failed} failed"
    )
    if result.scans_unlisted:
        summary += f", {result.scans_unlisted} scan(s) could not be listed ({noun}s unknown)"
    if result.scans_without_frames:
        summary += f", {result.scans_without_frames} scan(s) have no images"
    if result.disk_full:
        summary += (
            f" — the disk filled up or the storage quota was spent, so the remaining {noun}s "
            "were never attempted"
        )
    lines.append(summary)
    atomic_write_bytes(path, ("\n".join(lines) + "\n").encode("utf-8"))


def write_failed(path: Path, exc: OSError) -> click.ClickException:
    """The message for a failed write, with advice matched to why it failed."""
    advice = (
        "Free some space, or download somewhere else."
        if exc.errno in OUT_OF_SPACE
        else "Check the path is spelled correctly and that you have permission to write there."
    )
    return click.ClickException(f"cannot write to {path}: {exc.strerror or exc}. {advice}")


def ensure_writable(out_dir: Path) -> None:
    """Create ``out_dir`` and fail now if nothing can be written to it.

    Probes with a real file: `os.access` answers from the permission bits, which is the wrong
    answer on network mounts and as root.

    The parent has to exist already — only the last directory is created. `parents=True` would
    happily build the whole of `/Volumes/LabDrive/run3` while the drive was unmounted, and
    then fill the boot disk with an experiment the scientist meant to put on the drive.
    """
    path = Path(out_dir)
    parent = path.parent
    if not parent.is_dir():
        raise click.ClickException(
            f"cannot write to {path}: {parent} does not exist. Check the path is spelled "
            f"correctly, and if it is on a removable or network drive, that it is mounted. "
            f"Only the last directory is created for you."
        )
    # Named like the other temps so `sweep_orphan_temps` collects one left by a hard kill.
    probe = path / f".dl-probe-{uuid4().hex}.tmp"
    try:
        path.mkdir(exist_ok=True)
        probe.write_bytes(b"bloomctl write test")  # bytes, so a full disk fails here too
    except OSError as exc:
        # A full disk is not a typo, and telling someone to check their spelling when the
        # disk is full sends them off after the wrong thing entirely.
        raise write_failed(path, exc) from exc
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


def _rate_between(first: tuple[float, int], second: tuple[float, int]) -> float | None:
    """Frames per second between two progress samples, or None if it can't be measured."""
    elapsed = second[0] - first[0]
    progressed = second[1] - first[1]
    if elapsed <= 0 or progressed <= 0:
        return None
    return progressed / elapsed


def format_rate(rate: float) -> str:
    """Frames per second, e.g. ``6.3/s`` or ``44/s``."""
    return f"{rate:.1f}/s" if rate < 10 else f"{rate:,.0f}/s"


def format_duration(seconds: float) -> str:
    """A rough duration, e.g. ``2h39m``, ``17m``, ``45s``."""
    whole = max(int(seconds), 0)
    if whole >= 3600:
        return f"{whole // 3600}h{(whole % 3600) // 60:02d}m"
    if whole >= 60:
        return f"{whole // 60}m"
    return f"{whole}s"
