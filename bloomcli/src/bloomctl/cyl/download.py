"""`bloomctl cyl download`: metadata (scans.csv) + per-frame images.

Cylinder-specific only: the scans.csv columns, the queries, the on-disk path layout, and the
loop that walks a scan's frames. Everything about performing the download safely — atomic
writes, resume, bounded concurrency, collision detection, progress and logging — is shared
with the other scan methods in `bloomctl/_download.py`.

Pure helpers (column mapping, paths) are separated from the supabase/storage I/O
so the contract is unit-testable without a live server.
"""

from __future__ import annotations

import csv
import io
import threading
from pathlib import Path
from typing import Any, Callable

import click

from .._download import (
    BURST_DROP_FACTOR,
    DEFAULT_WORKERS,
    MANIFEST_NAME,
    MAX_WORKERS,
    OUT_OF_SPACE,
    PROGRESS_INTERVAL_SECONDS,
    RATE_WINDOW_SAMPLES,
    RETRY_HINT,
    CollidingFrames,
    DownloadResult,
    FrameResult,
    ProgressReporter,
    contained_dest,
    describe_manifest_mismatch,
    download_to,
    ensure_writable,
    fetch_all,
    find_collisions,
    format_duration,
    format_progress,
    format_rate,
    holds_an_unidentified_download,
    read_manifest,
    safe_component,
    selector_of,
    write_download_log,
    write_failed,
    write_manifest,
)
from .._postgrest import fetch_in_batches, queried
from .._storage import (
    atomic_write_bytes,
    sweep_orphan_temps,
)
from ..credentials import DEFAULT_PROFILE

# Cylinder frames live in the `images` bucket. Passed explicitly on every fetch — the shared
# storage helper has no default, so no command can read another method's bucket by omission.
IMAGES_BUCKET = "images"

# Stamped into the manifest so a plate download cannot resume into a cylinder directory.
METHOD = "cyl"

