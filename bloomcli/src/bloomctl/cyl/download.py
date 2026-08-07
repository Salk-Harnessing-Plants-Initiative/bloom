"""`bloomctl cyl download`: metadata (scans.csv) + per-frame images.

Pure helpers (column mapping, paths) are separated from the supabase/storage I/O
so the contract is unit-testable without a live server.
"""

from __future__ import annotations

import csv
import itertools
import json
import os
import unicodedata
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import click

from ..credentials import DEFAULT_PROFILE
from ._storage import (
    already_downloaded,
    atomic_write_bytes,
    download_object,
    sweep_orphan_temps,
)

# Frames are fetched one HTTP request each, so downloading several at once mostly overlaps
# waiting. 8 is a modest default that speeds up a large experiment without hammering the server.
DEFAULT_WORKERS = 8

# Upper limit on concurrent downloads, applied both to the flag and inside download_images so
# no caller can open an unbounded number of connections.
MAX_WORKERS = 64

# scans.csv schema: (output column, source key in a cyl_scans_extended row).
# Order matches the legacy CLI's predict-container contract; `genotype` is
# inserted after `accession_id`, and `scan_path` is derived (relative).
_COLUMNS: list[tuple[str, str | None]] = [
    ("scan_id", "scan_id"),
    ("plant_qr_code", "qr_code"),
    ("scan_path", None),  # derived
    ("scanner_id", "scanner_id"),
    ("species_id", "species_id"),
    ("species_name", "species_name"),
    ("species_genus", "species_genus"),
    ("species_species", "species_species"),
    ("uploaded_at", "uploaded_at"),
    ("wave_id", "wave_id"),
    ("wave_number", "wave_number"),
    ("wave_name", "wave_name"),
    ("accession_id", "accession_id"),
    ("genotype", None),  # derived (accessions.name)
    ("date_scanned", "date_scanned"),
    ("experiment_id", "experiment_id"),
    ("experiment_name", "experiment_name"),
    ("germ_day", "germ_day"),
    ("germ_day_color", "germ_day_color"),
    ("phenotyper_id", "phenotyper_id"),
    ("plant_age_days", "plant_age_days"),
    ("plant_id", "plant_id"),
]
CSV_COLUMNS: list[str] = [name for name, _ in _COLUMNS]


def safe_component(value: Any) -> str:
    """Turn a database value into a single safe path segment.

    Every part of a frame's destination comes from the database (`qr_code`, `date_scanned`,
    `frame_number`, ...). Stripping separators keeps an odd value from steering the write
    somewhere else on disk — note that joining an absolute path would otherwise discard the
    output directory entirely.
    """
    # Note: whitespace is deliberately preserved. `qr_code` is UNIQUE per wave, so "QR-1" and
    # "QR-1 " are two different plants, and trimming would merge their frames into one directory.
    cleaned = str(value).replace("\\", "_").replace("/", "_").replace("\0", "_")
    if cleaned in {"", ".", ".."} or set(cleaned) == {"."}:
        return "_"
    return cleaned


def scan_relative_dir(scan: dict[str, Any]) -> str:
    """Per-scan image dir, relative to the output dir (where scans.csv lives)."""
    wave = scan.get("wave_number") or 0
    day = safe_component(scan.get("plant_age_days"))
    date = safe_component(scan.get("date_scanned"))
    qr = safe_component(scan.get("qr_code"))
    return f"images/Wave{safe_component(wave)}/Day{day}_{date}/{qr}"


def build_scan_row(scan: dict[str, Any], genotype: str | None) -> dict[str, Any]:
    """Map a cyl_scans_extended row to the ordered scans.csv row."""
    row: dict[str, Any] = {}
    for name, key in _COLUMNS:
        if name == "scan_path":
            row[name] = scan_relative_dir(scan)
        elif name == "genotype":
            row[name] = genotype if genotype is not None else ""
        else:
            row[name] = scan.get(key, "")
    return row


