"""`bloomctl cyl download`: metadata (scans.csv) + per-frame images.

Pure helpers (column mapping, paths) are separated from the supabase/storage I/O
so the contract is unit-testable without a live server.
"""

from __future__ import annotations

import csv
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from ..credentials import DEFAULT_PROFILE

# Default concurrent image downloads. Image fetches are I/O-bound (one HTTP round-trip
# to Storage each), so a thread pool overlaps request latency; 8 is a modest default
# that cuts large-experiment download time without hammering the server (see #534).
DEFAULT_WORKERS = 8

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


def scan_relative_dir(scan: dict[str, Any]) -> str:
    """Per-scan image dir, relative to the output dir (where scans.csv lives)."""
    wave = scan.get("wave_number") or 0
    return f"images/Wave{wave}/Day{scan.get('plant_age_days')}_{scan.get('date_scanned')}/{scan.get('qr_code')}"


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


def image_dest(out_dir: Path, scan: dict[str, Any], image: dict[str, Any]) -> Path:
    """Absolute destination for one frame, preserving its real extension."""
    ext = Path(image["object_path"]).suffix or ".png"
    return Path(out_dir) / scan_relative_dir(scan) / f"{image['frame_number']}{ext}"


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
    query = (
        client.table("cyl_scans_extended")
        .select("*")
        .eq("experiment_id", experiment_id)
    )
    if plant_qr_code:
        query = query.eq("qr_code", plant_qr_code)
    else:
        query = query.gte("plant_age_days", plant_age_min).lte(
            "plant_age_days", plant_age_max
        )
    return query.limit(limit).execute().data or []


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
    rows = (
        client.table("accessions").select("id, name").in_("id", ids).execute().data
        or []
    )
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


@dataclass
class DownloadResult:
    """Aggregate outcome of a `download_images` run."""

    frames: list[FrameResult]

    @property
    def ok(self) -> int:
        return sum(1 for f in self.frames if f.ok)

    @property
    def failed(self) -> int:
        return sum(1 for f in self.frames if not f.ok)

    @property
    def total(self) -> int:
        return len(self.frames)


def download_frame(
    bucket: Any, scan: dict[str, Any], image: dict[str, Any], out_dir: Path
) -> FrameResult:
    """Download one frame to its destination, returning a FrameResult.

    Self-contained and exception-safe: any failure (missing object, transient 5xx,
    write error) is captured on the result, never raised — so one bad frame can't
    abort a concurrent run. Safe to call from a worker thread.

    The bytes are written to a temp file in the destination directory and then atomically
    ``os.replace``-d into place. Two frames can map to the same destination path when the
    path scheme (wave/day/qr + frame_number) doesn't distinguish two scans; the atomic
    rename means concurrent writers can never produce a torn file — the result is one
    complete file (last writer wins), matching the sequential behaviour.
    """
    object_path = image.get("object_path", "")
    result = FrameResult(scan.get("scan_id"), image.get("frame_number"), object_path, ok=False)
    try:
        data = bucket.download(object_path)
        if data is None:
            raise ValueError("empty response from storage")
        dest = image_dest(out_dir, scan, image)
        dest.parent.mkdir(parents=True, exist_ok=True)  # mkdir(exist_ok) is race-safe
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".dl-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, dest)  # atomic: no torn file even if two workers collide
        except BaseException:
            _unlink_quietly(tmp)  # never leave a temp behind
            raise
        result.ok = True
    except Exception as exc:  # per-frame: record and continue
        result.error = str(exc)
    return result


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _list_scan_frames(
    client: Any, scan: dict[str, Any]
) -> tuple[list[dict[str, Any]], FrameResult | None]:
    """List a scan's frames; on a listing failure return a synthetic failed FrameResult.

    A failed metadata query (transient 5xx, auth expiry) becomes one recorded failure for
    that scan instead of crashing the whole run with a traceback — so the partial-download
    exit + download_log still apply uniformly.
    """
    try:
        return fetch_images(client, scan.get("scan_id")), None
    except Exception as exc:
        return [], FrameResult(scan.get("scan_id"), None, "", ok=False, error=f"list images: {exc}")


