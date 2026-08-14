"""`bloomctl plate download`: metadata (plates.csv) + per-plate images.

Plate-specific only: the plates.csv columns, the gravi queries, the on-disk path layout, and
the loop that walks the selected scans. Everything about performing the download safely lives
in `bloomctl/_download.py`, shared with the cylinder command.

The shape differs from cylinders in one way that drives the whole file: `gravi_images` is
UNIQUE(scan_id), so a scan holds exactly one image. Repetition comes from time instead — a
continuous session captures the same plate once per cycle — so the layout groups by plate and
names each file by its capture.

Pure helpers (column mapping, paths) are separated from the supabase/storage I/O so the
contract is unit-testable without a live server.
"""

from __future__ import annotations

import csv
import io
import threading
from pathlib import Path
from typing import Any, Callable

import click

from .._download import (
    DEFAULT_WORKERS,
    MANIFEST_NAME,
    MAX_WORKERS,
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
    holds_an_unidentified_download,
    read_manifest,
    safe_component,
    selector_of,
    write_download_log,
    write_manifest,
)
from .._postgrest import fetch_in_batches
from .._storage import atomic_write_bytes, sweep_orphan_temps
from ..credentials import DEFAULT_PROFILE

# Plate images live in their own bucket. Passed explicitly on every fetch — the shared storage
# helper has no default, so this can never be confused with the cylinder `images` bucket.
IMAGES_BUCKET = "graviscan-images"

# The desktop uploads jpegs (gravi_scans.format defaults to 'jpeg'), so that is the fallback
# when an object path carries no extension of its own.
DEFAULT_EXTENSION = "jpg"

# What a plate download reports on, in logs and progress lines. A plate scan's unit of
# repetition is a capture in time, not a rotation frame.
NOUN = "capture"

__all__ = [
    "CollidingFrames",
    "DownloadResult",
    "FrameResult",
    "MANIFEST_NAME",
    "download",
]

# plates.csv schema: (output column, source key in a gravi_scans_extended row).
# `image_path` is derived (relative to the output directory).
_COLUMNS: list[tuple[str, str | None]] = [
    ("scan_id", "scan_id"),
    ("plate_id", "plate_id"),
    ("image_path", None),  # derived
    ("capture_date", "capture_date"),
    ("uploaded_at", "uploaded_at"),
    ("cycle_number", "cycle_number"),
    ("wave_number", "wave_number"),
    ("experiment_id", "experiment_id"),
    ("experiment_name", "experiment_name"),
    ("system_name", "system_name"),
    ("species_id", "species_id"),
    ("species_name", "species_name"),
    ("species_genus", "species_genus"),
    ("species_species", "species_species"),
    ("scanner_id", "scanner_id"),
    ("scanner_name", "scanner_name"),
    ("phenotyper_id", "phenotyper_id"),
    ("session_id", "session_id"),
    ("scan_mode", "scan_mode"),
    ("grid_mode", "grid_mode"),
    ("plate_index", "plate_index"),
    ("resolution", "resolution"),
    ("format", "format"),
    ("transplant_date", "transplant_date"),
    ("custom_note", "custom_note"),
    ("metadata_id", "metadata_id"),
    ("accession_id", "accession_id"),
    ("accession_name", "accession_name"),
]
CSV_COLUMNS: list[str] = [name for name, _ in _COLUMNS]

# plate_sections.csv: a plate's sections are one-to-many and their plants one-to-many again,
# so they cannot be flattened into plates.csv without duplicating scan rows. Joins back on
# metadata_id.
SECTION_COLUMNS = ["metadata_id", "plate_section_id", "medium", "plant_qr"]


def plate_relative_dir(scan: dict[str, Any]) -> str:
    """Per-plate image dir, relative to the output dir (where plates.csv lives).

    Grouped by plate so a continuous session's whole time series sits in one directory.
    """
    wave = safe_component(scan.get("wave_number"))
    plate = safe_component(scan.get("plate_id"))
    return f"images/Wave{wave}/{plate}"


