"""`bloomctl cyl download-for-predict`: stage one scan in the layout
`sleap_roots_predict.discover_scans` expects.

Pure helpers (sidecar assembly, path derivation, checksum) are separated from
the supabase/storage I/O so the contract is unit-testable without a live
server — mirroring ``download.py`` and ``ingest.py``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4

import click
from pydantic import ValidationError
from sleap_roots_contracts import (
    RunManifest,
    pipeline_run_id_from_env,
    run_manifest_name_for_writing,
)

from .._download import (
    DEFAULT_WORKERS,
    MAX_WORKERS,
    DownloadResult,
    FrameResult,
    download_to,
    fetch_all,
)
from .._storage import atomic_write_bytes
from ..credentials import DEFAULT_PROFILE
from ._batch import BatchResult, ScanResult, format_json, format_summary
from ._locks import (
    DEFAULT_LOCK_STALENESS_SECONDS,
    LOCKS_DIRNAME,
    MANIFEST_LOCK_FILENAME,
    LockContendedError,
    acquire_lock,
)
from .download import IMAGES_BUCKET, fetch_images, fetch_scan

# Matches sleap_roots_predict.batch._IMAGE_EXTENSIONS — the exact set discover_scans
# globs for, so clearing the stage directory removes anything predict would pick up.
_IMAGE_EXTENSIONS = frozenset({".png", ".tif", ".tiff", ".jpg", ".jpeg"})


def scan_key_for(scan_id: Any) -> str:
    """The sidecar's scan_key — must equal the filename stem (predict validates this)."""
    return f"scan_{scan_id}"


def frame_dest_for_predict(scan_dir: Path, image: dict[str, Any]) -> Path:
    """Absolute destination for one frame, co-located with the sidecar."""
    ext = Path(image["object_path"]).suffix or ".png"
    return Path(scan_dir) / f"{image['frame_number']}{ext}"


def compute_checksum(frame_bytes_list: list[bytes]) -> str:
    """sha256 over frame bytes concatenated in the given (DB frame_number) order."""
    digest = hashlib.sha256()
    for data in frame_bytes_list:
        digest.update(data)
    return f"sha256:{digest.hexdigest()}"


def validate_frame_numbers(images: list[dict[str, Any]]) -> None:
    """Raise ValueError if any frame_number is null or duplicated across images.

    A null/duplicate frame_number would map two cyl_images rows onto the same
    on-disk filename, so the sidecar's image_ids/images_checksum would no
    longer describe what's actually written to disk (see design.md).
    """
    seen: set[Any] = set()
    for image in images:
        frame_number = image.get("frame_number")
        if frame_number is None:
            raise ValueError(f"cyl_images row {image.get('id')} has a null frame_number")
        if frame_number in seen:
            raise ValueError(f"duplicate frame_number {frame_number!r} in cyl_images")
        seen.add(frame_number)


def resolve_sidecar_params(scan: dict[str, Any]) -> dict[str, Any]:
    """Resolve the sidecar's params via the contracts oracle.

    `mode` is forced via `resolve_params`'s documented `overrides` mechanism (not
    row augmentation — see design.md) since every `cyl` scan is cylinder-scanner.
    Extracted from `build_sidecar` so callers can validate scan metadata resolves
    cleanly *before* any destructive filesystem action (see design.md).
    """
    from sleap_roots_contracts import resolve_params

    return resolve_params(scan, overrides={"mode": "cylinder"}).values


