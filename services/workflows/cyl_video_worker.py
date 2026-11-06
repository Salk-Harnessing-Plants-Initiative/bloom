"""Render cylinder scan videos from the container.

`run` consumes the cyl video queue: claim a job, render it, complete or fail
it. `render` renders a single scan on command, through the same
`generate_experiment_scan_video`, for an operator. See
services/workflows/README.md.
"""

import argparse
import logging
import sys

from fastapi import HTTPException

from supabase_client import app_client
from video import (
    NO_FRAMES_ENCODED,
    NOT_IN_EXPERIMENT,
    NOT_SIGNED,
    RECORD_UNREADABLE,
    generate_experiment_scan_video,
)
from video_queue import CYL_QUEUE, KEPT, RENDERED, RenderFailed
from video_worker_loop import Worker
from video_worker_cli import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_REFUSED,
    configure_logging,
    exit_for_status,
)

logger = logging.getLogger(__name__)

HOLD = (
    "A `render` here does not take the queue's one-job-per-scan hold, so do not "
    "run one for a scan the queue may be rendering."
)


def code_for_refusal(exc: HTTPException) -> str:
    """The job's error code for one of video.py's refusals."""
    detail = str(exc.detail)
    if exc.status_code == 404:
        # The route checks the pair before queueing, so a 404 here is a scan with no images.
        return "scan_not_in_experiment" if NOT_IN_EXPERIMENT in detail else "no_images"
    if exc.status_code == 503:
        # The record table is a database read; everything else at 503 is storage.
        return (
            "database_unavailable"
            if detail.startswith(RECORD_UNREADABLE)
            else "storage_unavailable"
        )
    if detail.startswith(NO_FRAMES_ENCODED):
        return "no_frames_encoded"
    if detail.startswith(NOT_SIGNED):
        return "not_signed"
    return "encode_failed"


def render_job(client, job: dict, progress) -> str:
    """One claimed job, rendered: 'rendered' or 'kept', or RenderFailed."""
    try:
        result = generate_experiment_scan_video(
            job["experiment_id"], job["scan_id"], client=client, progress=progress
        )
    except HTTPException as exc:
        raise RenderFailed(code_for_refusal(exc), str(exc.detail)) from exc
    return RENDERED if result.get("regenerated", True) else KEPT


def identifier(value: str) -> int:
    """No upper bound: the cyl id columns are BIGINT, and the route bounds
    nothing either. A too-large id simply finds no row and is refused."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("an id is 1 or greater")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render cyl scan videos.",
        epilog=f"{HOLD} Exit codes: 0 rendered or kept, 1 failed, 3 refused.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="consume the cyl video queue until stopped")
    render = commands.add_parser(
        "render", help="render one scan and exit", epilog=parser.epilog
    )
    render.add_argument("--experiment", type=identifier, required=True)
    render.add_argument("--scan", type=identifier, required=True)
    return parser


def parse_args(argv=None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv=None) -> int:
    configure_logging()
    args = parse_args(argv)

    if args.command == "run":
        Worker(CYL_QUEUE, render_job, app_client).run()
        return EXIT_OK

    try:
        result = generate_experiment_scan_video(
            args.experiment, args.scan, client=app_client()
        )
    except HTTPException as exc:
        exit_code = exit_for_status(exc.status_code)
        if exit_code == EXIT_REFUSED:
            print(f"refused ({exc.status_code}): {exc.detail}")
            return exit_code
        logger.error("scan %s could not be rendered: %s", args.scan, exc.detail)
        print(f"failed ({exc.status_code}): {exc.detail}")
        if NOT_SIGNED in exc.detail:
            # The only failure raised after the object is written: signing its
            # URL is the last step before the row. Every other 5xx stops before
            # anything is stored, and saying otherwise cries wolf on a re-render.
            print("  the video was stored; its record was not updated")
        return exit_code
    except Exception as exc:
        logger.exception("scan %s could not be rendered", args.scan)
        print(f"failed: {exc}")
        return EXIT_FAILED

    frames = result.get("frames")
    expected = result.get("frames_expected")
    path = result.get("path")
    capped = (
        "; the scan has more frames than the encoder's cap"
        if result.get("truncated")
        else ""
    )
    if result.get("regenerated", True):
        skipped = ""
        if expected is not None and frames is not None and frames < expected:
            skipped = f" ({expected - frames} of {expected} could not be read)"
        print(f"rendered {frames} frames{skipped} to {path}{capped}")
        return EXIT_OK

    # `frames` means the image rows on one keep branch and the video record's
    # own count on the others, and nothing in the result says which — so report
    # it as what the service said, not as a measurement of the file.
    print(f"kept: {path} was not remade (reported frames: {frames}){capped}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
