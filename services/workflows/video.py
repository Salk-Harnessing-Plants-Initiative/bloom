"""
Generate an MP4 from a cylinder scan and write it to Supabase Storage, using a
dedicated least-privilege app user (see supabase_client.py). All DB and storage
access goes through the authenticated Supabase client, so the app user's grants
and storage policies bound what this can touch.

Flow: validate scan ∈ experiment (cyl_scans_extended) -> read scan images
(cyl_images) -> download each frame from the images bucket -> decimate -> encode
H.264 with VideoWriter -> upload MP4 to the videos bucket -> signed URL ->
record the row through the record_cyl_scan_video wrapper.
"""

import io
import logging
import os
import tempfile
import threading
from typing import NamedTuple

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
# Path within the videos bucket — must match what Bloom web reads. The web side
# defines it once in web/lib/supabase/scan-video-path.ts; the two are pinned
# together by tests/unit/test_cyl_video_path_agreement.py.
VIDEO_PATH_PREFIX = "cyl-videos"
# Record table linking scan_id -> stored video path (one row per scan, written
# through the record_cyl_scan_video wrapper — never upserted; see _record_video).
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
        return PUBLIC_SUPABASE_URL.rstrip("/") + url[len(internal):]
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
    """A scan's encodable images (object_path, frame_number), ordered, capped at `limit`.

    Rows without an object are excluded in the query rather than skipped later, for two
    reasons. They would otherwise spend slots in the cap, pushing real images at the tail
    outside the window and baking a permanently short video for a scan whose frames were all
    there. And `frames_expected` would then count rows this can never encode, while the
    recorded count only ever counts rows that have one — so the two would be measured
    differently and the comparison between them would not mean anything.
    """
    return (
        client.table("cyl_images")
        .select("object_path, frame_number")
        .eq("scan_id", scan_id)
        .not_.is_("object_path", "null")
        .order("frame_number")
        .limit(limit)
        .execute()
        .data
        or []
    )


# Deciding whether to keep the stored video and then uploading is a read-modify-write on an
# unversioned object. This lock holds only within one process, so it depends on the service
# running a single uvicorn worker — `--workers 1` in docker-compose.prod.yml. Raising that
# reopens the race, and closing it across processes needs a lock in the database instead.
_scan_locks: dict[int, threading.Lock] = {}
_scan_locks_guard = threading.Lock()


def _scan_lock(scan_id: int) -> threading.Lock:
    """The lock for one scan, created on first use."""
    with _scan_locks_guard:
        return _scan_locks.setdefault(scan_id, threading.Lock())


def _stored_video_exists(vids, key: str) -> bool | None:
    """Whether an object is stored at ``key``, or None when storage could not say.

    None is neither yes nor no, and it must not collapse into either. Read as "no", a storage
    outage overwrites a good rotation with whatever this run managed to encode — and the same
    outage skips frame downloads, so an unclear answer arrives precisely when this encode is
    the worse one. Read as "yes", the request keeps a video it then cannot sign a URL for.
    Callers refuse instead: an ambiguous answer about an unversioned object is not a licence
    to replace it.
    """
    try:
        vids.create_signed_url(key, DOWNLOAD_URL_TTL)
        return True
    except Exception as exc:
        message = str(exc).lower()
        if "not found" in message or "not_found" in message or "does not exist" in message:
            return False
        logger.warning("could not check for a stored video at %s: %s", key, exc)
        return None


def _confirm_stored_video(vids, key: str, scan_id: int) -> bool:
    """Whether a video is stored at ``key``, refusing the request if storage cannot say.

    Both answers this gates are irreversible in one direction or the other — keep a video
    that may not be there, or overwrite one that is — so an unclear answer ends the request
    instead of picking one.
    """
    stored = _stored_video_exists(vids, key)
    if stored is None:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Could not confirm whether scan {scan_id} already has a video. "
                "Nothing was changed. Try again shortly."
            ),
        )
    return stored


class RecordedVideo(NamedTuple):
    """What the record table says about a scan.

    `row_exists` and `frames` are separate because a row with a NULL count and no row at all
    are different facts that read the same from `frames` alone: one has already been recorded
    as unmeasured, the other has never been recorded. Collapsing them makes every request
    rewrite the same NULL over itself.
    """

    row_exists: bool
    frames: int | None