def write_scans_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """Write rows to scans.csv with the fixed column order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


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


def download_selector(**options: Any) -> dict[str, Any]:
    """The options that decide which scans a run downloads.

    `experiment_id` is the resolved id, so selecting the same experiment by name on one run
    and by id on the next still counts as the same download.
    """
    return {
        key: options.get(key)
        for key in (
            "experiment_id",
            "scan_id",
            "plant_qr_code",
            "plant_age_min",
            "plant_age_max",
            "limit",
        )
    }


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


def image_dest(out_dir: Path, scan: dict[str, Any], image: dict[str, Any]) -> Path:
    """Destination for one frame, preserving its real extension.

    Raises ValueError if the path would land outside ``out_dir`` — a second check behind
    `safe_component`, so nothing can reach a write outside the output directory.

    The check is on the path as written, without consulting the filesystem. Resolving it would
    follow symlinks, which would reject the perfectly ordinary case of `images/` pointing at
    another disk.
    """
    ext = Path(image["object_path"]).suffix or ".png"
    ext = "." + safe_component(ext.lstrip(".") or "png")
    relative = f"{scan_relative_dir(scan)}/{safe_component(image['frame_number'])}{ext}"
    normalized = os.path.normpath(relative)
    if os.path.isabs(normalized) or normalized.split(os.sep)[0] == os.pardir:
        raise ValueError(f"refusing to write outside {out_dir}: {relative}")
    return Path(out_dir) / normalized


# --- supabase / storage I/O -------------------------------------------------


def fetch_scans(
    client: Any,
    experiment_id: int,
    *,
    plant_qr_code: str | None = None,
    plant_age_min: int = 0,
    plant_age_max: int = 1000,
    limit: int = 100000,
) -> list[dict[str, Any]]:
    """Query cyl_scans_extended for an experiment (legacy filter semantics)."""
    query = client.table("cyl_scans_extended").select("*").eq("experiment_id", experiment_id)
    if plant_qr_code:
        query = query.eq("qr_code", plant_qr_code)
    else:
        query = query.gte("plant_age_days", plant_age_min).lte("plant_age_days", plant_age_max)
    return query.limit(limit).execute().data or []


def search_experiments(client: Any, query: str, species: str | None = None) -> list[dict[str, Any]]:
    """Server-side experiment name search via the cyl_experiment_search RPC (trigram-indexed).

    The query (and optional species) are passed as bound RPC arguments — never concatenated into
    SQL — so no user text can alter the query. Returns matching live experiments (id, name,
    species_name, created_at), capped server-side.
    """
    params: dict[str, Any] = {"p_query": query}
    if species:
        params["p_species"] = species
    return client.rpc("cyl_experiment_search", params).execute().data or []


def fetch_scan(client: Any, scan_id: Any) -> dict[str, Any] | None:
    """Single cyl_scans_extended row for one scan_id, or None if not found."""
    rows = (
        client.table("cyl_scans_extended")
        .select("*")
        .eq("scan_id", scan_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def fetch_genotypes(client: Any, accession_ids: list[Any]) -> dict[Any, str]:
    """Map accession_id -> accessions.name for the given ids."""
    ids = sorted({a for a in accession_ids if a is not None})
    if not ids:
        return {}
    rows = client.table("accessions").select("id, name").in_("id", ids).execute().data or []
    return {row["id"]: row["name"] for row in rows}


def fetch_images(client: Any, scan_id: Any) -> list[dict[str, Any]]:
    """Frames for a scan, ordered by frame_number."""
    return (
        client.table("cyl_images")
        .select("*")
        .eq("scan_id", scan_id)
        .order("frame_number")
        .execute()
        .data
        or []
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

    @property
    def scan_level(self) -> bool:
        """True when this records a problem with a whole scan rather than one frame."""
        return self.unlisted or self.no_frames


@dataclass
class DownloadResult:
    """Aggregate outcome of a `download_images` run."""

    frames: list[FrameResult]

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
        """True if anything is known to be missing from the output directory."""
        return bool(self.failed or self.scans_unlisted or self.scans_without_frames)


def download_frame(
    client: Any,
    scan: dict[str, Any],
    image: dict[str, Any],
    out_dir: Path,
    *,
    overwrite: bool = False,
) -> FrameResult:
    """Download one frame to its destination, returning the outcome.

    Never raises: any failure (missing object, server error, write error) is recorded on
    the result instead, so one bad frame can't abort the run. Safe to call from a worker thread.

    A frame already on disk is reported as skipped without making a request, which is what
    makes an interrupted run cheap to pick back up.
    """
    object_path = image.get("object_path", "")
    result = FrameResult(scan.get("scan_id"), image.get("frame_number"), object_path, ok=False)
    try:
        dest = image_dest(out_dir, scan, image)
        if not overwrite and already_downloaded(dest):
            result.ok = True
            result.skipped = True
            return result
        atomic_write_bytes(dest, download_object(client, object_path))
        result.ok = True
    except (KeyError, TypeError) as exc:  # a bare key or pathlib error explains nothing
        result.error = f"malformed cyl_images row: {exc}"
    except Exception as exc:  # per-frame: record and continue
        result.error = str(exc)
    return result


def _run_bounded(
    work: list[Any], run_one: Callable[[Any], FrameResult], workers: int, *, window_factor: int = 4
) -> list[FrameResult]:
    """Run ``run_one`` over ``work`` across ``workers`` threads, a window at a time.

    Queueing every frame up front would cost hundreds of MB on a large experiment before the
    first byte arrives. Keeping a limited number in flight uses flat memory and still keeps
    every thread busy. Results are stored by position, so the order matches ``work``.
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
                results[pending.pop(future)] = future.result()
            for index, item in itertools.islice(remaining, len(done)):
                pending[pool.submit(run_one, item)] = index
    return results


