"""`bloomctl cyl download-for-predict`: stage one scan in the layout
`sleap_roots_predict.discover_scans` expects.

Pure helpers (sidecar assembly, path derivation, checksum) are separated from
the supabase/storage I/O so the contract is unit-testable without a live
server — mirroring ``download.py`` and ``ingest.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import click

from ..credentials import DEFAULT_PROFILE
from .download import DownloadResult, FrameResult, fetch_images, fetch_scan

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
    """
    return {
        "scan_key": scan_key_for(scan["scan_id"]),
        "params": params,
        "image_ids": [image["id"] for image in images],
        "images_checksum": compute_checksum(frame_bytes_list),
    }


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes so a crash mid-write can never leave a truncated file at `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


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


# --- supabase / storage I/O -------------------------------------------------


def download_frames_for_predict(
    client: Any, scan: dict[str, Any], images: list[dict[str, Any]], scan_dir: Path
) -> tuple[DownloadResult, list[bytes]]:
    """Download every frame for one scan into the nested predict layout.

    Returns the aggregate result plus each successfully-downloaded frame's bytes
    in DB frame_number order (for the checksum) — a failed frame contributes no
    bytes entry, mirroring ``download.py``'s per-frame failure isolation.
    """
    bucket = client.storage.from_("images")
    frames: list[FrameResult] = []
    frame_bytes: list[bytes] = []
    for image in images:
        object_path = image.get("object_path", "")
        result = FrameResult(scan.get("scan_id"), image.get("frame_number"), object_path, ok=False)
        try:
            data = bucket.download(object_path)
            if data is None:
                raise ValueError("empty response from storage")
            dest = frame_dest_for_predict(scan_dir, image)
            _atomic_write_bytes(dest, data)
            result.ok = True
            frame_bytes.append(data)
        except Exception as exc:  # per-frame: record and continue
            result.error = str(exc)
        frames.append(result)
    return DownloadResult(frames), frame_bytes


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
def download_for_predict(scan_id: int, out_dir: Path, profile: str) -> None:
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

    result, frame_bytes = download_frames_for_predict(client, scan, images, scan_dir)

    if result.failed:
        raise click.ClickException(
            f"{result.failed} of {result.total} frames failed to download — "
            f"frames downloaded this run remain in {scan_dir}; no sidecar written."
        )

    sidecar = build_sidecar(scan, images, frame_bytes, params)
    sidecar_path = scan_dir / f"{scan_key_for(scan_id)}.scan_metadata.json"
    write_sidecar(sidecar, sidecar_path)
    click.echo(f"Staged {result.ok}/{result.total} frames -> {scan_dir}  (sidecar: {sidecar_path})")
