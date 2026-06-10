#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "psycopg[binary]>=3.2",
#   "httpx>=0.27",
#   "python-dotenv>=1.0",
#   "imageio-ffmpeg>=0.5",
# ]
# ///
"""
render_plate_videos.py — backfill time-lapse MP4s for plate-scanner experiments.

Reads gravi_scans + gravi_images for each (experiment_id, plate_id) pair, pulls
the TIFFs out of the graviscan-images bucket, encodes a sorted-by-capture_date
MP4 with ffmpeg, uploads the result to the graviscan-videos bucket at
{experiment_id}/{plate_id}.mp4, and upserts a row in gravi_plate_videos so the
per-plate detail page can render it.

REQUIREMENTS
------------
- ffmpeg: either on PATH, or the bundled binary that ships with the
  `imageio-ffmpeg` pip package (the script falls back to that automatically,
  so no admin install is required).
- uv (or run with `python -m` and install the deps from the script header).

USAGE
-----
Set these env vars (or put them in a .env file next to this script):

    SUPABASE_URL=https://staging.bloom.salk.edu:8443/api
    SERVICE_ROLE_KEY=<staging service_role JWT>
    POSTGRES_DSN=postgres://supabase_admin:<pass>@host:port/postgres

Then:

    # all plates for one experiment
    uv run scripts/render_plate_videos.py --experiment 1

    # one specific plate
    uv run scripts/render_plate_videos.py --experiment 1 --plate PLATE_011

    # dry-run: list what would be done without writing anything
    uv run scripts/render_plate_videos.py --experiment 1 --dry-run

    # force re-render even if gravi_plate_videos already has a row
    uv run scripts/render_plate_videos.py --experiment 1 --force

DESIGN NOTES
------------
- Idempotent by default: skips a plate whose `gravi_plate_videos.frame_count`
  matches the current count of `gravi_images` rows for that plate.
- Conservative on errors: a failure on one plate logs and continues; the
  process exits non-zero if any plate failed so cron can detect it.
- TIFF → MP4: ffmpeg reads each TIFF as a frame; framerate is configurable
  via --framerate (default 4 fps).
- Auth: uses SERVICE_ROLE_KEY for both DB queries and storage I/O. Never
  bake this script into a request path; it's a back-office job.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import httpx
import psycopg
from dotenv import load_dotenv

logger = logging.getLogger("render_plate_videos")


# ─── Config ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Config:
    supabase_url: str
    service_role_key: str
    postgres_dsn: str
    source_bucket: str = "graviscan-images"
    target_bucket: str = "graviscan-videos"
    framerate: int = 4
    max_width: int = 720

    @classmethod
    def from_env(cls, framerate: int, max_width: int) -> "Config":
        missing = [
            k
            for k in ("SUPABASE_URL", "SERVICE_ROLE_KEY", "POSTGRES_DSN")
            if not os.getenv(k)
        ]
        if missing:
            raise SystemExit(
                f"Missing env var(s): {', '.join(missing)}. Set them or put them "
                "in a .env file next to this script."
            )
        return cls(
            supabase_url=os.environ["SUPABASE_URL"].rstrip("/"),
            service_role_key=os.environ["SERVICE_ROLE_KEY"],
            postgres_dsn=os.environ["POSTGRES_DSN"],
            framerate=framerate,
            max_width=max_width,
        )


# ─── DB helpers ──────────────────────────────────────────────────────────────


@dataclass
class PlateJob:
    experiment_id: int
    plate_id: str
    session_id: int | None
    frame_paths: list[str]  # object_paths from gravi_images, ordered by capture_date


def list_plate_jobs(
    conn: psycopg.Connection, experiment_id: int, plate_id: str | None
) -> list[PlateJob]:
    """Return one job per (experiment, plate) with the ordered list of frame paths."""
    sql = """
        SELECT
            s.experiment_id,
            s.plate_id,
            MAX(s.session_id)   AS session_id,
            ARRAY_AGG(i.object_path ORDER BY s.capture_date) AS frame_paths
        FROM gravi_scans s
        JOIN gravi_images i ON i.scan_id = s.id
        WHERE s.experiment_id = %s
          AND s.plate_id IS NOT NULL
          {plate_filter}
        GROUP BY s.experiment_id, s.plate_id
        HAVING COUNT(*) >= 1
        ORDER BY s.plate_id
    """
    if plate_id:
        sql = sql.format(plate_filter="AND s.plate_id = %s")
        params: tuple = (experiment_id, plate_id)
    else:
        sql = sql.format(plate_filter="")
        params = (experiment_id,)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        PlateJob(
            experiment_id=r[0], plate_id=r[1], session_id=r[2], frame_paths=list(r[3])
        )
        for r in rows
    ]


def existing_video_row(
    conn: psycopg.Connection, experiment_id: int, plate_id: str
) -> dict | None:
    sql = """
        SELECT id, object_path, frame_count
        FROM gravi_plate_videos
        WHERE experiment_id = %s AND plate_id = %s
        LIMIT 1
    """
    with conn.cursor() as cur:
        cur.execute(sql, (experiment_id, plate_id))
        row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "object_path": row[1], "frame_count": row[2]}


def upsert_video_row(
    conn: psycopg.Connection,
    *,
    experiment_id: int,
    plate_id: str,
    session_id: int | None,
    object_path: str,
    duration_seconds: int,
    frame_count: int,
    file_size_bytes: int,
) -> None:
    """Upsert a row in gravi_plate_videos keyed on (experiment, plate, session?)."""
    sql = """
        INSERT INTO gravi_plate_videos
          (experiment_id, plate_id, session_id, object_path,
           duration_seconds, frame_count, file_size_bytes, generated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (experiment_id, plate_id, COALESCE(session_id, -1)) DO UPDATE
          SET object_path     = EXCLUDED.object_path,
              duration_seconds = EXCLUDED.duration_seconds,
              frame_count     = EXCLUDED.frame_count,
              file_size_bytes = EXCLUDED.file_size_bytes,
              generated_at    = EXCLUDED.generated_at
    """
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                experiment_id,
                plate_id,
                session_id,
                object_path,
                duration_seconds,
                frame_count,
                file_size_bytes,
            ),
        )


# ─── Storage helpers ─────────────────────────────────────────────────────────


def storage_client(cfg: Config) -> httpx.Client:
    return httpx.Client(
        base_url=cfg.supabase_url,
        headers={
            "apikey": cfg.service_role_key,
            "Authorization": f"Bearer {cfg.service_role_key}",
        },
        timeout=60.0,
        verify=False,  # staging uses tls internal; in prod swap for verify=True
    )


def download_frame(
    client: httpx.Client, bucket: str, object_path: str, dest: Path
) -> None:
    """Pull a single object's raw bytes via the storage-api authenticated endpoint."""
    url = f"/storage/v1/object/{bucket}/{object_path}"
    with client.stream("GET", url) as r:
        r.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in r.iter_bytes():
                fh.write(chunk)


def upload_video(
    client: httpx.Client,
    bucket: str,
    object_path: str,
    source: Path,
    overwrite: bool,
) -> None:
    """Upload (or overwrite) an MP4 to the storage bucket."""
    url = f"/storage/v1/object/{bucket}/{object_path}"
    method = "PUT" if overwrite else "POST"
    headers = {"Content-Type": "video/mp4"}
    if overwrite:
        headers["x-upsert"] = "true"
    with source.open("rb") as fh:
        r = client.request(method, url, headers=headers, content=fh.read())
    if r.status_code >= 400:
        raise RuntimeError(
            f"Upload failed {r.status_code} for {object_path}: {r.text[:300]}"
        )


# ─── Encoding ────────────────────────────────────────────────────────────────


def encode_mp4(
    frames_dir: Path, out_path: Path, framerate: int, max_width: int
) -> None:
    """Run ffmpeg to encode all frames in frames_dir into a web-friendly MP4.

    Uses the static ffmpeg binary bundled with imageio-ffmpeg (no admin
    install required). Inputs are passed as a printf-style sequence
    (`frame_%04d.<ext>`) — the imageio-ffmpeg build doesn't support
    `-pattern_type glob`.
    """
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    frames = sorted(frames_dir.iterdir())
    if not frames:
        raise RuntimeError("encode_mp4: no frames found in {frames_dir}")
    ext = frames[0].suffix or ".bin"
    input_pattern = str(frames_dir / f"frame_%04d{ext}")

    # Preserve source aspect (plate scanner TIFFs are ≈5:7 portrait) but
    # cap the long edge so the resulting MP4 is web-sized — TIFFs are
    # 4960x6850 native, way too big for an in-page preview. Cap width at
    # max_width and let height float (-2 rounds to even, which libx264
    # + yuv420p require).
    cmd = [
        ffmpeg,
        "-y",
        "-framerate",
        str(framerate),
        "-i",
        input_pattern,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-vf",
        f"scale='min({max_width},iw)':-2",
        str(out_path),
    ]
    logger.debug("ffmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (rc={result.returncode}): {result.stderr[-500:]}"
        )


def estimate_duration_seconds(frame_count: int, framerate: int) -> int:
    """Duration is just frames / fps — no need to ffprobe."""
    if framerate <= 0:
        return 0
    return max(1, round(frame_count / framerate))


# ─── Orchestration ───────────────────────────────────────────────────────────


def render_one(
    cfg: Config,
    conn: psycopg.Connection,
    client: httpx.Client,
    job: PlateJob,
    force: bool,
    dry_run: bool,
) -> None:
    target_path = f"{job.experiment_id}/{job.plate_id}.mp4"

    existing = existing_video_row(conn, job.experiment_id, job.plate_id)
    if existing and existing["frame_count"] == len(job.frame_paths) and not force:
        logger.info(
            "  skip — gravi_plate_videos already at %d frames; use --force to redo",
            existing["frame_count"],
        )
        return
    if dry_run:
        logger.info(
            "  DRY-RUN would render %d frames → %s/%s",
            len(job.frame_paths),
            cfg.target_bucket,
            target_path,
        )
        return

    with tempfile.TemporaryDirectory(prefix="gravi-render-") as tmp:
        tmp_dir = Path(tmp)
        frames_dir = tmp_dir / "frames"
        frames_dir.mkdir()
        out_path = tmp_dir / "out.mp4"

        # 1. Download frames in order
        for idx, object_path in enumerate(job.frame_paths):
            suffix = Path(object_path).suffix or ".tif"
            local = frames_dir / f"frame_{idx:04d}{suffix}"
            logger.debug("  download %s → %s", object_path, local.name)
            download_frame(client, cfg.source_bucket, object_path, local)

        # 2. Encode MP4
        logger.info(
            "  encode %d frames @ %d fps → %s",
            len(job.frame_paths),
            cfg.framerate,
            out_path.name,
        )
        encode_mp4(frames_dir, out_path, cfg.framerate, cfg.max_width)

        # 3. Upload
        logger.info(
            "  upload → %s/%s (%.1f MiB)",
            cfg.target_bucket,
            target_path,
            out_path.stat().st_size / (1024 * 1024),
        )
        upload_video(
            client,
            cfg.target_bucket,
            target_path,
            out_path,
            overwrite=bool(existing),
        )

        # 4. Upsert DB row
        upsert_video_row(
            conn,
            experiment_id=job.experiment_id,
            plate_id=job.plate_id,
            session_id=job.session_id,
            object_path=target_path,
            duration_seconds=estimate_duration_seconds(
                len(job.frame_paths), cfg.framerate
            ),
            frame_count=len(job.frame_paths),
            file_size_bytes=out_path.stat().st_size,
        )
        conn.commit()
        logger.info("  ✓ committed gravi_plate_videos row")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--experiment", type=int, required=True, help="gravi_experiments.id")
    ap.add_argument("--plate", type=str, default=None, help="optional plate_id filter")
    ap.add_argument("--framerate", type=int, default=4, help="output fps (default 4)")
    ap.add_argument(
        "--max-width",
        type=int,
        default=720,
        help="cap output width in px; height scales to preserve aspect (default 720)",
    )
    ap.add_argument(
        "--force", action="store_true", help="re-render even if row already matches"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="list jobs without writing"
    )
    ap.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).with_name(".env.render-plate-videos"),
        help="env file with SUPABASE_URL / SERVICE_ROLE_KEY / POSTGRES_DSN",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.env_file.exists():
        load_dotenv(args.env_file)

    # Pre-flight: confirm the bundled ffmpeg is available.
    try:
        import imageio_ffmpeg  # noqa: F401
    except ImportError:
        logger.error("imageio-ffmpeg not installed (`pip install imageio-ffmpeg`)")
        return 2

    cfg = Config.from_env(framerate=args.framerate, max_width=args.max_width)

    failures: list[str] = []
    with psycopg.connect(cfg.postgres_dsn) as conn, storage_client(cfg) as client:
        jobs = list_plate_jobs(conn, args.experiment, args.plate)
        logger.info("Found %d plate(s) to consider for experiment %d", len(jobs), args.experiment)
        for job in jobs:
            logger.info(
                "[%s] %d frames%s",
                job.plate_id,
                len(job.frame_paths),
                " (DRY-RUN)" if args.dry_run else "",
            )
            try:
                render_one(cfg, conn, client, job, force=args.force, dry_run=args.dry_run)
            except Exception as e:  # noqa: BLE001 — keep going on a single-plate failure
                logger.error("[%s] FAILED: %s", job.plate_id, e)
                conn.rollback()
                failures.append(job.plate_id)

    if failures:
        logger.error("%d plate(s) failed: %s", len(failures), ", ".join(failures))
        return 1
    logger.info("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