def _list_scan_frames(
    client: Any, scan: dict[str, Any]
) -> tuple[list[dict[str, Any]], FrameResult | None]:
    """List a scan's frames; on a listing failure return a synthetic failed FrameResult.

    A failed query becomes one recorded failure for that scan rather than crashing the whole
    run, so the log and the non-zero exit still cover it. It carries no frame count, because
    there isn't one — `DownloadResult.scans_unlisted` keeps these apart from missing frames so
    a whole unlisted scan doesn't read as a single lost frame.
    """
    try:
        return fetch_images(client, scan.get("scan_id")), None
    except Exception as exc:
        return [], FrameResult(
            scan.get("scan_id"),
            None,
            "",
            ok=False,
            error=f"list images: {exc}",
            unlisted=True,
        )


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


def find_frame_collisions(
    out_dir: Path, work: list[tuple[dict[str, Any], dict[str, Any]]]
) -> list[str]:
    """Describe every pair of frames that would land on the same file.

    The destination is built from wave/age/date/qr plus the frame number, all of which can be
    empty in the database — and two rows with an empty value are not caught by a uniqueness
    constraint. Two rows can therefore share a filename, and without this check the second is
    quietly skipped as already-downloaded and its image never arrives.

    The comparison is on the real destination path, compared the way this filesystem compares
    names: on macOS `st0-001` and `ST0-001` are two database values but one file, so matching
    the raw strings would miss exactly the collisions that matter. On a case-sensitive
    filesystem they are genuinely different files and are left alone.
    """
    fold = filesystem_folds_case(Path(out_dir))
    seen: dict[str, tuple[Any, Any, Path]] = {}
    clashes: list[str] = []
    for scan, image in work:
        try:
            dest = image_dest(out_dir, scan, image)
        except (KeyError, TypeError, ValueError):
            continue  # malformed row; reported per-frame during the download
        key = _path_key(dest, fold)
        owner = (scan.get("scan_id"), image.get("frame_number"))
        if key in seen:
            first_scan, first_frame, first_dest = seen[key]
            clashes.append(
                f"scan {first_scan!r} frame {first_frame!r} ({first_dest}) and scan {owner[0]!r} "
                f"frame {owner[1]!r} ({dest}) are the same file"
            )
        else:
            seen[key] = (*owner, dest)
    return clashes