def download_images(
    client: Any,
    scans: list[dict[str, Any]],
    out_dir: Path,
    *,
    workers: int = DEFAULT_WORKERS,
) -> DownloadResult:
    """Download every frame for every scan from Storage bucket `images`.

    Frames are listed per scan (ordered), then downloaded concurrently across a thread
    pool of up to ``workers`` — the per-frame HTTP round-trips are the bottleneck on large
    experiments, and being I/O-bound they overlap well (see #534). ``workers <= 1`` runs
    sequentially. Results preserve scan/frame order, so the download log is deterministic.
    The Supabase client's underlying httpx client is thread-safe, so the one bucket handle
    is shared across workers. A scan whose frame list can't be fetched is recorded as a
    failure (not a crash); atomic per-frame writes tolerate colliding destination paths.
    """
    bucket = client.storage.from_("images")
    work: list[tuple[dict[str, Any], dict[str, Any]]] = []
    listing_failures: list[FrameResult] = []
    for scan in scans:
        images, failure = _list_scan_frames(client, scan)
        if failure is not None:
            listing_failures.append(failure)
        for image in images:
            work.append((scan, image))

    # Never spawn more threads than there's work for (or than requested).
    n = min(workers, len(work)) if work else 0
    if n <= 1:
        frames = [download_frame(bucket, scan, image, out_dir) for scan, image in work]
    else:
        with ThreadPoolExecutor(max_workers=n) as pool:
            # map preserves input order → log stays in scan/frame order.
            frames = list(pool.map(lambda p: download_frame(bucket, p[0], p[1], out_dir), work))
    return DownloadResult(frames + listing_failures)


def write_download_log(result: DownloadResult, path: Path) -> None:
    """Write a per-frame download log (one line per frame) with a summary footer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for f in result.frames:
        line = (
            f"{'OK  ' if f.ok else 'FAIL'} scan={f.scan_id} frame={f.frame_number} {f.object_path}"
        )
        if not f.ok:
            line += f"  error={f.error}"
        lines.append(line)
    lines.append(f"\nSummary: {result.ok} downloaded, {result.failed} failed, {result.total} total")
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
    "-n",
    "--workers",
    type=click.IntRange(min=1, max=64),
    default=DEFAULT_WORKERS,
    show_default=True,
    help="Concurrent image downloads (I/O-bound, 1-64). 1 = sequential.",
)
def download(
    out_dir: Path,
    experiment_id: int | None,
    scan_id: int | None,
    profile: str,
    meta_only: bool,
    plant_qr_code: str | None,
    plant_age_min: int,
    plant_age_max: int,
    limit: int,
    workers: int,
) -> None:
    """Download a cylinder experiment (--experiment-id) or a single scan (--scan-id):
    metadata (scans.csv) and per-frame images."""
    from .. import auth
    from ..credentials import load_credentials

    # Exactly one of --experiment-id / --scan-id.
    if (experiment_id is None) == (scan_id is None):
        raise click.UsageError("Pass exactly one of --experiment-id or --scan-id.")

    try:
        creds = load_credentials(profile)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(f"{exc} — run `bloomctl login`.") from exc
    try:
        client = auth.make_authed_client(creds)
    except auth.AuthError as exc:
        raise click.ClickException(str(exc)) from exc

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

    out = Path(out_dir)
    csv_path = out / "scans.csv"
    write_scans_csv(rows, csv_path)
    click.echo(f"Wrote {len(rows)} scans -> {csv_path}")

    if meta_only:
        return

    result = download_images(client, scans, out, workers=workers)
    log_path = out / "download_log.txt"
    write_download_log(result, log_path)
    click.echo(
        f"Downloaded {result.ok}/{result.total} image frames -> {out / 'images'}  (log: {log_path})"
    )
    if result.failed:
        # Partial download: surface it and exit non-zero so a pipeline knows the
        # output is incomplete (the log lists every failed frame).
        raise click.ClickException(
            f"{result.failed} of {result.total} frames failed to download — see {log_path}"
        )
