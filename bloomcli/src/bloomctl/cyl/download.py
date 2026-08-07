"""`bloomctl cyl download`: metadata (scans.csv) + per-frame images.

Pure helpers (column mapping, paths) are separated from the supabase/storage I/O
so the contract is unit-testable without a live server.
"""

from __future__ import annotations

import csv
import itertools
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
    text = str(value)
    cleaned = text.replace("\\", "/").replace("/", "_").strip()
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


def image_dest(out_dir: Path, scan: dict[str, Any], image: dict[str, Any]) -> Path:
    """Absolute destination for one frame, preserving its real extension.

    Raises ValueError if the path would land outside ``out_dir`` — a second check behind
    `safe_component`, so nothing can reach a write outside the output directory.
    """
    ext = safe_component(Path(image["object_path"]).suffix or ".png").lstrip("_")
    ext = ext if ext.startswith(".") else f".{ext}"
    root = Path(out_dir).resolve()
    dest = (root / scan_relative_dir(scan) / f"{safe_component(image['frame_number'])}{ext}").resolve()
    if not dest.is_relative_to(root):
        raise ValueError(f"refusing to write outside {out_dir}: {dest}")
    return dest


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
        """Frames known to be missing. Scans that couldn't be listed are counted separately,
        since how many frames they hold is unknown."""
        return sum(1 for f in self.frames if not f.ok and not f.unlisted)

    @property
    def scans_unlisted(self) -> int:
        """Scans whose frame list couldn't be fetched, so an unknown number of frames is missing."""
        return sum(1 for f in self.frames if f.unlisted)

    @property
    def total(self) -> int:
        """Frames that were actually listed."""
        return sum(1 for f in self.frames if not f.unlisted)

    @property
    def incomplete(self) -> bool:
        """True if anything is known to be missing from the output directory."""
        return bool(self.failed or self.scans_unlisted)


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
    except KeyError as exc:  # a bare KeyError in the log wouldn't say what was wrong
        result.error = f"malformed cyl_images row: missing key {exc}"
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


def check_frame_collisions(scans: list[dict[str, Any]], images_by_scan: dict[Any, list]) -> None:
    """Raise if two frames would be written to the same file.

    The destination is built from wave/age/date/qr plus the frame number, all of which can be
    empty in the database — and two rows with an empty value are not caught by a uniqueness
    constraint. Two rows can therefore land on one filename, and without this check the second
    is quietly skipped as already-downloaded and its image never arrives.
    `download-for-predict` makes the same check on the same data.
    """
    seen: dict[str, Any] = {}
    for scan in scans:
        for image in images_by_scan.get(scan.get("scan_id"), []):
            key = f"{scan_relative_dir(scan)}/{safe_component(image.get('frame_number'))}"
            if key in seen:
                raise ValueError(
                    f"scans {seen[key]!r} and {scan.get('scan_id')!r} both map frame "
                    f"{image.get('frame_number')!r} to {key} — refusing to download, as one "
                    f"frame's images would silently overwrite or mask the other's"
                )
            seen[key] = scan.get("scan_id")


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
    images_by_scan: dict[Any, list[dict[str, Any]]] = {}
    for scan in scans:
        images, failure = _list_scan_frames(client, scan)
        images_by_scan[scan.get("scan_id")] = images
        if failure is not None:
            slots.append(failure)
        slots.extend((scan, image) for image in images)

    check_frame_collisions(scans, images_by_scan)

    work = [slot for slot in slots if not isinstance(slot, FrameResult)]

    def _one(pair: tuple[dict[str, Any], dict[str, Any]]) -> FrameResult:
        return download_frame(client, pair[0], pair[1], out_dir, overwrite=overwrite)

    # Never start more threads than there are frames, than were asked for, or than the limit.
    n = min(workers, MAX_WORKERS, len(work)) if work else 0
    fetched = [_one(pair) for pair in work] if n <= 1 else _run_bounded(work, _one, n)

    outcomes = iter(fetched)
    frames = [slot if isinstance(slot, FrameResult) else next(outcomes) for slot in slots]
    return DownloadResult(frames)


def write_download_log(result: DownloadResult, path: Path) -> None:
    """Write a per-frame download log (one line per frame) with a summary footer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for f in result.frames:
        if f.unlisted:
            lines.append(f"UNLISTED scan={f.scan_id} (frame count unknown)  error={f.error}")
            continue
        status = "SKIP" if f.skipped else "OK  " if f.ok else "FAIL"
        line = f"{status} scan={f.scan_id} frame={f.frame_number} {f.object_path}"
        if not f.ok:
            line += f"  error={f.error}"
        lines.append(line)
    summary = (
        f"\nSummary: {result.ok}/{result.total} frames present "
        f"({result.downloaded} downloaded this run, {result.skipped} already on disk), "
        f"{result.failed} failed"
    )
    if result.scans_unlisted:
        summary += f", {result.scans_unlisted} scan(s) could not be listed (frames unknown)"
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

    out = Path(out_dir)
    csv_path = out / "scans.csv"
    write_scans_csv(rows, csv_path)
    click.echo(f"Wrote {len(rows)} scans -> {csv_path}")

    if meta_only:
        return

    try:
        result = download_images(client, scans, out, overwrite=overwrite, workers=workers)
    except ValueError as exc:  # two frames want the same file; stop rather than lose one
        raise click.ClickException(str(exc)) from exc

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
        missing = f"{result.failed} of {result.total} frames failed to download"
        if result.scans_unlisted:
            missing += (
                f", and {result.scans_unlisted} scan(s) could not be listed at all "
                f"(an unknown number of further frames is missing)"
            )
        raise click.ClickException(
            f"{missing} — see {log_path}. "
            "Re-running the same command retries only the frames still missing."
        )