def download_images(
    client: Any,
    scans: list[dict[str, Any]],
    out_dir: Path,
    *,
    overwrite: bool = False,
    workers: int = DEFAULT_WORKERS,
) -> DownloadResult:
    """Download every frame for every scan from Storage bucket `images`.

    Frames are listed per scan, then fetched by up to ``workers`` threads at once, since the
    per-frame requests are what make a large experiment slow. ``workers <= 1`` runs one at a
    time. Results stay in scan and frame order, so the log reads the same way every run.

    A failing frame is recorded rather than raised, so one bad frame can't abort the run.

    Frames already written by an earlier run are skipped unless ``overwrite`` is set, which is
    what makes an interrupted download cheap to resume.
    """
    sweep_orphan_temps(Path(out_dir))

    # One entry per log line, in scan order: either a scan that couldn't be listed or a frame
    # to fetch. Building the list this way keeps unlisted scans next to the scans around them
    # rather than collected at the end of the log.
    slots: list[Any] = []
    for scan in scans:
        images, failure = _list_scan_frames(client, scan)
        if failure is not None:
            slots.append(failure)
        elif not images:
            # A scan with no rows in cyl_images is missing data, not a finished scan. Left
            # uncounted it would be invisible: no log line, and a clean exit.
            slots.append(
                FrameResult(
                    scan.get("scan_id"), None, "", ok=False, error="no frames", no_frames=True
                )
            )
        slots.extend((scan, image) for image in images)

    work = [slot for slot in slots if not isinstance(slot, FrameResult)]

    clashes = find_frame_collisions(Path(out_dir), work)
    if clashes:
        raise CollidingFrames("; ".join(clashes))

    def _one(pair: tuple[dict[str, Any], dict[str, Any]]) -> FrameResult:
        return download_frame(client, pair[0], pair[1], out_dir, overwrite=overwrite)

    # Never start more threads than there are frames, than were asked for, or than the limit.
    n = min(workers, MAX_WORKERS, len(work)) if work else 0
    fetched = [_one(pair) for pair in work] if n <= 1 else _run_bounded(work, _one, n)

    outcomes = iter(fetched)
    frames = [slot if isinstance(slot, FrameResult) else next(outcomes) for slot in slots]
    return DownloadResult(frames)


def _one_line(text: Any) -> str:
    """Collapse whitespace so one record can never span more than one line.

    `object_path` and the error text both come from the server, and a multi-line error (an
    httpx message, say) would otherwise emit continuation lines that read like frame records.
    """
    return " ".join(str(text).split())