def capture_filename(scan: dict[str, Any], image: dict[str, Any]) -> str:
    """Filename for one capture: cycle (when there is one) then the capture instant.

    `capture_date` alone is already unique per (experiment, plate) by
    idx_gravi_scans_natural_key. The cycle prefix is for readability and ordering, not
    uniqueness — and it is omitted for single-mode scans, which have no cycle.

    The cycle is zero-padded because the prefix decides the sort: unpadded, `c10` sorts
    between `c1` and `c2`, so a session of ten cycles or more reads out of order in a
    directory listing, in ffmpeg's glob, and in ImageJ's image sequence import. A
    gravitropic response is monotonic, so a reordered series still looks like a smooth
    curve — it is just the wrong one, with nothing to show it. Four digits covers a
    ten-minute cycle running for sixty-nine days.
    """
    ext = Path(image["object_path"]).suffix.lstrip(".") or DEFAULT_EXTENSION
    # Swap the timestamp's colons for dashes before sanitising: `safe_component` would map
    # them to underscores, which reads worse in a directory of timestamps. Either is safe —
    # a raw colon is not (on Windows it names an alternate data stream).
    stamp = safe_component(str(scan.get("capture_date")).replace(":", "-"))
    cycle = scan.get("cycle_number")
    if cycle is None:
        prefix = ""
    else:
        try:
            prefix = f"c{int(cycle):04d}_"
        except (TypeError, ValueError):  # not a number: keep it, unpadded, over dropping it
            prefix = f"c{safe_component(cycle)}_"
    return f"{prefix}{stamp}.{safe_component(ext)}"


def image_dest(out_dir: Path, scan: dict[str, Any], image: dict[str, Any]) -> Path:
    """Destination for one capture, preserving the object's real extension.

    Raises ValueError if the path would land outside ``out_dir``.
    """
    relative = f"{plate_relative_dir(scan)}/{capture_filename(scan, image)}"
    return contained_dest(Path(out_dir), relative)


def build_plate_row(scan: dict[str, Any], image: dict[str, Any] | None) -> dict[str, Any]:
    """Map a gravi_scans_extended row (plus its image, if any) to the ordered plates.csv row.

    An image row this can't build a path from leaves `image_path` empty rather than raising.
    The CSV is written before anything is fetched, so raising here would abort the whole run
    over one bad row — where the download itself records that row and carries on.
    """
    row: dict[str, Any] = {}
    for name, key in _COLUMNS:
        if name == "image_path":
            row[name] = _relative_image_path(scan, image)
        else:
            value = scan.get(key)
            row[name] = "" if value is None else value
    return row


def _relative_image_path(scan: dict[str, Any], image: dict[str, Any] | None) -> str:
    """Where this scan's image will land, relative to the output dir; "" if there isn't one."""
    if not image:
        return ""
    try:
        return f"{plate_relative_dir(scan)}/{capture_filename(scan, image)}"
    except (KeyError, TypeError):
        return ""


def write_plates_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """Write rows to plates.csv with the fixed column order.

    Rendered in full before anything touches the file: this runs on every invocation,
    including a re-run that resumes, so opening the existing plates.csv for writing would
    empty it before the first row was written and a full disk would leave it that way —
    the images intact, and the metadata that makes them interpretable gone.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_bytes(path, buffer.getvalue().encode("utf-8"))


def build_section_rows(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per (section, plant QR) pair, in the fixed column order."""
    return [{name: row.get(name, "") for name in SECTION_COLUMNS} for row in sections]


