"""
Generate an MP4 from a cylinder scan and write it to S3, using the service's own
DB + S3 credentials (no per-user auth). Mirrors services/video-worker, but runs
synchronously from an HTTP request instead of a pg_notify job.

Flow: experiment_id -> scan_id (cyl_scans_extended) -> cyl_images -> download each
frame from S3 -> decimate -> encode H.264 with VideoWriter -> upload to S3 ->
return a presigned download URL.
"""

import io
import os
import tempfile

import boto3
import numpy as np
import psycopg2
from PIL import Image
from fastapi import HTTPException

from video_writer import VideoWriter

# Config from env (same contract as services/video-worker).
DATABASE_URL = os.environ.get("DATABASE_URL")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT")
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "bloom-storage")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

DECIMATE_FACTOR = 4
IMAGES_PREFIX = "storage-single-tenant/images"
VIDEO_KEY_TMPL = "cyl-videos/{scan_id}.mp4"
DOWNLOAD_URL_TTL = 86400  # 24h presigned URL


def _require_config():
    """Fail fast with a 500 if the service isn't configured for DB/S3 access."""
    missing = [
        name
        for name, val in [
            ("DATABASE_URL", DATABASE_URL),
            ("S3_ENDPOINT", S3_ENDPOINT),
            ("AWS_ACCESS_KEY_ID", AWS_ACCESS_KEY_ID),
            ("AWS_SECRET_ACCESS_KEY", AWS_SECRET_ACCESS_KEY),
        ]
        if not val
    ]
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"workflows service not configured: missing {', '.join(missing)}",
        )


def _db():
    return psycopg2.connect(DATABASE_URL)


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION,
    )


def scan_in_experiment(conn, experiment_id: int, scan_id: int) -> bool:
    """True if scan_id belongs to experiment_id (via cyl_scans_extended)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM cyl_scans_extended "
            "WHERE experiment_id = %s AND scan_id = %s LIMIT 1",
            (experiment_id, scan_id),
        )
        return cur.fetchone() is not None


def get_scan_images(conn, scan_id: int):
    """(object_path, frame_number) rows for a scan, ordered by frame_number."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ci.object_path, ci.frame_number "
            "FROM cyl_images ci JOIN cyl_scans cs ON ci.scan_id = cs.id "
            "WHERE cs.id = %s ORDER BY ci.frame_number",
            (scan_id,),
        )
        return cur.fetchall()


def generate_scan_video(scan_id: int, decimate: int = DECIMATE_FACTOR) -> dict:
    """Build the MP4 for `scan_id`, upload it to S3, return {frames, download_url}."""
    conn = _db()
    try:
        images = get_scan_images(conn, scan_id)
    finally:
        conn.close()
    if not images:
        raise HTTPException(
            status_code=404, detail=f"No images found for scan {scan_id}"
        )

    s3 = _s3()
    frames_written = 0
    with tempfile.TemporaryDirectory() as tmp_dir:
        video_path = os.path.join(tmp_dir, f"{scan_id}.mp4")
        writer = VideoWriter(filename=video_path)

        for object_path, _frame_number in images:
            try:
                prefix = f"{IMAGES_PREFIX}/{object_path}"
                resp = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=prefix)
                contents = resp.get("Contents", [])
                if len(contents) != 1:
                    continue
                obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=contents[0]["Key"])
                arr = np.array(Image.open(io.BytesIO(obj["Body"].read())))
                arr = arr[::decimate, ::decimate]
                if arr.size == 0:
                    continue
                writer.add(arr)
                frames_written += 1
            except Exception:
                # Skip unreadable/missing frames rather than fail the whole video.
                continue

        writer.close()
        if frames_written == 0:
            raise HTTPException(
                status_code=500, detail=f"No frames could be encoded for scan {scan_id}"
            )

        key = VIDEO_KEY_TMPL.format(scan_id=scan_id)
        s3.upload_file(
            Filename=video_path,
            Bucket=S3_BUCKET_NAME,
            Key=key,
            ExtraArgs={"ContentType": "video/mp4"},
        )

    download_url = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": S3_BUCKET_NAME, "Key": key},
        ExpiresIn=DOWNLOAD_URL_TTL,
    )
    return {"frames": frames_written, "download_url": download_url}


def generate_experiment_scan_video(experiment_id: int, scan_id: int) -> dict:
    """Validate the scan belongs to the experiment, then generate its video."""
    _require_config()
    conn = _db()
    try:
        belongs = scan_in_experiment(conn, experiment_id, scan_id)
    finally:
        conn.close()
    if not belongs:
        raise HTTPException(
            status_code=404,
            detail=f"Scan {scan_id} not found in experiment {experiment_id}",
        )
    result = generate_scan_video(scan_id)
    result["scan_id"] = scan_id
    return result
