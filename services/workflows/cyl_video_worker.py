"""Render one cylinder scan's video from the container.

`render` renders a single scan through the same `generate_experiment_scan_video`
the route calls, so a video made here is the video the button would have made.
The claim loop that consumes the queue arrives with the queue itself.

Run:
    docker compose run --rm cyl-video-worker python cyl_video_worker.py \
        render --experiment 1 --scan 5
"""

import argparse
import logging
import sys

from fastapi import HTTPException

from supabase_client import app_client
from video import generate_experiment_scan_video

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render one cyl scan's video.")
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render", help="render one scan and exit")
    render.add_argument("--experiment", type=int, required=True)
    render.add_argument("--scan", type=int, required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        result = generate_experiment_scan_video(
            args.experiment, args.scan, client=app_client()
        )
    except HTTPException as exc:
        # The route's own refusals: a scan outside the experiment, or one with
        # no images. Nothing to retry.
        print(f"refused: {exc.detail}")
        return EXIT_REFUSED
    except Exception as exc:
        logger.exception("scan %s could not be rendered", args.scan)
        print(f"failed: {exc}")
        return EXIT_FAILED

    frames = result.get("frames")
    truncated = ""
    if result.get("truncated"):
        truncated = " (truncated: the scan has more frames than the encoder's cap)"
    if result.get("regenerated", True):
        print(f"rendered {frames} frames to {result.get('path')}{truncated}")
    else:
        print(f"kept: the stored video already holds {frames} frames{truncated}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