def write_download_log(result: DownloadResult, path: Path) -> None:
    """Write a per-frame download log (one line per frame) with a summary footer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for f in result.frames:
        if f.unlisted:
            lines.append(
                f"UNLISTED scan={f.scan_id} (frame count unknown)  error={_one_line(f.error)}"
            )
            continue
        if f.no_frames:
            lines.append(f"NOFRAMES scan={f.scan_id} (no images recorded for this scan)")
            continue
        status = "SKIP" if f.skipped else "OK  " if f.ok else "FAIL"
        line = f"{status} scan={f.scan_id} frame={f.frame_number} {_one_line(f.object_path)}"
        if not f.ok:
            line += f"  error={_one_line(f.error)}"
        lines.append(line)
    summary = (
        f"\nSummary: {result.ok}/{result.total} frames present "
        f"({result.downloaded} downloaded this run, {result.skipped} already on disk), "
        f"{result.failed} failed"
    )
    if result.scans_unlisted:
        summary += f", {result.scans_unlisted} scan(s) could not be listed (frames unknown)"
    if result.scans_without_frames:
        summary += f", {result.scans_without_frames} scan(s) have no images"
    lines.append(summary)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- command ----------------------------------------------------------------


@click.command(name="download")
@click.argument("out_dir", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--experiment-id",
    "--experiment_id",
    "experiment_id",
    type=int,
    default=None,
    help="Download a whole experiment by ID (mutually exclusive with --scan-id).",
)
@click.option(
    "--scan-id",
    "--scan_id",
    "scan_id",
    type=int,
    default=None,
    help="Download a single scan by ID (mutually exclusive with --experiment-id).",
)
@click.option(
    "--experiment-name",
    "--experiment_name",
    "experiment_name",
    default=None,
    help="Resolve the experiment to download by name (case-insensitive substring); an ambiguous "
    "name lists candidates "
    "and exits without downloading. Mutually exclusive with --experiment-id / --scan-id.",
)
@click.option(
    "--species",
    default=None,
    help="Narrow --experiment-name to one species (common name).",
)
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
@click.option(
    "--meta-only",
    "--meta_only",
    "meta_only",
    is_flag=True,
    help="Write scans.csv only; skip image download.",
)
@click.option(
    "--plant-qr-code",
    "--plant_qr_code",
    "plant_qr_code",
    default=None,
    help="Restrict to a single plant QR code.",
)
@click.option(
    "--plant-age-min",
    "--plant_age_min",
    "plant_age_min",
    type=int,
    default=0,
    show_default=True,
    help="Minimum plant age in days.",
)
@click.option(
    "--plant-age-max",
    "--plant_age_max",
    "plant_age_max",
    type=int,
    default=1000,
    show_default=True,
    help="Maximum plant age in days.",
)
@click.option(
    "--limit",
    type=int,
    default=100000,
    show_default=True,
    help="Maximum number of scans to fetch.",
)
@click.option(
    "--overwrite",
    is_flag=True,
    help="Re-download frames that are already on disk (default: keep them and resume).",
)
@click.option(
    "-n",
    "--workers",
    type=click.IntRange(min=1, max=MAX_WORKERS),
    default=DEFAULT_WORKERS,
    show_default=True,
    help=f"Concurrent image downloads (I/O-bound, 1-{MAX_WORKERS}). 1 = sequential.",
)
def download(
    out_dir: Path,
    experiment_id: int | None,
    scan_id: int | None,
    experiment_name: str | None,
    species: str | None,
    profile: str,
    meta_only: bool,
    plant_qr_code: str | None,
    plant_age_min: int,
    plant_age_max: int,
    limit: int,
    overwrite: bool,
    workers: int,
) -> None:
    """Download a cylinder experiment (--experiment-id / --experiment-name) or a single scan
    (--scan-id): metadata (scans.csv) and per-frame images."""
    from .. import auth
    from ..credentials import load_credentials
    from ._resolve import Ambiguous, NoMatch, Resolved, classify

    # Exactly one primary selector.
    if [experiment_id is not None, scan_id is not None, experiment_name is not None].count(
        True
    ) != 1:
        raise click.UsageError(
            "Pass exactly one of --experiment-id, --scan-id, or --experiment-name."
        )
    if species and experiment_name is None:
        raise click.UsageError("--species only applies with --experiment-name.")

    try:
        creds = load_credentials(profile)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(f"{exc} — run `bloomctl login`.") from exc
    try:
        client = auth.make_authed_client(creds)
    except auth.AuthError as exc:
        raise click.ClickException(str(exc)) from exc

    if experiment_name is not None:  # resolve the name to a concrete id (server-side search)
        from postgrest import APIError

        try:
            found = search_experiments(client, experiment_name, species=species)
        except APIError as exc:  # e.g. the RPC's >200-char guard, or a permission error
            raise click.ClickException(getattr(exc, "message", None) or str(exc)) from exc
        outcome = classify(found)
        if isinstance(outcome, NoMatch):
            scope = f" for species {species!r}" if species else ""
            raise click.ClickException(f"No experiment matches {experiment_name!r}{scope}.")
        if isinstance(outcome, Ambiguous):
            listing = "\n".join(
                f"  {m.id}  {m.label}  {m.created or ''}" for m in outcome.candidates
            )
            raise click.ClickException(
                f"{len(outcome.candidates)} experiments match {experiment_name!r} — "
                f"narrow it (--species) or pass --experiment-id:\n{listing}"
            )
        assert isinstance(outcome, Resolved)
        experiment_id = outcome.match.id
        click.echo(f"Matched: {outcome.match.label} (id {experiment_id})", err=True)

    if scan_id is not None:
        scan = fetch_scan(client, scan_id)
        if scan is None:
            raise click.ClickException(f"Scan {scan_id} not found.")
        scans = [scan]
    else:
        scans = fetch_scans(
            client,
            experiment_id,
            plant_qr_code=plant_qr_code,
            plant_age_min=plant_age_min,
            plant_age_max=plant_age_max,
            limit=limit,
        )
    genotypes = fetch_genotypes(client, [s.get("accession_id") for s in scans])
    rows = [build_scan_row(s, genotypes.get(s.get("accession_id"))) for s in scans]

    if not scans:
        raise click.ClickException(
            "No scans matched, so there is nothing to download. Check the experiment and any "
            "--plant-qr-code / --plant-age-min / --plant-age-max filters."
        )

    out = Path(out_dir)
    selector = download_selector(
        experiment_id=experiment_id,
        scan_id=scan_id,
        plant_qr_code=plant_qr_code,
        plant_age_min=plant_age_min,
        plant_age_max=plant_age_max,
        limit=limit,
    )
    mismatch = describe_manifest_mismatch(read_manifest(out), selector)
    if mismatch:
        # Not escapable with --overwrite: that re-fetches this run's frames but leaves the
        # earlier selection's in place, which is the mix-up being prevented.
        raise click.ClickException(
            f"{out} already holds a different download ({mismatch}). Give each selection its "
            f"own directory. Re-running the same command here resumes where it left off."
        )

    csv_path = out / "scans.csv"
    write_scans_csv(rows, csv_path)
    write_manifest(out, selector)
    click.echo(f"Wrote {len(rows)} scans -> {csv_path}")

    if meta_only:
        return

    try:
        result = download_images(client, scans, out, overwrite=overwrite, workers=workers)
    except CollidingFrames as exc:
        raise click.ClickException(
            f"{exc}. Refusing to download, because one frame's image would overwrite or mask "
            f"another's. Narrow the download (--scan-id / --plant-qr-code) or fix the rows."
        ) from exc

    log_path = out / "download_log.txt"
    write_download_log(result, log_path)
    click.echo(
        f"{result.ok}/{result.total} frames present in {out / 'images'} "
        f"({result.downloaded} downloaded this run, {result.skipped} already on disk)  "
        f"(log: {log_path})"
    )
    if result.incomplete:
        # Partial download: surface it and exit non-zero so a pipeline knows the
        # output is incomplete (the log lists every failed frame).
        problems = []
        if result.failed:
            problems.append(f"{result.failed} of {result.total} frames failed to download")
        if result.scans_unlisted:
            problems.append(
                f"{result.scans_unlisted} scan(s) could not be listed at all "
                f"(an unknown number of further frames is missing)"
            )
        if result.scans_without_frames:
            problems.append(f"{result.scans_without_frames} scan(s) have no images recorded")
        raise click.ClickException(
            f"{'; '.join(problems)} — see {log_path}. "
            "Re-running the same command retries only the frames still missing."
        )
