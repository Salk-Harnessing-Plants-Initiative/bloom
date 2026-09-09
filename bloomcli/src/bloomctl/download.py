"""Cylinder experiment download: metadata (scans.csv) + per-frame images.

Pure helpers (column mapping, paths) are separated from the supabase/storage I/O
so the contract is unit-testable without a live server.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def download_images(client: Any, scans: list[dict[str, Any]], out_dir: Path) -> DownloadResult:
    """Download every frame for every scan from Storage bucket `images`.

    Each frame is downloaded independently: a failure is recorded, not raised, so
    one bad frame (missing object, transient 5xx) can't abort the whole run.
    Signs server-side via Supabase Storage (no MinIO secrets, no legacy Lambda).
    """
    bucket = client.storage.from_("images")
    frames: list[FrameResult] = []
    for scan in scans:
        for image in fetch_images(client, scan["scan_id"]):
            object_path = image.get("object_path", "")
            result = FrameResult(scan.get("scan_id"), image.get("frame_number"), object_path, ok=False)
            try:
                data = bucket.download(object_path)
                if data is None:
                    raise ValueError("empty response from storage")
                dest = image_dest(out_dir, scan, image)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                result.ok = True
            except Exception as exc:  # per-frame: record and continue
                result.error = str(exc)
            frames.append(result)
    return DownloadResult(frames)


def write_download_log(result: DownloadResult, path: Path) -> None:
    """Write a per-frame download log (one line per frame) with a summary footer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for f in result.frames:
        line = f"{'OK  ' if f.ok else 'FAIL'} scan={f.scan_id} frame={f.frame_number} {f.object_path}"
        if not f.ok:
            line += f"  error={f.error}"
        lines.append(line)
    lines.append(f"\nSummary: {result.ok} downloaded, {result.failed} failed, {result.total} total")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
