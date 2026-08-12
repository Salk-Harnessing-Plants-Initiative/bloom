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
import logging
import os
import tempfile

import numpy as np
from PIL import Image
from fastapi import HTTPException

from supabase_client import app_client
from video_writer import VideoWriter, VideoEncodeError

logger = logging.getLogger(__name__)

DECIMATE_FACTOR = 4
# Hard cap on frames for the synchronous route (a cyl scan is ~72). Guards
# against a huge scan blowing the request timeout; revisit when we measure the
# real max a sync request can handle.
MAX_IMAGES = 72
DOWNLOAD_URL_TTL = 3600  # 1h signed URL, matching the app-wide convention

# Storage buckets + optional record table — configurable to match the Supabase
# setup the app user has access to.
IMAGES_BUCKET = os.environ.get("WORKFLOWS_IMAGES_BUCKET", "images")
VIDEOS_BUCKET = os.environ.get("WORKFLOWS_VIDEOS_BUCKET", "videos")
# Path within the videos bucket — must match what Bloom web plays
# (web/components/plant-scan.tsx -> videos/cyl-videos/{scan_id}.mp4).
VIDEO_PATH_PREFIX = "cyl-videos"
# Record table linking scan_id -> stored video path (upserted per scan).
VIDEO_TABLE = os.environ.get("WORKFLOWS_VIDEO_TABLE", "cyl_scan_videos")

# Signed URLs come back pointing at the internal gateway (SUPABASE_URL, e.g.
# http://kong:8000), which an outside caller can't reach. Rewrite that host to the
# public base so the returned download_url is usable externally — mirrors
# web/lib/supabase/storage-url.ts.
SUPABASE_URL = os.environ.get("SUPABASE_URL")
PUBLIC_SUPABASE_URL = os.environ.get("WORKFLOWS_PUBLIC_SUPABASE_URL")


def _to_public_url(url: str) -> str:
    """Swap the internal Supabase host for the public base; no-op if either is
    unset or the URL isn't on the internal host."""
    if not url or not PUBLIC_SUPABASE_URL or not SUPABASE_URL:
        return url
    internal = SUPABASE_URL.rstrip("/")
    if url.startswith(internal):
        return PUBLIC_SUPABASE_URL.rstrip("/") + url[len(internal) :]
    return url


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


def get_scan_images(client, scan_id: int, limit: int = MAX_IMAGES) -> list[dict]:
    """A scan's images (object_path, frame_number), ordered, capped at `limit`."""
    return (
        client.table("cyl_images")
        .select("object_path, frame_number")
        .eq("scan_id", scan_id)
        .order("frame_number")
        .limit(limit)
        .execute()
        .data
        or []
    )