def build_sidecar(
    scan: dict[str, Any],
    images: list[dict[str, Any]],
    frame_bytes_list: list[bytes],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the scan_metadata.json sidecar dict for one scan.

    `params` is the already-resolved dict from `resolve_sidecar_params` — resolved
    ahead of time so a metadata-resolution failure surfaces before any download or
    directory-clearing happens, not after.

    `image_ids`/`images_checksum` are built via `InputRef` rather than a bare dict so
    Pydantic enforces the same `image_ids: list[str]` shape `trait_extractor`'s
    `ScanMetadata` requires — catching a type mismatch here, at construction, instead
    of downstream in trait_extractor validation (see bloom#555).
    """
    from sleap_roots_contracts import InputRef

    input_ref = InputRef(
        image_ids=[str(image["id"]) for image in images],
        images_checksum=compute_checksum(frame_bytes_list),
    )
    return {
        "scan_key": scan_key_for(scan["scan_id"]),
        "params": params,
        **input_ref.model_dump(),
    }


def write_sidecar(sidecar: dict[str, Any], path: Path) -> None:
    """Write the sidecar as valid UTF-8 JSON, creating the parent dir if absent.

    Atomic (temp file + `os.replace`) — a crash mid-write leaves the destination
    either absent or with its prior content, never truncated.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(sidecar), encoding="utf-8")
    os.replace(tmp, path)


def clear_scan_dir(scan_dir: Path) -> list[str]:
    """Remove scan_dir entirely if it exists; return the names removed.

    Called at the *start* of every invocation, before any download, so no frame
    or sidecar from a previous invocation can survive into — or be silently
    mistaken for part of — this invocation's output (see design.md: this
    supersedes the narrower end-of-run stray-frame reconciliation, which didn't
    close the case of a stale sidecar from an earlier successful run surviving a
    later partial-failure retry).
    """
    if not scan_dir.exists():
        return []
    removed = sorted(p.name for p in scan_dir.iterdir())
    shutil.rmtree(scan_dir)
    return removed


# --- batch: scan_ids input ----------------------------------------------------


def read_scan_ids(source: str, *, stdin: TextIO | None = None) -> list[int]:
    """Parse a JSON array of integer scan_ids from a path, or from stdin when ``source`` is ``-``.

    Raises ``ValueError`` (readable message) if the source doesn't exist / isn't a file, isn't
    valid JSON, or doesn't parse to an array of integers. An empty array is valid input (the
    empty-batch no-op case), not an error.
    """
    if source == "-":
        stream = stdin if stdin is not None else sys.stdin
        text = stream.read()
        where = "stdin"
    else:
        where = repr(source)
        path = Path(source)
        if not path.is_file():
            raise ValueError(f"scan_ids source {where} does not exist or is not a file")
        text = path.read_text(encoding="utf-8")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"scan_ids source {where} is not valid JSON: {exc}") from exc

    if not isinstance(data, list) or not all(
        isinstance(x, int) and not isinstance(x, bool) for x in data
    ):
        raise ValueError(f"scan_ids source {where} must be a JSON array of integers")
    return data


def parse_scan_ids_flag(value: str) -> list[int]:
    """Parse a comma-separated ``--scan-ids`` flag value (e.g. ``"1,2,3"``) into a list of ints."""
    parts = [p.strip() for p in value.split(",") if p.strip()]
    try:
        return [int(p) for p in parts]
    except ValueError as exc:
        raise ValueError(
            f"--scan-ids must be a comma-separated list of integers, got {value!r}"
        ) from exc


def scan_is_already_staged(scan_dir: Path, scan_key: str) -> bool:
    """True iff ``scan_dir`` already has a valid sidecar for ``scan_key`` (skip-if-done check).

    Mirrors the validity check ``sleap_roots_predict.batch._load_scan`` itself applies: the
    sidecar must exist, parse as JSON, and its ``scan_key`` field must match. A missing,
    unparseable, or mismatched sidecar is treated as not staged.

    Additionally rejects a sidecar whose ``image_ids`` aren't all ``str`` — the exact shape
    a pre-bloom#555-fix ``build_sidecar`` wrote — so a scan staged before that fix shipped is
    re-staged with corrected ids instead of being skipped forever by this resume check. A
    sidecar with no ``image_ids`` key at all is unaffected by this check (not a real
    ``build_sidecar`` output; kept staged per the ``scan_key`` check above).
    """
    sidecar_path = scan_dir / f"{scan_key}.scan_metadata.json"
    if not sidecar_path.is_file():
        return False
    try:
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict) or data.get("scan_key") != scan_key:
        return False
    image_ids = data.get("image_ids")
    if image_ids is not None and not (
        isinstance(image_ids, list) and all(isinstance(x, str) for x in image_ids)
    ):
        return False
    return True


# --- supabase / storage I/O -------------------------------------------------