def _recorded_frames(client, scan_id: int) -> RecordedVideo:
    """What is recorded for this scan.

    A lookup that *failed* is not an absence, and must not be reported as one. Read as "no
    row", a transient error sends the request down the branch for a video nobody has measured:
    the stored video is kept whatever this run encoded, and a null count is written over the
    real one. It raises instead.
    """
    if not VIDEO_TABLE:
        return RecordedVideo(row_exists=False, frames=None)
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
    except Exception as exc:
        logger.warning("scan %s: could not read the video record: %s", scan_id, exc)
        raise HTTPException(
            status_code=503,
            detail=f"Could not check the recorded video for scan {scan_id}. Nothing was changed.",
        ) from exc
    if not rows:
        return RecordedVideo(row_exists=False, frames=None)
    return RecordedVideo(row_exists=True, frames=rows[0].get("frames"))


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
            scan_id, MAX_IMAGES, MAX_IMAGES,
        )
    frames_expected = len(images)

    key = f"{VIDEO_PATH_PREFIX}/{scan_id}.mp4"
    vids = client.storage.from_(VIDEOS_BUCKET)

    # Decided before the encode, not after. `frames_written` can only ever be at most
    # `frames_expected`, so whenever the stored video already matches that upper bound this
    # run cannot beat it whatever it produces — and downloading 72 frames and running ffmpeg
    # to discard the result is pure waste. Every scan whose video predates the record table
    # takes this path on every request.
    record = _recorded_frames(client, scan_id)
    prior_frames = record.frames
    if prior_frames is None or frames_expected <= prior_frames:
        if _confirm_stored_video(vids, key, scan_id):
            if prior_frames is None:
                logger.warning(
                    "scan %s: a video is stored with no recorded frame count; keeping it",
                    scan_id,
                )
                kept = _result(
                    scan_id, vids, key, frames_expected, frames_expected, truncated,
                    regenerated=False,
                )
                # Recorded once, not on every request: a row already carrying a NULL count
                # says all this can say, so rewriting it each time changes nothing. Until
                # something measures the stored file, this scan keeps landing here — that is
                # the conservative answer, not a resolved one.
                # The response still carries a number because the client's shape guard
                # requires one; the web summary suppresses it when regenerated is false.
                kept["stored_frames_unknown"] = not record.row_exists
                return kept
            logger.warning(
                "scan %s: at most %s frames available, recorded %s; keeping the existing video",
                scan_id, frames_expected, prior_frames,
            )
            return _result(
                scan_id, vids, key, prior_frames, frames_expected, truncated,
                regenerated=False,
            )

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
            scan_id, frames_written, frames_expected, frames_expected - frames_written,
        )

    # The gate above cleared this run to encode because `frames_expected` beat the recorded
    # count. Skipped frames can have brought the real total back down to it, so the same rule
    # is applied again now that the actual number is known. A tie keeps the stored video: the
    # same frame count is not the same frames — rows that finished uploading and rows that
    # became unreadable cancel out — and overwriting an unversioned object on a request anyone
    # signed in can make needs a reason beyond "no worse".
    if (
        prior_frames is not None
        and frames_written <= prior_frames
        and _confirm_stored_video(vids, key, scan_id)
    ):
        logger.warning(
            "scan %s: new encode has %s frames, recorded %s; keeping the existing video",
            scan_id, frames_written, prior_frames,
        )
        return _result(
            scan_id, vids, key, prior_frames, frames_expected, truncated, regenerated=False
        )

    vids.upload(key, video_bytes, {"content-type": "video/mp4", "upsert": "true"})
    return _result(
        scan_id, vids, key, frames_written, frames_expected, truncated, regenerated=True
    )


def _result(scan_id, vids, key, frames, frames_expected, truncated, regenerated) -> dict:
    """Build the response, failing (not returning null) if no URL can be signed."""
    download_url = _signed_url(vids, key)
    if not download_url:
        # A response without a usable URL is a failure, not a success.
        raise HTTPException(
            status_code=500, detail=f"Could not create a download URL for scan {scan_id}"
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
    """Record the scan -> video-path row (best-effort; one row per scan).

    Through a wrapper rather than an upsert: PostgREST builds its DO UPDATE from every key in
    the payload, so it writes scan_id too, and bloom_workflows deliberately cannot update that
    column. The wrapper matches on scan_id without setting it.
    """
    if not VIDEO_TABLE:
        return
    try:
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


def generate_experiment_scan_video(experiment_id: int, scan_id: int) -> dict:
    """Validate the scan belongs to the experiment, then generate its video."""
    client = app_client()
    if not scan_in_experiment(client, experiment_id, scan_id):
        raise HTTPException(
            status_code=404,
            detail=f"Scan {scan_id} not found in experiment {experiment_id}",
        )
    # Held across the encode and the record so two requests for one scan cannot interleave.
    with _scan_lock(scan_id):
        result = generate_scan_video(client, scan_id)
        result["scan_id"] = scan_id
        # Only (re)record when we actually wrote a new video — a kept-existing result
        # must not overwrite the recorded frame count with a lower one. The one exception
        # is a kept video that had no row at all: recording the path with no count claims
        # nothing, and without it the scan can never leave that branch.
        if result.pop("stored_frames_unknown", False):
            _record_video(client, scan_id, {**result, "frames": None})
        elif result.get("regenerated", True):
            _record_video(client, scan_id, result)
    return result