def _recorded_frames(client, scan_id: int):
    """Frame count of the video currently recorded for this scan, or None."""
    if not VIDEO_TABLE:
        return None
    try:
        rows = (
            client.table(VIDEO_TABLE)
            .select("frames")
            .eq("scan_id", scan_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        return None
    return rows[0].get("frames") if rows else None


def _signed_url(bucket, path: str) -> str:
    """Best-effort extraction of the signed URL across supabase-py versions,
    rewritten to the public host so external callers can open it."""
    res = bucket.create_signed_url(path, DOWNLOAD_URL_TTL)
    if isinstance(res, dict):
        url = res.get("signedURL") or res.get("signed_url") or res.get("signedUrl")
    else:
        url = res
    return _to_public_url(url)


def generate_scan_video(client, scan_id: int, decimate: int = DECIMATE_FACTOR) -> dict:
    """Build the scan's MP4, upload to the videos bucket, return {frames, download_url}."""
    # Fetch one past the cap so we can tell a truncated (>MAX_IMAGES) scan apart
    # from one that is exactly at the cap.
    images = get_scan_images(client, scan_id, MAX_IMAGES + 1)
    if not images:
        raise HTTPException(
            status_code=404, detail=f"No images found for scan {scan_id}"
        )

    truncated = len(images) > MAX_IMAGES
    if truncated:
        images = images[:MAX_IMAGES]
        logger.warning(
            "scan %s: has more than %s images; encoding the first %s "
            "(higher frame_numbers dropped)",
            scan_id,
            MAX_IMAGES,
            MAX_IMAGES,
        )
    frames_expected = len(images)

    img_bucket = client.storage.from_(IMAGES_BUCKET)
    frames_written = 0
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Constant name in the unique temp dir — keeps user-derived scan_id out
        # of the local filesystem path and the ffmpeg command line.
        video_path = os.path.join(tmp_dir, "scan.mp4")
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
            except Exception as exc:
                # Skip an unreadable/missing frame rather than fail the whole
                # video — but log it so a half-missing scan isn't silent.
                logger.warning(
                    "scan %s: skipping frame %s: %s", scan_id, object_path, exc
                )
                continue

        if frames_written == 0:
            raise HTTPException(
                status_code=500, detail=f"No frames could be encoded for scan {scan_id}"
            )

        # A non-zero/stuck ffmpeg raises here — so a truncated MP4 is never
        # uploaded to the canonical path the web trusts. Log the real reason;
        # keep the HTTP detail generic (ffmpeg stderr can leak internal paths).
        try:
            writer.close()
        except VideoEncodeError as exc:
            logger.warning("scan %s: video encode failed: %s", scan_id, exc)
            raise HTTPException(
                status_code=500, detail=f"Video encoding failed for scan {scan_id}"
            ) from exc

        if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            raise HTTPException(
                status_code=500, detail=f"Encoded video for scan {scan_id} is empty"
            )

        with open(video_path, "rb") as fh:
            video_bytes = fh.read()

    if frames_written < frames_expected:
        logger.warning(
            "scan %s: encoded %s of %s frames (%s skipped)",
            scan_id,
            frames_written,
            frames_expected,
            frames_expected - frames_written,
        )

    key = f"{VIDEO_PATH_PREFIX}/{scan_id}.mp4"
    vids = client.storage.from_(VIDEOS_BUCKET)

    # Don't let a re-run degrade the canonical asset: if a video with more frames
    # is already recorded, keep it instead of overwriting with a worse one.
    prior_frames = _recorded_frames(client, scan_id)
    if prior_frames is not None and frames_written < prior_frames:
        logger.warning(
            "scan %s: new encode has %s frames < recorded %s; keeping the existing video",
            scan_id,
            frames_written,
            prior_frames,
        )
        return _result(
            scan_id,
            vids,
            key,
            prior_frames,
            frames_expected,
            truncated,
            regenerated=False,
        )

    vids.upload(key, video_bytes, {"content-type": "video/mp4", "upsert": "true"})
    return _result(
        scan_id, vids, key, frames_written, frames_expected, truncated, regenerated=True
    )


def _result(
    scan_id, vids, key, frames, frames_expected, truncated, regenerated
) -> dict:
    """Build the response, failing (not returning null) if no URL can be signed."""
    download_url = _signed_url(vids, key)
    if not download_url:
        # A response without a usable URL is a failure, not a success.
        raise HTTPException(
            status_code=500,
            detail=f"Could not create a download URL for scan {scan_id}",
        )
    return {
        "frames": frames,
        "frames_expected": frames_expected,
        "truncated": truncated,
        "regenerated": regenerated,
        "path": key,
        "download_url": download_url,
    }


def _record_video(client, scan_id: int, result: dict):
    """Upsert the scan -> video-path record (best-effort; one row per scan)."""
    if not VIDEO_TABLE:
        return
    try:
        # Through the wrapper, not a client upsert: PostgREST builds its DO UPDATE from every
        # payload key, so an upsert writes scan_id, which bloom_workflows may not update.
        client.rpc(
            "record_cyl_scan_video",
            {
                "p_scan_id": scan_id,
                "p_path": result["path"],
                "p_frames": result.get("frames"),
            },
        ).execute()
    except Exception as exc:
        # A failed record write shouldn't lose the already-generated video, but
        # log it — a stored video with no DB row is a divergence worth seeing.
        logger.warning(
            "scan %s: video stored at %s but recording the row failed: %s",
            scan_id,
            result.get("path"),
            exc,
        )


def generate_experiment_scan_video(
    experiment_id: int, scan_id: int, client=None
) -> dict:
    """Validate the scan belongs to the experiment, then generate its video.

    ``client`` lets a caller (the queue worker) reuse its already-authenticated client; the
    on-demand route omits it and a fresh ``app_client()`` is created per request.
    """
    if client is None:
        client = app_client()
    if not scan_in_experiment(client, experiment_id, scan_id):
        raise HTTPException(
            status_code=404,
            detail=f"Scan {scan_id} not found in experiment {experiment_id}",
        )
    result = generate_scan_video(client, scan_id)
    result["scan_id"] = scan_id
    # Only (re)record when we actually wrote a new video — a kept-existing result
    # must not overwrite the recorded frame count with a lower one.
    if result.get("regenerated", True):
        _record_video(client, scan_id, result)
    return result