def _download_one_frame_for_predict(
    client: Any,
    scan: dict[str, Any],
    image: dict[str, Any],
    scan_dir: Path,
    *,
    stop: threading.Event | None = None,
) -> tuple[FrameResult, bytes | None]:
    """Download one frame into the predict layout; never raises (safe to call from a worker
    thread), mirroring ``download_plate_image``'s dest-resolution guard around ``download_to``.

    ``stop`` is set when the disk fills; frames not yet started are recorded failed without
    being fetched (``download_to``'s own behavior — see `cyl download`'s/`plate download`'s
    identical orchestrators).
    """
    object_path = image.get("object_path", "")
    result = FrameResult(scan.get("scan_id"), image.get("frame_number"), object_path, ok=False)
    try:
        dest = frame_dest_for_predict(scan_dir, image)
    except (KeyError, TypeError) as exc:  # a bare key or pathlib error explains nothing
        result.error = f"malformed cyl_images row: {exc}"
        return result, None
    fetched = download_to(client, object_path, dest, bucket=IMAGES_BUCKET, stop=stop)
    result.ok, result.skipped, result.error, result.note = fetched
    if not result.ok:
        return result, None
    try:
        return result, dest.read_bytes()
    except OSError as exc:  # written successfully but couldn't be read back; never raise
        result.ok = False
        result.error = f"downloaded but could not read it back for the checksum: {exc}"
        return result, None


def download_frames_for_predict(
    client: Any,
    scan: dict[str, Any],
    images: list[dict[str, Any]],
    scan_dir: Path,
    *,
    workers: int = DEFAULT_WORKERS,
) -> tuple[DownloadResult, list[bytes]]:
    """Download every frame for one scan into the nested predict layout.

    Frames are fetched by up to ``workers`` threads at once — the same bounded pool
    ``download_images`` uses for the sibling `cyl download` command (PR #623, bloom #652).
    ``workers <= 1`` runs one at a time, on the calling thread, with no pool at all.

    Returns the aggregate result plus each successfully-downloaded frame's bytes in DB
    frame_number order (for the checksum) — a failed frame contributes no bytes entry.
    `fetch_all` returns outcomes in ``images`` order regardless of which thread finishes
    first, so this ordering holds under concurrency the same way it held for the old
    sequential loop.

    A ``stop`` Event is created for every call, regardless of ``workers`` — once one frame's
    write fails because the disk is full or the quota is spent, every frame not yet started
    (including under ``workers=1``, where this stops the very next queued frame) is recorded
    failed without being fetched, rather than each independently attempting and failing.
    """
    stop = threading.Event()
    outcomes = fetch_all(
        images,
        lambda image: _download_one_frame_for_predict(client, scan, image, scan_dir, stop=stop),
        workers=workers,
    )
    frames = [frame for frame, _ in outcomes]
    frame_bytes = [data for _, data in outcomes if data is not None]
    return DownloadResult(frames, disk_full=stop.is_set()), frame_bytes


# --- command ----------------------------------------------------------------


