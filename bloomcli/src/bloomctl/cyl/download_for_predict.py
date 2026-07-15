"""`bloomctl cyl download-for-predict`: stage one scan in the layout
`sleap_roots_predict.discover_scans` expects.

Pure helpers (sidecar assembly, path derivation, checksum) are separated from
the supabase/storage I/O so the contract is unit-testable without a live
server — mirroring ``download.py`` and ``ingest.py``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import click

from ..credentials import DEFAULT_PROFILE
from .download import DownloadResult, FrameResult, fetch_images, fetch_scan

# Matches sleap_roots_predict.batch._IMAGE_EXTENSIONS — the exact set discover_scans
# globs for, so stray-frame reconciliation removes anything predict would pick up.
_IMAGE_EXTENSIONS = frozenset({".png", ".tif", ".tiff", ".jpg", ".jpeg"})


def scan_key_for(scan_id: Any) -> str:
    """The sidecar's scan_key — must equal the filename stem (predict validates this)."""
    return f"scan_{scan_id}"


def frame_dest_for_predict(scan_dir: Path, image: dict[str, Any]) -> Path:
    """Absolute destination for one frame, co-located with the sidecar."""
    ext = Path(image.get("object_path", "")).suffix or ".png"
    return Path(scan_dir) / f"{image['frame_number']}{ext}"


def compute_checksum(frame_bytes_list: list[bytes]) -> str:
    """sha256 over frame bytes concatenated in the given (DB frame_number) order."""
    digest = hashlib.sha256()
    for data in frame_bytes_list:
        digest.update(data)
    return f"sha256:{digest.hexdigest()}"


def build_sidecar(
    scan: dict[str, Any], images: list[dict[str, Any]], frame_bytes_list: list[bytes]
) -> dict[str, Any]:
    """Assemble the scan_metadata.json sidecar dict for one scan.

    `mode` is forced via `resolve_params`'s documented `overrides` mechanism (not
    row augmentation — see design.md) since every `cyl` scan is cylinder-scanner.
    """
    from sleap_roots_contracts import resolve_params

    resolved = resolve_params(scan, overrides={"mode": "cylinder"})
    return {
        "scan_key": scan_key_for(scan["scan_id"]),
        "params": resolved.values,
        "image_ids": [image["id"] for image in images],
        "images_checksum": compute_checksum(frame_bytes_list),
    }


def write_sidecar(sidecar: dict[str, Any], path: Path) -> None:
    """Write the sidecar as valid UTF-8 JSON, creating the parent dir if absent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sidecar), encoding="utf-8")


def reconcile_stray_frames(scan_dir: Path, written: list[Path]) -> None:
    """Delete any image-extension file in scan_dir not among the just-written frames.

    Guards against a stale frame surviving from an earlier failed attempt whose
    cyl_images rows have since changed (see design.md): discover_scans globs every
    image-extension file physically present in the sidecar's directory, not just
    the ones named in image_ids, so a leftover file would silently be fed to
    predict as an unaccounted-for frame.
    """
    if not scan_dir.exists():
        return
    written_names = {p.name for p in written}
    for entry in scan_dir.iterdir():
        if (
            entry.is_file()
            and entry.suffix.lower() in _IMAGE_EXTENSIONS
            and entry.name not in written_names
        ):
            entry.unlink()


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
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
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
    from .. import auth
    from ..credentials import load_credentials

    try:
        creds = load_credentials(profile)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(f"{exc} — run `bloomctl login`.") from exc
    try:
        client = auth.make_authed_client(creds)
    except auth.AuthError as exc:
        raise click.ClickException(str(exc)) from exc

    scan = fetch_scan(client, scan_id)
    if scan is None:
        raise click.ClickException(f"Scan {scan_id} not found.")

    images = fetch_images(client, scan_id)
    if not images:
        raise click.ClickException(f"No frames found for scan {scan_id}.")

    scan_dir = Path(out_dir) / scan_key_for(scan_id)
    result, frame_bytes = download_frames_for_predict(client, scan, images, scan_dir)

    if result.failed:
        raise click.ClickException(
            f"{result.failed} of {result.total} frames failed to download — "
            f"successfully downloaded frames remain in {scan_dir}; no sidecar written."
        )

    written = [frame_dest_for_predict(scan_dir, image) for image in images]
    reconcile_stray_frames(scan_dir, written)

    sidecar = build_sidecar(scan, images, frame_bytes)
    sidecar_path = scan_dir / f"{scan_key_for(scan_id)}.scan_metadata.json"
    write_sidecar(sidecar, sidecar_path)
    click.echo(f"Staged {result.ok}/{result.total} frames -> {scan_dir}  (sidecar: {sidecar_path})")