def write_sections_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """Write rows to plate_sections.csv with the fixed column order, atomically.

    Same reasoning as plates.csv: it is rewritten on every run, so a failed write must not
    be able to leave the previous run's copy truncated.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=SECTION_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_bytes(path, buffer.getvalue().encode("utf-8"))


# Which options decide the selection a download directory holds. `experiment_id` is the
# resolved id, so selecting the same experiment by name on one run and by id on the next still
# counts as the same download.
SELECTOR_KEYS = (
    "experiment_id",
    "scan_id",
    "plate_id",
    "wave_number",
    "session_id",
    "limit",
)


def download_selector(**options: Any) -> dict[str, Any]:
    """The options that decide which scans a run downloads."""
    return selector_of(SELECTOR_KEYS, options)


# --- supabase / storage I/O -------------------------------------------------

def _queried(what: str, call: Callable[[], Any]) -> Any:
    """Run one metadata query, turning a server error into something a scientist can act on.

    Permission denied, a gateway blip, an unapplied migration — all of them arrive as an
    APIError, and unhandled they reach the user as a Python traceback with the useful sentence
    buried in it. `what` names the query so the message says which one failed.
    """
    from postgrest import APIError

    try:
        return call()
    except APIError as exc:
        detail = getattr(exc, "message", None) or str(exc)
        raise click.ClickException(f"Could not read {what} from Bloom: {detail}") from exc


def fetch_plate_scans(
    client: Any,
    experiment_id: int,
    *,
    plate_id: str | None = None,
    wave_number: int | None = None,
    session_id: int | None = None,
    limit: int = 100000,
) -> list[dict[str, Any]]:
    """Query gravi_scans_extended for an experiment, narrowed by any supplied filter.

    Ordered by scan_id so that `--limit` samples the same captures every time. Without it the
    rows come back in whatever order the plan produces — stable in practice, but incidental,
    and a sample nobody can reproduce is not much of a sample.
    """
    query = client.table("gravi_scans_extended").select("*").eq("experiment_id", experiment_id)
    if plate_id is not None:
        query = query.eq("plate_id", plate_id)
    if wave_number is not None:
        query = query.eq("wave_number", wave_number)
    if session_id is not None:
        query = query.eq("session_id", session_id)
    return query.order("scan_id").limit(limit).execute().data or []


def fetch_plate_scan(client: Any, scan_id: Any) -> dict[str, Any] | None:
    """Single gravi_scans_extended row for one scan_id, or None if not found."""
    rows = (
        client.table("gravi_scans_extended")
        .select("*")
        .eq("scan_id", scan_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def fetch_plate_images(client: Any, scan_ids: list[Any]) -> dict[Any, dict[str, Any]]:
    """Map scan_id -> its image row.

    Batched rather than one query per scan: gravi_images is UNIQUE(scan_id), so there is no
    per-scan list to page through and a whole batch comes back in one round trip. The batches
    exist only because the `in.(…)` filter travels in the URL — see ``id_batches``.
    """
    ids = [i for i in scan_ids if i is not None]
    rows = fetch_in_batches(
        lambda batch: client.table("gravi_images").select("*").in_("scan_id", batch), ids
    )
    return {row["scan_id"]: row for row in rows}


def fetch_plate_sections(client: Any, metadata_ids: list[Any]) -> list[dict[str, Any]]:
    """Per-plate section metadata flattened to (metadata_id, section, medium, plant QR) rows."""
    ids = sorted({i for i in metadata_ids if i is not None})
    if not ids:
        return []
    sections = fetch_in_batches(
        lambda batch: client.table("gravi_scan_metadata_sections")
        .select("*")
        .in_("metadata_id", batch),
        ids,
    )
    if not sections:
        return []
    by_id = {s["id"]: s for s in sections}
    plants = fetch_in_batches(
        lambda batch: client.table("gravi_scan_metadata_section_plants")
        .select("*")
        .in_("section_id", batch),
        sorted(by_id),
    )
    by_section: dict[Any, list[dict[str, Any]]] = {}
    for plant in plants:
        by_section.setdefault(plant.get("section_id"), []).append(plant)

    rows = []
    for section_id, section in by_id.items():
        base = {
            "metadata_id": section.get("metadata_id"),
            "plate_section_id": section.get("plate_section_id"),
            "medium": section.get("medium"),
        }
        # A section with no plant QRs recorded still gets a row, with an empty plant_qr.
        # Dropping it would lose that section's `medium` — the growth condition is a property
        # of the section, not of the plants in it, and it is what most analyses group by.
        found = by_section.get(section_id) or [{}]
        rows += [{**base, "plant_qr": plant.get("plant_qr", "")} for plant in found]
    return rows


def search_experiments(client: Any, query: str, species: str | None = None) -> list[dict[str, Any]]:
    """Server-side experiment name search via the gravi_experiment_search RPC.

    The query (and optional species) are passed as bound RPC arguments — never concatenated
    into SQL — so no user text can alter the query. Results carry `system_name`, because one
    experiment name can legitimately exist on more than one GraviScan rig.
    """
    params: dict[str, Any] = {"p_query": query}
    if species:
        params["p_species"] = species
    return client.rpc("gravi_experiment_search", params).execute().data or []


def download_plate_image(
    client: Any,
    scan: dict[str, Any],
    image: dict[str, Any],
    out_dir: Path,
    *,
    stop: threading.Event | None = None,
) -> FrameResult:
    """Download one plate capture to its destination, returning the outcome.

    Never raises: any failure is recorded on the result instead, so one bad capture can't
    abort the run. Safe to call from a worker thread.

    `gravi_images.file_size_bytes` gives resume a real completeness check — a truncated file
    is re-fetched rather than skipped forever. It is nullable, in which case the check falls
    back to the file being non-empty.
    """
    object_path = image.get("object_path", "")
    result = FrameResult(scan.get("scan_id"), scan.get("cycle_number"), object_path, ok=False)
    try:
        dest = image_dest(out_dir, scan, image)
    except (KeyError, TypeError) as exc:  # a bare key or pathlib error explains nothing
        result.error = f"malformed gravi_images row: {exc}"
        return result
    except ValueError as exc:
        result.error = str(exc)
        return result
    fetched = download_to(
        client,
        object_path,
        dest,
        bucket=IMAGES_BUCKET,
        expected_size=image.get("file_size_bytes"),
        stop=stop,
    )
    result.ok, result.skipped, result.error, result.note = fetched
    return result


def find_capture_collisions(
    out_dir: Path, work: list[tuple[dict[str, Any], dict[str, Any]]]
) -> list[str]:
    """Describe every pair of captures that would land on the same file.

    The destination is built from wave, plate and capture instant, any of which can be empty
    in the database — and two rows with an empty value are not caught by a uniqueness
    constraint. Without this check the second is quietly skipped as already-downloaded and its
    image never arrives.
    """
    return find_collisions(
        Path(out_dir),
        work,
        lambda pair: image_dest(out_dir, pair[0], pair[1]),
        lambda pair: f"scan {pair[0].get('scan_id')!r} plate {pair[0].get('plate_id')!r}",
    )


def download_images(
    client: Any,
    scans: list[dict[str, Any]],
    images: dict[Any, dict[str, Any]],
    out_dir: Path,
    *,
    workers: int = DEFAULT_WORKERS,
    on_progress: Callable[[str, int, int, int], None] | None = None,
) -> DownloadResult:
    """Download every selected scan's image from Storage bucket `graviscan-images`.

    One image per scan, so there is no listing phase — ``images`` is already the whole map,
    fetched in one query. A scan missing from it has no row in gravi_images, which happens
    when an upload was interrupted; that is noted in the log but is not a failure, since there
    is nothing to fetch and every re-run would report it again.

    Captures already written by an earlier run are skipped, which is what makes an interrupted
    download cheap to resume.
    """
    sweep_orphan_temps(Path(out_dir))

    # One entry per log line, in scan order: either a scan with no image or a capture to fetch.
    slots: list[Any] = []
    for scan in scans:
        image = images.get(scan.get("scan_id"))
        if image is None:
            slots.append(
                FrameResult(
                    scan.get("scan_id"), None, "", ok=False, error="no image", no_frames=True
                )
            )
        else:
            slots.append((scan, image))

    work = [slot for slot in slots if not isinstance(slot, FrameResult)]

    clashes = find_capture_collisions(Path(out_dir), work)
    if clashes:
        raise CollidingFrames("; ".join(clashes))

    stop = threading.Event()

    def _one(pair: tuple[dict[str, Any], dict[str, Any]]) -> FrameResult:
        return download_plate_image(client, pair[0], pair[1], out_dir, stop=stop)

    done = failed = 0

    def _tick(result: FrameResult) -> None:
        # Counting completions alone would show 100% on a run where every capture failed.
        nonlocal done, failed
        done += 1
        if not result.ok:
            failed += 1
        if on_progress is not None:
            on_progress("downloading", done, len(work), failed)

    fetched = fetch_all(work, _one, workers=workers, on_done=_tick)

    outcomes = iter(fetched)
    frames = [slot if isinstance(slot, FrameResult) else next(outcomes) for slot in slots]
    return DownloadResult(frames, disk_full=stop.is_set())


# --- command ----------------------------------------------------------------


@click.command(name="download")
@click.argument("out_dir", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--experiment-id",
    "--experiment_id",
    "experiment_id",
    type=int,
    default=None,
    help="Download a whole plate experiment by ID (mutually exclusive with --scan-id).",
)
@click.option(
    "--scan-id",
    "--scan_id",
    "scan_id",
    type=int,
    default=None,
    help="Download a single plate scan by ID (mutually exclusive with --experiment-id).",
)
@click.option(
    "--experiment-name",
    "--experiment_name",
    "experiment_name",
    default=None,
    help="Resolve the experiment to download by name (case-insensitive substring); an ambiguous "
    "name lists candidates with their rig and exits without downloading. Mutually exclusive "
    "with --experiment-id / --scan-id.",
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
    help="Write plates.csv only; skip image download.",
)
@click.option(
    "--plate-id",
    "--plate_id",
    "plate_id",
    default=None,
    help="Restrict to a single plate barcode.",
)
@click.option(
    "--wave-number",
    "--wave_number",
    "wave_number",
    type=int,
    default=None,
    help="Restrict to one wave.",
)
@click.option(
    "--session-id",
    "--session_id",
    "session_id",
    type=int,
    default=None,
    help="Restrict to one scan session (one continuous run of cycles).",
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
    plate_id: str | None,
    wave_number: int | None,
    session_id: int | None,
    limit: int,
    workers: int,
) -> None:
    """Download a plate experiment (--experiment-id / --experiment-name) or a single scan
    (--scan-id): metadata (plates.csv) and per-plate images."""
    from .. import auth
    from ..credentials import load_credentials
    from ..cyl._resolve import Ambiguous, NoMatch, Resolved, classify

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
            # system_name is on every line: gravi_experiments is UNIQUE(species_id, name,
            # system_name), so two rigs can hold the same name and the rows would otherwise
            # be indistinguishable.
            listing = "\n".join(
                f"  {m.id}  {m.label}  {_rig_of(found, m.id)}  {m.created or ''}"
                for m in outcome.candidates
            )
            raise click.ClickException(
                f"{len(outcome.candidates)} experiments match {experiment_name!r} — "
                f"narrow it (--species) or pass --experiment-id:\n{listing}"
            )
        assert isinstance(outcome, Resolved)
        experiment_id = outcome.match.id
        click.echo(f"Matched: {outcome.match.label} (id {experiment_id})", err=True)

    if scan_id is not None:
        scan = _queried("this scan", lambda: fetch_plate_scan(client, scan_id))
        if scan is None:
            raise click.ClickException(f"Scan {scan_id} not found.")
        scans = [scan]
    else:
        scans = _queried(
            "the scans for this experiment",
            lambda: fetch_plate_scans(
                client,
                experiment_id,
                plate_id=plate_id,
                wave_number=wave_number,
                session_id=session_id,
                limit=limit,
            ),
        )

    if not scans:
        raise click.ClickException(
            "No scans matched, so there is nothing to download. Check the experiment and any "
            "--plate-id / --wave-number / --session-id filters."
        )

    images = _queried(
        "the image rows for these scans",
        lambda: fetch_plate_images(client, [s.get("scan_id") for s in scans]),
    )
    rows = [build_plate_row(s, images.get(s.get("scan_id"))) for s in scans]

    out = Path(out_dir)
    selector = download_selector(
        experiment_id=experiment_id,
        scan_id=scan_id,
        plate_id=plate_id,
        wave_number=wave_number,
        session_id=session_id,
        limit=limit,
    )
    if holds_an_unidentified_download(out):
        raise click.ClickException(
            f"{out} already holds images but no {MANIFEST_NAME}, so there is no way to tell "
            f"which download they belong to. Downloading here risks mixing two experiments "
            f"in one directory. Download into a new directory instead."
        )

    mismatch = describe_manifest_mismatch(read_manifest(out), selector)
    if mismatch:
        raise click.ClickException(
            f"{out} already holds a different download ({mismatch}). Give each selection its "
            f"own directory. Re-running the same command here resumes where it left off."
        )

    csv_path = out / "plates.csv"
    write_plates_csv(rows, csv_path)
    write_manifest(out, selector)
    click.echo(f"Wrote {len(rows)} scans -> {csv_path}")

    sections = build_section_rows(
        _queried(
            "the plate section metadata (plates.csv is already written)",
            lambda: fetch_plate_sections(client, [s.get("metadata_id") for s in scans]),
        )
    )
    if sections:
        sections_path = out / "plate_sections.csv"
        write_sections_csv(sections, sections_path)
        click.echo(f"Wrote {len(sections)} section rows -> {sections_path}")

    if meta_only:
        return

    try:
        result = download_images(
            client,
            scans,
            images,
            out,
            workers=workers,
            on_progress=ProgressReporter(noun=f"{NOUN}s"),
        )
    except CollidingFrames as exc:
        raise click.ClickException(
            f"{exc}. Refusing to download, because one capture's image would overwrite or mask "
            f"another's. Narrow the download (--scan-id / --plate-id) or fix the rows."
        ) from exc

    log_path = out / "download_log.txt"
    # Counts first: on a full disk the log write is what fails, and the numbers matter more.
    click.echo(
        f"{result.ok:,}/{result.total:,} captures present in {out / 'images'} "
        f"({result.downloaded:,} downloaded this run, {result.skipped:,} already on disk)"
    )
    try:
        write_download_log(result, log_path, noun=NOUN)
    except OSError as exc:
        click.echo(f"Could not write {log_path.name}: {exc.strerror or exc}", err=True)
    else:
        click.echo(f"Log: {log_path}")
    if result.scans_without_frames:
        click.echo(
            f"Note: {result.scans_without_frames} scan(s) have no image recorded in Bloom, "
            f"so there was nothing to download for them (listed in the log).",
            err=True,
        )
    if result.incomplete:
        # Partial download: surface it and exit non-zero so a pipeline knows the output is
        # incomplete (the log lists every failed capture).
        problems = []
        if result.failed:
            problems.append(f"{result.failed} of {result.total} captures failed to download")
        if result.scans_unlisted:
            problems.append(
                f"{result.scans_unlisted} scan(s) could not be listed at all "
                f"(an unknown number of further captures is missing)"
            )
        raise click.ClickException(
            f"{'; '.join(problems)} — see {log_path}. "
            "Re-running the same command retries only the captures still missing."
        )


def _rig_of(found: list[dict[str, Any]], experiment_id: Any) -> str:
    """The system_name for one candidate, for the ambiguous-match listing."""
    for row in found:
        if row.get("id") == experiment_id:
            return str(row.get("system_name") or "-")
    return "-"