@click.command(name="download-for-predict")
@click.argument("scan_id", type=int)
@click.argument("out_dir", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
@click.option(
    "-n",
    "--workers",
    type=click.IntRange(min=1, max=MAX_WORKERS),
    default=DEFAULT_WORKERS,
    show_default=True,
    help=f"Concurrent frame downloads (I/O-bound, 1-{MAX_WORKERS}). 1 = sequential.",
)
def download_for_predict(scan_id: int, out_dir: Path, profile: str, workers: int) -> None:
    """Stage one cylinder scan (SCAN_ID) into OUT_DIR in the layout
    sleap_roots_predict.discover_scans expects — frames co-located with a
    scan_metadata.json sidecar. Distinct from `cyl download`'s scans.csv layout."""
    from ..cli import _authed_client

    client = _authed_client(profile)

    scan = fetch_scan(client, scan_id)
    if scan is None:
        raise click.ClickException(f"Scan {scan_id} not found.")

    images = fetch_images(client, scan_id)
    if not images:
        raise click.ClickException(f"No frames found for scan {scan_id}.")

    try:
        validate_frame_numbers(images)
        params = resolve_sidecar_params(scan)
    except ValueError as exc:
        raise click.ClickException(f"Scan {scan_id}: {exc}") from exc

    scan_dir = Path(out_dir) / scan_key_for(scan_id)
    removed = clear_scan_dir(scan_dir)
    if removed:
        click.echo(f"Cleared {len(removed)} existing file(s) from a previous run: {scan_dir}")

    result, frame_bytes = download_frames_for_predict(
        client, scan, images, scan_dir, workers=workers
    )

    if result.failed:
        cause = "the disk filled up or the storage quota was spent; " if result.disk_full else ""
        raise click.ClickException(
            f"{cause}{result.failed} of {result.total} frames failed to download — "
            f"frames downloaded this run remain in {scan_dir}; no sidecar written."
        )

    sidecar = build_sidecar(scan, images, frame_bytes, params)
    sidecar_path = scan_dir / f"{scan_key_for(scan_id)}.scan_metadata.json"
    write_sidecar(sidecar, sidecar_path)
    click.echo(f"Staged {result.ok}/{result.total} frames -> {scan_dir}  (sidecar: {sidecar_path})")


# --- batch: non-raising per-scan core ----------------------------------------


def stage_one_scan(
    client: Any,
    scan_id: Any,
    out_dir: Path,
    staleness_seconds: float = DEFAULT_LOCK_STALENESS_SECONDS,
    workers: int = DEFAULT_WORKERS,
) -> ScanResult:
    """Stage one scan, isolating any failure into a `ScanResult` instead of raising.

    Sequences the same pure helpers `download_for_predict` (the single-scan command) calls, but
    never raises — a batch caller isolates one scan's failure and continues the others. Skips
    (``status="skipped"``) a scan already staged with a valid sidecar (see
    `scan_is_already_staged`); this command does not touch `download_for_predict`'s own
    unconditional clear-and-redownload behavior.

    Holds an exclusive lock at ``out_dir/.locks/{scan_key}.lock`` from the skip-check through the
    sidecar write, so two invocations racing on the *same* scan_id can't both pass the skip-check
    and clobber each other's writes (bloom #533). Lock contention is isolated the same way every
    other per-scan failure is — a `failed` `ScanResult`, not a raised exception.
    """
    scan_key = scan_key_for(scan_id)
    scan_dir = Path(out_dir) / scan_key
    lock_path = Path(out_dir) / LOCKS_DIRNAME / f"{scan_key}.lock"

    try:
        with acquire_lock(lock_path, staleness_seconds=staleness_seconds):
            if scan_is_already_staged(scan_dir, scan_key):
                return ScanResult(scan_key, "skipped")

            scan = fetch_scan(client, scan_id)
            if scan is None:
                return ScanResult(scan_key, "failed", f"Scan {scan_id} not found.")

            images = fetch_images(client, scan_id)
            if not images:
                return ScanResult(scan_key, "failed", f"No frames found for scan {scan_id}.")

            try:
                validate_frame_numbers(images)
                params = resolve_sidecar_params(scan)
            except ValueError as exc:
                return ScanResult(scan_key, "failed", f"Scan {scan_id}: {exc}")

            clear_scan_dir(scan_dir)
            result, frame_bytes = download_frames_for_predict(
                client, scan, images, scan_dir, workers=workers
            )
            if result.failed:
                cause = (
                    "the disk filled up or the storage quota was spent; "
                    if result.disk_full
                    else ""
                )
                return ScanResult(
                    scan_key,
                    "failed",
                    f"{cause}{result.failed} of {result.total} frames failed to download "
                    f"for scan {scan_id}.",
                )

            sidecar = build_sidecar(scan, images, frame_bytes, params)
            sidecar_path = scan_dir / f"{scan_key}.scan_metadata.json"
            write_sidecar(sidecar, sidecar_path)
            return ScanResult(scan_key, "ok")
    except Exception as exc:  # batch isolation: a transient network/auth/OS error on one
        # scan (including LockContendedError) must never abort the rest of the batch (review
        # finding: this was previously uncaught, mirroring download_images's own per-frame
        # "record and continue" discipline).
        return ScanResult(scan_key, "failed", str(exc))


# --- batch: RunManifest write (bloom #653; per-run name + overwrite, bloom #934) ----


def resolve_pipeline_run_id(run_id: str | None) -> str:
    """The `pipeline_run_id` to stamp inside the manifest: `run_id` (the run identity from
    `pipeline_run_id_from_env()`) when there is one, else a freshly generated local placeholder.

    The placeholder is distinct per invocation (not a fixed sentinel) so separate manual/dev
    runs are distinguishable from each other in the manifest. It is only ever stamped inside
    the file, never used to name it: a reader cannot reproduce another process's placeholder,
    so a file named after it would be invisible to every reader (sleap-roots-pipeline#71
    design section 2.3). This function reads no environment itself, so the stamped id and the
    file name can never disagree (design section 3.2).
    """
    return run_id or f"local-{uuid4().hex[:8]}"


def resolve_manifest_name(run_id: str | None) -> str:
    """The manifest file name for `run_id`, or a `click.ClickException` naming the value when
    the contract rejects it (an id that is not a safe path component, or is too long)."""
    try:
        return run_manifest_name_for_writing(run_id)
    except ValueError as exc:
        raise click.ClickException(
            f"ARGO_WORKFLOW_NAME={run_id!r} cannot name a run manifest: {exc}"
        ) from exc


def write_run_manifest(
    out_dir: Path,
    result: BatchResult,
    *,
    manifest_name: str,
    pipeline_run_id: str | None,
    staleness_seconds: float,
) -> None:
    """Write this invocation's usable scan_keys to `out_dir / manifest_name`, replacing it.

    Usable scan_keys are every scan whose result was `ok` or `skipped` this run (excludes
    `failed`). The write overwrites rather than unions: an existing manifest is never read, so
    a retry of the same run cannot latch a key from a failed earlier attempt, and a stale
    legacy `run_manifest.json` beside a per-run file is never touched (sleap-roots-pipeline#71
    design section 2.4). Skips the write entirely if there is nothing usable to record
    (`RunManifest` itself rejects an empty list), leaving any existing file of that name alone.
    Raises `click.ClickException` for every failure mode here — manifest-lock contention, an
    `OSError` from the lock's own file operations or from `atomic_write_bytes`, or a
    `RunManifest` construction failure — rather than letting any of them surface as a raw
    traceback.
    """
    scan_keys = sorted({s.scan_key for s in result.scans if s.status in ("ok", "skipped")})
    if not scan_keys:
        return

    manifest_path = Path(out_dir) / manifest_name
    lock_path = Path(out_dir) / LOCKS_DIRNAME / MANIFEST_LOCK_FILENAME

    try:
        with acquire_lock(lock_path, staleness_seconds=staleness_seconds):
            manifest = RunManifest(
                pipeline_run_id=resolve_pipeline_run_id(pipeline_run_id), scan_keys=scan_keys
            )
            atomic_write_bytes(manifest_path, manifest.model_dump_json().encode("utf-8"))
    except (LockContendedError, OSError, ValidationError) as exc:
        # OSError covers disk-full/permission failures from the lock's own file operations or
        # from atomic_write_bytes. ValidationError guards the RunManifest(...) construction
        # itself — practically unreachable (scan_keys is deduplicated via a set and every
        # entry comes from scan_key_for()'s fixed format, so RunManifest's own
        # empty/duplicate/blank checks can't trip), but kept so a future change to either
        # invariant fails loud via ClickException rather than a raw traceback.
        raise click.ClickException(str(exc)) from exc


# --- batch: command -----------------------------------------------------------


@click.command(name="batch-download-for-predict")
@click.argument("out_dir", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--scan-ids-file",
    "scan_ids_file",
    default=None,
    help="Path to a JSON array of scan_ids, or - for stdin. Alternative to --scan-ids.",
)
@click.option(
    "--scan-ids",
    "scan_ids_flag",
    default=None,
    help="Comma-separated scan_ids (e.g. 1,2,3). Alternative to --scan-ids-file.",
)
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the batch result as a JSON array on stdout.",
)
@click.option(
    "--lock-staleness-seconds",
    "lock_staleness_seconds",
    type=click.FloatRange(min=0, min_open=True),
    default=DEFAULT_LOCK_STALENESS_SECONDS,
    show_default=True,
    help="Age (seconds) past which a per-scan or manifest lock is considered abandoned "
    "and reclaimable. Must be strictly positive: 0 (or negative) would treat a lock's age "
    "as always past the threshold, reclaiming even a lock held a moment ago.",
)
@click.option(
    "-n",
    "--workers",
    type=click.IntRange(min=1, max=MAX_WORKERS),
    default=DEFAULT_WORKERS,
    show_default=True,
    help=f"Concurrent frame downloads per scan (I/O-bound, 1-{MAX_WORKERS}). 1 = sequential.",
)
@click.pass_context
def batch_download_for_predict(
    ctx: click.Context,
    out_dir: Path,
    scan_ids_file: str | None,
    scan_ids_flag: str | None,
    profile: str,
    as_json: bool,
    lock_staleness_seconds: float,
    workers: int,
) -> None:
    """Stage every scan_id (from --scan-ids-file, a JSON array file or - for stdin, or
    --scan-ids, a comma-separated list) into OUT_DIR, one nested {scan_key}/ directory per
    scan — the batch sibling of `download-for-predict`. Isolates per-scan failures, including
    lock contention on a scan (one bad or contended scan doesn't abort the batch); exits 0 if
    every scan succeeded, was skipped, or the input was empty, and exits 3 (not 1, mirroring
    sleap_roots_predict/trait_extractor's own `0`/`3` convention) if at least one scan failed
    — one scan or every scan; 3 only means "not every scan succeeded," never "some scan did"
    (check the written RunManifest or --json output for which scans actually staged). A usage
    error still exits 2, and a manifest-lock/write failure or an ARGO_WORKFLOW_NAME that
    cannot name a run manifest exits 1, independent of any scan's outcome (bloom #772).

    Each scan's frames download via up to `--workers` concurrent threads (bloom #652); scans
    themselves are still staged one at a time.

    After every scan is processed, writes a `sleap_roots_contracts.RunManifest` recording
    every usable (`ok` or `skipped`) scan_key into OUT_DIR, replacing any existing file of the
    same name: `run_manifest.<ARGO_WORKFLOW_NAME>.json` inside Argo, `run_manifest.json`
    without it (bloom #934). The write happens under a lock separate from the per-scan locks
    `stage_one_scan` holds (bloom #533, #481). `--lock-staleness-seconds` controls the age at
    which both kinds of lock are considered abandoned and reclaimable.

    NB: an early draft took the scan_ids source as a positional argument alongside OUT_DIR, but
    Click cannot disambiguate an omitted optional positional from a required one that follows
    it (verified: with only one positional token left after consuming a mutually-exclusive
    flag, Click fills the first-declared slot regardless of which one is actually required) —
    so both scan_ids inputs are options, and OUT_DIR is the only positional argument.
    """
    if (scan_ids_file is None) == (scan_ids_flag is None):
        raise click.UsageError("Pass exactly one of --scan-ids-file or --scan-ids.")

    if not math.isfinite(lock_staleness_seconds):
        # click.FloatRange(min=0, min_open=True) lets `nan` straight through — NaN
        # comparisons are always False, so its own range check never rejects it, and a NaN
        # threshold would silently make every lock look immediately reclaimable. Caught here,
        # before any work starts, rather than only inside acquire_lock's own defense-in-depth
        # ValueError (which would otherwise surface late, and inconsistently — masked as a
        # per-scan failure by stage_one_scan's catch-all, or as a raw exception from
        # write_run_manifest — rather than one clean, immediate usage error).
        raise click.UsageError(
            f"--lock-staleness-seconds must be a finite positive number, "
            f"got {lock_staleness_seconds!r}."
        )

    try:
        scan_ids = (
            parse_scan_ids_flag(scan_ids_flag)
            if scan_ids_flag is not None
            else read_scan_ids(scan_ids_file)
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if not scan_ids:
        click.echo("No scan_ids given; nothing to stage.")
        return

    # Resolved once, before any authentication or staging, so an unusable run identity fails
    # fast (exit 1) instead of after a full batch — and so the file name and the id stamped
    # inside it come from the same single read of the environment.
    pipeline_run_id = pipeline_run_id_from_env()
    manifest_name = resolve_manifest_name(pipeline_run_id)

    from ..cli import _authed_client

    client = _authed_client(profile)

    result = BatchResult(
        [
            stage_one_scan(
                client, scan_id, out_dir, staleness_seconds=lock_staleness_seconds, workers=workers
            )
            for scan_id in scan_ids
        ]
    )

    if as_json:
        click.echo(format_json(result))
    else:
        click.echo(format_summary(result, verb="Staged", noun="scan", destination=str(out_dir)))

    write_run_manifest(
        out_dir,
        result,
        manifest_name=manifest_name,
        pipeline_run_id=pipeline_run_id,
        staleness_seconds=lock_staleness_seconds,
    )

    ctx.exit(0 if result.ok else 3)