__all__ = [  # re-exported so callers and tests reach the mechanism through this module
    "BURST_DROP_FACTOR",
    "CollidingFrames",
    "DEFAULT_WORKERS",
    "DownloadResult",
    "FrameResult",
    "MANIFEST_NAME",
    "MAX_WORKERS",
    "OUT_OF_SPACE",
    "PROGRESS_INTERVAL_SECONDS",
    "ProgressReporter",
    "RATE_WINDOW_SAMPLES",
    "RETRY_HINT",
    "atomic_write_bytes",
    "contained_dest",
    "describe_manifest_mismatch",
    "download",
    "download_to",
    "ensure_writable",
    "fetch_all",
    "find_collisions",
    "format_duration",
    "format_progress",
    "format_rate",
    "holds_an_unidentified_download",
    "read_manifest",
    "safe_component",
    "selector_of",
    "write_download_log",
    "write_failed",
    "write_manifest",
]


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
    """Write rows to scans.csv with the fixed column order.

    Rendered in full before anything touches the file: this runs on every invocation,
    including a re-run that resumes, so opening the existing scans.csv for writing would
    empty it before the first row was written and a full disk would leave it that way.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_bytes(path, buffer.getvalue().encode("utf-8"))

# Which scans a run downloads. `experiment_id` is resolved, so name and id are one download.
SELECTOR_KEYS = (
    "experiment_id",
    "scan_id",
    "plant_qr_code",
    "plant_age_min",
    "plant_age_max",
    "limit",
)


def download_selector(**options: Any) -> dict[str, Any]:
    """The options that decide which scans a run downloads."""
    return selector_of(SELECTOR_KEYS, options)

def image_dest(out_dir: Path, scan: dict[str, Any], image: dict[str, Any]) -> Path:
    """Destination for one frame, preserving its real extension.

    Raises ValueError if the path would land outside ``out_dir``.
    """
    ext = Path(image["object_path"]).suffix or ".png"
    ext = "." + safe_component(ext.lstrip(".") or "png")
    relative = f"{scan_relative_dir(scan)}/{safe_component(image['frame_number'])}{ext}"
    return contained_dest(out_dir, relative)

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
    rows = fetch_in_batches(
        lambda batch: client.table("accessions").select("id, name").in_("id", batch), ids
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

def download_frame(
    client: Any,
    scan: dict[str, Any],
    image: dict[str, Any],
    out_dir: Path,
    *,
    stop: threading.Event | None = None,
) -> FrameResult:
    """Download one frame to its destination, returning the outcome.

    Never raises: any failure (missing object, server error, write error) is recorded on
    the result instead, so one bad frame can't abort the run. Safe to call from a worker thread.

    A frame already on disk is reported as skipped without making a request, which is what
    makes an interrupted run cheap to pick back up.

    ``stop`` is set when the disk fills. Frames that have not started yet are recorded as
    failed without being fetched — there is nowhere to put them, and a large experiment would
    otherwise pull hundreds of gigabytes only to throw them away. The few already in flight
    run to completion, so a little work carries on past the point the disk fills, bounded by
    the number of workers.

    A frame already on disk is still reported as present once the disk has filled. Nothing
    needs writing for it, and a resumed run that runs out of space part way through would
    otherwise report every frame it had already fetched as missing.
    """
    object_path = image.get("object_path", "")
    result = FrameResult(scan.get("scan_id"), image.get("frame_number"), object_path, ok=False)

    try:
        dest = image_dest(out_dir, scan, image)
    except (KeyError, TypeError) as exc:  # a bare key or pathlib error explains nothing
        result.error = f"malformed cyl_images row: {exc}"
        return result
    except Exception as exc:  # containment refusal, and anything else per-frame
        result.error = str(exc)
        return result

    # cyl_images records no size, so the resume check can only say the file isn't empty.
    fetched = download_to(
        client, object_path, dest, bucket=IMAGES_BUCKET, expected_size=None, stop=stop
    )
    result.ok, result.skipped, result.error, result.note = fetched
    return result

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

def find_frame_collisions(
    out_dir: Path, work: list[tuple[dict[str, Any], dict[str, Any]]]
) -> list[str]:
    """Describe every pair of frames that would land on the same file.

    The destination is built from wave/age/date/qr plus the frame number, all of which can be
    empty in the database — and two rows with an empty value are not caught by a uniqueness
    constraint. Two rows can therefore share a filename, and without this check the second is
    quietly skipped as already-downloaded and its image never arrives.
    """
    return find_collisions(
        out_dir,
        work,
        dest_of=lambda pair: image_dest(out_dir, pair[0], pair[1]),
        describe=lambda pair: (
            f"scan {pair[0].get('scan_id')!r} frame {pair[1].get('frame_number')!r}"
        ),
    )

def download_images(
    client: Any,
    scans: list[dict[str, Any]],
    out_dir: Path,
    *,
    workers: int = DEFAULT_WORKERS,
    on_progress: Callable[[str, int, int, int], None] | None = None,
) -> DownloadResult:
    """Download every frame for every scan from Storage bucket `images`.

    Frames are listed per scan, then fetched by up to ``workers`` threads at once, since the
    per-frame requests are what make a large experiment slow. ``workers <= 1`` runs one at a
    time. Results stay in scan and frame order, so the log reads the same way every run.

    A failing frame is recorded rather than raised, so one bad frame can't abort the run.

    Frames already written by an earlier run are skipped, which is what makes an interrupted
    download cheap to resume. To fetch an experiment afresh, download into a new directory.

    ``on_progress(phase, done, total, failed)`` is called as work completes, with ``phase``
    of ``"listing"`` or ``"downloading"``. It is only ever called from this thread.
    """
    sweep_orphan_temps(Path(out_dir))

    # One entry per log line, in scan order: either a scan that couldn't be listed or a frame
    # to fetch. Building the list this way keeps unlisted scans next to the scans around them
    # rather than collected at the end of the log.
    slots: list[Any] = []
    for listed, scan in enumerate(scans, start=1):
        if on_progress is not None:
            on_progress("listing", listed, len(scans), 0)
        images, failure = _list_scan_frames(client, scan)
        if failure is not None:
            slots.append(failure)
        elif not images:
            # A scan can have no rows in cyl_images if its upload was interrupted. Note it in
            # the log so it is visible, but don't fail the run: there is nothing to fetch, so
            # failing would make every future run of this experiment fail too.
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

    stop = threading.Event()

    def _one(pair: tuple[dict[str, Any], dict[str, Any]]) -> FrameResult:
        return download_frame(client, pair[0], pair[1], out_dir, stop=stop)

    done = failed = 0

    def _tick(result: FrameResult) -> None:
        # Counting completions alone would show 100% on a run where every frame failed.
        nonlocal done, failed
        done += 1
        if not result.ok:
            failed += 1
        if on_progress is not None:
            on_progress("downloading", done, len(work), failed)

    # The bounded runner is shared: how many threads to start, and whether to bother with a
    # pool at all, is the same question for every method.
    fetched = fetch_all(work, _one, workers=workers, on_done=_tick)

    outcomes = iter(fetched)
    frames = [slot if isinstance(slot, FrameResult) else next(outcomes) for slot in slots]
    return DownloadResult(frames, disk_full=stop.is_set())

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
    help=(
        "Fetch at most this many scans — for looking at a sample of an experiment. "
        "Not a way to export one in parts: each limit is its own selection, so a "
        "sample and a full download need separate directories."
    ),
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

    # Before signing in, so a bad path costs a second rather than the whole metadata phase.
    ensure_writable(Path(out_dir))

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
        scan = queried("this scan", lambda: fetch_scan(client, scan_id))
        if scan is None:
            raise click.ClickException(f"Scan {scan_id} not found.")
        scans = [scan]
    else:
        scans = queried(
            "this experiment's scans",
            lambda: fetch_scans(
                client,
                experiment_id,
                plant_qr_code=plant_qr_code,
                plant_age_min=plant_age_min,
                plant_age_max=plant_age_max,
                limit=limit,
            ),
        )
    genotypes = queried(
        "the accession names",
        lambda: fetch_genotypes(client, [s.get("accession_id") for s in scans]),
    )
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
    if holds_an_unidentified_download(out):
        raise click.ClickException(
            f"{out} already holds images but no {MANIFEST_NAME}, so there is no way to tell "
            f"which download they belong to. Downloading here risks mixing two experiments "
            f"in one directory. Download into a new directory instead."
        )

    mismatch = describe_manifest_mismatch(read_manifest(out), selector, method=METHOD)
    if mismatch:
        raise click.ClickException(
            f"{out} already holds a different download ({mismatch}). Give each selection its "
            f"own directory. Re-running the same command here resumes where it left off."
        )

    csv_path = out / "scans.csv"
    # The writability probe ran before the metadata queries; the disk can fill in between.
    try:
        # Manifest first: it is small and written atomically, so it claims the directory before
        # any other file exists. A run killed after the CSV but before the stamp would otherwise
        # leave a directory the other method can still claim.
        write_manifest(out, selector, method=METHOD)
        write_scans_csv(rows, csv_path)
    except OSError as exc:
        raise write_failed(Path(exc.filename or csv_path), exc) from exc
    click.echo(f"Wrote {len(rows)} scans -> {csv_path}")

    if meta_only:
        return

    try:
        result = download_images(
            client, scans, out, workers=workers, on_progress=ProgressReporter()
        )
    except CollidingFrames as exc:
        raise click.ClickException(
            f"{exc}. Refusing to download, because one frame's image would overwrite or mask "
            f"another's. Narrow the download (--scan-id / --plant-qr-code) or fix the rows."
        ) from exc

    log_path = out / "download_log.txt"
    # Counts first: on a full disk the log write is what fails, and the numbers matter more.
    click.echo(
        f"{result.ok:,}/{result.total:,} frames present in {out / 'images'} "
        f"({result.downloaded:,} downloaded this run, {result.skipped:,} already on disk)"
    )
    logged = True
    try:
        write_download_log(result, log_path)
    except OSError as exc:
        logged = False
        click.echo(f"Could not write {log_path.name}: {exc.strerror or exc}", err=True)
    else:
        click.echo(f"Log: {log_path}")
    if result.scans_without_frames:
        click.echo(
            f"Note: {result.scans_without_frames} scan(s) have no images recorded in Bloom, "
            f"so there was nothing to download for them (listed in the log).",
            err=True,
        )
    if result.incomplete:
        # Partial download: surface it and exit non-zero so a pipeline knows the
        # output is incomplete (the log lists every failed frame).
        problems = []
        if result.failed:
            problems.append(f"{result.failed:,} of {result.total:,} frames failed to download")
        if result.scans_unlisted:
            problems.append(
                f"{result.scans_unlisted} scan(s) could not be listed at all "
                f"(an unknown number of further frames is missing)"
            )
        # Not "the disk filled up": shared lab storage is quota-limited, where the disk has
        # plenty of space and `df` sends the reader off after the wrong thing entirely.
        cause = "the disk filled up or the storage quota was spent; " if result.disk_full else ""
        where = f" — see {log_path}" if logged else ""
        raise click.ClickException(
            f"{cause}{'; '.join(problems)}{where}. "
            "Re-running the same command retries only the frames still missing."
        )
