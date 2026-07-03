"""
Generate an MP4 from a cylinder scan and write it to Supabase Storage, using a
dedicated least-privilege app user (see supabase_client.py). All DB and storage
access goes through the authenticated Supabase client, so the app user's grants
and storage policies bound what this can touch.

Flow: validate scan ∈ experiment (cyl_scans_extended) -> read scan images
(cyl_images) -> download each frame from the images bucket -> decimate -> encode
H.264 with VideoWriter -> upload MP4 to the videos bucket -> signed URL ->
(optional) insert a record row.
"""

import io
import os
import tempfile

import numpy as np
from PIL import Image
from fastapi import HTTPException

from supabase_client import app_client
from video_writer import VideoWriter

DECIMATE_FACTOR = 4
# Hard cap on frames for the synchronous route (a cyl scan is ~72). Guards
# against a huge scan blowing the request timeout; revisit when we measure the
# real max a sync request can handle.
MAX_IMAGES = 72
DOWNLOAD_URL_TTL = 86400  # 24h signed URL

# Storage buckets + optional record table — configurable to match the Supabase
# setup the app user has access to.
IMAGES_BUCKET = os.environ.get("WORKFLOWS_IMAGES_BUCKET", "images")
VIDEOS_BUCKET = os.environ.get("WORKFLOWS_VIDEOS_BUCKET", "videos")
# Plain record table (NOT video_jobs — that would re-trigger the async worker).
# Unset -> skip the record and only write the bucket.
VIDEO_TABLE = os.environ.get("WORKFLOWS_VIDEO_TABLE") or None


def scan_in_experiment(client, experiment_id: int, scan_id: int) -> bool:
    """True if scan_id belongs to experiment_id (via cyl_scans_extended)."""
    rows = (
        client.table("cyl_scans_extended")
        .select("scan_id")
        .eq("experiment_id", experiment_id)
        .eq("scan_id", scan_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return bool(rows)


def get_scan_images(client, scan_id: int) -> list[dict]:
    """A scan's images (object_path, frame_number), ordered, capped at MAX_IMAGES."""
    return (
        client.table("cyl_images")
        .select("object_path, frame_number")
        .eq("scan_id", scan_id)
        .order("frame_number")
        .limit(MAX_IMAGES)
        .execute()
        .data
        or []
    )


def _signed_url(bucket, path: str) -> str:
    """Best-effort extraction of the signed URL across supabase-py versions."""
    res = bucket.create_signed_url(path, DOWNLOAD_URL_TTL)
    if isinstance(res, dict):
        return res.get("signedURL") or res.get("signed_url") or res.get("signedUrl")
    return res


def generate_scan_video(client, scan_id: int, decimate: int = DECIMATE_FACTOR) -> dict:
    """Build the scan's MP4, upload to the videos bucket, return {frames, download_url}."""
    images = get_scan_images(client, scan_id)
    if not images:
        raise HTTPException(
            status_code=404, detail=f"No images found for scan {scan_id}"
        )

    img_bucket = client.storage.from_(IMAGES_BUCKET)
    frames_written = 0
    with tempfile.TemporaryDirectory() as tmp_dir:
        video_path = os.path.join(tmp_dir, f"{scan_id}.mp4")
        writer = VideoWriter(filename=video_path)

        for image in images:
            object_path = image.get("object_path")
            if not object_path:
                continue
            try:
                data = img_bucket.download(object_path)
                if not data:
                    continue
                arr = np.array(Image.open(io.BytesIO(data)))
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

        with open(video_path, "rb") as fh:
            video_bytes = fh.read()

    key = f"{scan_id}.mp4"
    vids = client.storage.from_(VIDEOS_BUCKET)
    vids.upload(key, video_bytes, {"content-type": "video/mp4", "upsert": "true"})
    return {"frames": frames_written, "download_url": _signed_url(vids, key)}


def _record_video(client, experiment_id: int, scan_id: int, result: dict):
    """Insert a record of the generated video (only if WORKFLOWS_VIDEO_TABLE is set)."""
    if not VIDEO_TABLE:
        return
    try:
        client.table(VIDEO_TABLE).insert(
            {
                "experiment_id": experiment_id,
                "scan_id": scan_id,
                "frames": result["frames"],
                "download_url": result["download_url"],
            }
        ).execute()
    except Exception:
        # A failed record write shouldn't lose the already-generated video.
        pass


def generate_experiment_scan_video(experiment_id: int, scan_id: int) -> dict:
    """Validate the scan belongs to the experiment, then generate its video."""
    client = app_client()
    if not scan_in_experiment(client, experiment_id, scan_id):
        raise HTTPException(
            status_code=404,
            detail=f"Scan {scan_id} not found in experiment {experiment_id}",
        )
    result = generate_scan_video(client, scan_id)
    result["scan_id"] = scan_id
    _record_video(client, experiment_id, scan_id, result)
    return result
