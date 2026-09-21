"""`bloomctl cyl create-test-scan`: create one synthetic cylinder scan in the staging test
experiment A4-PIPELINE-E2E-TEST (experiment_id 12880747) — a committed replacement for the
throwaway scripts this exact tooling had been rewritten from scratch three times in two days
(see design.md).

Pure helpers (guard check, QR-suffix resolution, frame discovery, RPC/storage calls) are kept
separate from the `@click.command`, matching this codebase's "unit-testable without a live
server" convention (`cyl/download.py`'s module docstring). This command is hardcoded to
experiment 12880747 — it accepts no experiment override, by design (design.md's Non-Goals).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import click

from .. import credentials
from .._storage import upload_object
from ..credentials import DEFAULT_PROFILE
from ._locks import DEFAULT_LOCK_STALENESS_SECONDS, LockContendedError, acquire_lock

EXPERIMENT_ID = 12880747
EXPERIMENT_NAME_PREFIX = "A4-PIPELINE-E2E-TEST"
IMAGES_BUCKET = "images"
QR_PREFIX = "TEST-E2E-"
QR_SUFFIX_WIDTH = 3
MIN_FRAME_SIZE_BYTES = 1024
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}

_QR_SUFFIX_RE = re.compile(rf"^{re.escape(QR_PREFIX)}(\d+)$")

# Sentinel identity values (design.md): phenotypers/scientists/accessions are upserted by
# insert_image_v2_0 on a *global* natural key with no experiment scoping, so these are fixed,
# dedicated synthetic values, never copied forward from an existing scan.
PHENOTYPER_NAME = "Synthetic Test Phenotyper"
PHENOTYPER_EMAIL = "synthetic-test-phenotyper@bloom.invalid"
SCIENTIST_NAME = "Synthetic Test Scientist"
SCIENTIST_EMAIL = "synthetic-test-scientist@bloom.invalid"
ACCESSION_NAME = "SYNTHETIC-TEST-ACCESSION"

# Wave/plant-batch metadata, including device_name, sourced from an existing TEST-E2E-* scan
# (task 1.1) — device_name is NOT a sentinel: insert_image_v2_0 requires it to already exist in
# cyl_scanners (RAISE EXCEPTION otherwise), so a made-up value would break every invocation.
SPECIES_COMMON_NAME = "Canola"
WAVE_NUMBER = 9999
GERM_DAY = 1
GERM_DAY_COLOR = "TestGray"
PLANT_AGE_DAYS = 2
DATE_SCANNED = "2026-08-24"
DEVICE_NAME = "FastScanner"


class CreateTestScanError(Exception):
    """Raised for any failure this command should report as a readable, non-zero exit."""


def default_lock_path() -> Path:
    return credentials.default_config_dir() / ".locks" / f"cyl-create-test-scan-{EXPERIMENT_ID}.lock"


def check_experiment_guard(client: Any) -> None:
    """Raise :class:`CreateTestScanError` unless experiment 12880747 exists with the expected name."""
    rows = client.table("cyl_experiments").select("id, name").eq("id", EXPERIMENT_ID).execute().data or []
    if not rows:
        raise CreateTestScanError(
            f"experiment {EXPERIMENT_ID} does not exist on this server — refusing to proceed "
            "(wrong profile, or the test experiment was renamed/deleted)"
        )
    name = rows[0].get("name") or ""
    if not name.startswith(EXPERIMENT_NAME_PREFIX):
        raise CreateTestScanError(
            f"experiment {EXPERIMENT_ID} is named {name!r}, which does not start with "
            f"{EXPERIMENT_NAME_PREFIX!r} — refusing to proceed (wrong profile, or the test "
            "experiment was renamed)"
        )


def resolve_next_qr_code(client: Any) -> str:
    """Next `TEST-E2E-NNN` suffix after the highest one currently used in experiment 12880747."""
    rows = (
        client.table("cyl_plants_extended")
        .select("qr_code")
        .eq("experiment_id", EXPERIMENT_ID)
        .execute()
        .data
        or []
    )
    highest = 0
    for row in rows:
        match = _QR_SUFFIX_RE.match(row.get("qr_code") or "")
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{QR_PREFIX}{highest + 1:0{QR_SUFFIX_WIDTH}d}"


def discover_frame_files(frames_dir: Path) -> list[Path]:
    """Image files directly under `frames_dir`, sorted by filename ascending.

    Raises :class:`CreateTestScanError` if the directory doesn't exist or holds no image files.
    """
    frames_dir = Path(frames_dir)
    if not frames_dir.is_dir():
        raise CreateTestScanError(f"--frames-dir does not exist or is not a directory: {frames_dir}")
    files = sorted(
        p for p in frames_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not files:
        raise CreateTestScanError(
            f"--frames-dir {frames_dir} contains no image files "
            f"({', '.join(sorted(IMAGE_EXTENSIONS))})"
        )
    return files


def call_insert_image(client: Any, *, plant_qr_code: str, frame_number: int) -> int | None:
    """Call `insert_image_v2_0`; returns the new/existing `cyl_images.id`, or None if already SUCCESS."""
    params = {
        "species_common_name": SPECIES_COMMON_NAME,
        "experiment": EXPERIMENT_NAME_PREFIX,
        "wave_number": WAVE_NUMBER,
        "germ_day": GERM_DAY,
        "germ_day_color": GERM_DAY_COLOR,
        "plant_age_days": PLANT_AGE_DAYS,
        "date_scanned_": DATE_SCANNED,
        "device_name": DEVICE_NAME,
        "plant_qr_code": plant_qr_code,
        "accession_name": ACCESSION_NAME,
        "frame_number_": frame_number,
        "phenotyper_name": PHENOTYPER_NAME,
        "phenotyper_email": PHENOTYPER_EMAIL,
        "scientist_name": SCIENTIST_NAME,
        "scientist_email": SCIENTIST_EMAIL,
    }
    return client.rpc("insert_image_v2_0", params).execute().data


def resolve_scan_id(client: Any, image_id: int) -> int:
    row = client.table("cyl_images").select("scan_id").eq("id", image_id).single().execute().data
    return row["scan_id"]


def count_frames_for_scan(client: Any, scan_id: int) -> int:
    resp = client.table("cyl_images").select("id", count="exact").eq("scan_id", scan_id).execute()
    return resp.count


def build_object_path(image_id: int) -> str:
    return f"cyl-images/cyl-image_{image_id}_{uuid4()}.png"


def update_image_row(client: Any, image_id: int, object_path: str) -> None:
    client.table("cyl_images").update({"object_path": object_path, "status": "SUCCESS"}).eq(
        "id", image_id
    ).execute()


def create_test_scan_core(
    client: Any, *, poison: bool, frames_dir: Path | None
) -> dict[str, Any]:
    """Create one scan (poison or good). Guard runs unlocked (read-only); everything from
    QR-suffix resolution through the last frame's row update runs inside one lock acquisition.
    """
    check_experiment_guard(client)

    with acquire_lock(default_lock_path(), staleness_seconds=DEFAULT_LOCK_STALENESS_SECONDS):
        qr_code = resolve_next_qr_code(client)

        if poison:
            image_id = call_insert_image(client, plant_qr_code=qr_code, frame_number=1)
            if image_id is None:
                raise CreateTestScanError(
                    f"insert_image_v2_0 returned NULL for {qr_code} frame 1 — the resolved "
                    "cyl_images row is already SUCCESS (a QR-suffix bug, or a race the lock "
                    "failed to prevent)"
                )
            return {"plant_qr_code": qr_code, "mode": "poison", "cyl_images_ids": [image_id]}

        frame_paths = discover_frame_files(frames_dir)
        image_ids: list[int] = []
        for index, frame_path in enumerate(frame_paths, start=1):
            size = frame_path.stat().st_size
            if size < MIN_FRAME_SIZE_BYTES:
                raise CreateTestScanError(
                    f"frame file {frame_path} is {size} bytes, below the "
                    f"{MIN_FRAME_SIZE_BYTES}-byte (1 KiB) size floor — this looks like a "
                    "blank/placeholder image, not real root imagery; copy frames from a real "
                    "prior scan's images instead (e.g. `bloomctl cyl download` against scan "
                    "12894745 / TEST-E2E-001)"
                )

            image_id = call_insert_image(client, plant_qr_code=qr_code, frame_number=index)
            if image_id is None:
                raise CreateTestScanError(
                    f"insert_image_v2_0 returned NULL for {qr_code} frame {index} — the "
                    "resolved cyl_images row is already SUCCESS (a QR-suffix bug, or a race "
                    "the lock failed to prevent)"
                )

            scan_id = resolve_scan_id(client, image_id)
            actual_count = count_frames_for_scan(client, scan_id)
            if actual_count != index:
                raise CreateTestScanError(
                    f"frame-count mismatch for scan {scan_id} (qr_code {qr_code}): expected "
                    f"{index} frame(s) processed so far, found {actual_count} — a concurrent "
                    "writer may have touched this scan despite the lock; aborting before upload"
                )

            object_path = build_object_path(image_id)
            data = frame_path.read_bytes()
            try:
                upload_object(client, data, object_path, bucket=IMAGES_BUCKET)
            except Exception as exc:
                raise CreateTestScanError(
                    f"upload failed for frame {index} ({frame_path}, cyl_images id={image_id}): {exc}"
                ) from exc
            try:
                update_image_row(client, image_id, object_path)
            except Exception as exc:
                raise CreateTestScanError(
                    f"frame {index} ({frame_path}) uploaded to {object_path} but updating "
                    f"cyl_images id={image_id} failed: {exc} — the object is now orphaned and "
                    "the row is still PENDING"
                ) from exc

            image_ids.append(image_id)

        return {"plant_qr_code": qr_code, "mode": "good", "cyl_images_ids": image_ids}


@click.command(name="create-test-scan")
@click.option(
    "--poison",
    is_flag=True,
    help=(
        "Create a scan that will fail to download: one frame, insert_image_v2_0 only, "
        "object_path left NULL."
    ),
)
@click.option(
    "--good",
    is_flag=True,
    help="Create a fully downloadable scan from real imagery. Requires --frames-dir.",
)
@click.option(
    "--frames-dir",
    "frames_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Directory of real frame images (>= 1 KiB each) to upload, one per file, in ascending "
        "filename order. Copy frames from a known-good prior scan first — e.g. "
        "`bloomctl cyl download` against scan 12894745 / TEST-E2E-001 — never blank/synthetic "
        "images: those will not exercise a real recompute. Valid only with --good."
    ),
)
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help=(
        "Credentials profile to use (must have write access — e.g. staging-writer; "
        "pipeline-staging lacks the grants this command needs)."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the created scan as a JSON object on stdout.",
)
def create_test_scan(
    poison: bool, good: bool, frames_dir: Path | None, profile: str, as_json: bool
) -> None:
    """Create one synthetic cylinder scan in the staging test experiment A4-PIPELINE-E2E-TEST
    (experiment_id 12880747) — never any other experiment. One scan per invocation; run
    multiple times for multiple scans."""
    if poison and good:
        raise click.ClickException("--poison and --good are mutually exclusive")
    if not poison and not good:
        raise click.ClickException("exactly one of --poison or --good is required")
    if frames_dir is not None and not good:
        raise click.ClickException("--frames-dir is only valid with --good")
    if good and frames_dir is None:
        raise click.ClickException("--good requires --frames-dir")

    from ..cli import _authed_client

    client = _authed_client(profile)

    try:
        result = create_test_scan_core(client, poison=poison, frames_dir=frames_dir)
    except (LockContendedError, CreateTestScanError) as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(result))
    else:
        click.echo(
            f"Created scan {result['plant_qr_code']} (cyl_images id(s): {result['cyl_images_ids']})"
        )
