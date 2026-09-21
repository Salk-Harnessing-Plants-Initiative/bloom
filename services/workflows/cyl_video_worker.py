"""Render one cylinder scan's video from the container.

`render` renders a single scan through the same `generate_experiment_scan_video`
the route calls, so a video made here is the video the button would have made.
The claim loop that consumes the queue arrives with the queue itself.

Until the queue enforces one active job per scan, this command and the button
are two processes with no lock between them: run it only for a scan nobody is
generating from the app. See the README.

Exit codes: 0 rendered or kept, 1 failed (retry may help), 3 refused (it will
not). 2 is argparse's own usage error.

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
# Not 2: argparse exits 2 on a usage error, and a caller looping over scans has
# to tell a typo'd flag from a scan the renderer declined.
EXIT_REFUSED = 3


def identifier(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("an id is 1 or greater")
    return number


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render one cyl scan's video.")
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render", help="render one scan and exit")
    render.add_argument("--experiment", type=identifier, required=True)
    render.add_argument("--scan", type=identifier, required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    args = parse_args(argv)

    try:
        result = generate_experiment_scan_video(
            args.experiment, args.scan, client=app_client()
        )
    except HTTPException as exc:
        # A 4xx is the renderer declining: the scan is not in the experiment, or
        # it has no images. A 5xx is this service failing, and one of them —
        # the download URL it signs after uploading — leaves the object written
        # and the row not updated, so it must not read as "nothing happened".
        if exc.status_code < 500:
            print(f"refused ({exc.status_code}): {exc.detail}")
            return EXIT_REFUSED
        logger.error("scan %s could not be rendered: %s", args.scan, exc.detail)
        print(f"failed ({exc.status_code}): {exc.detail}")
        return EXIT_FAILED
    except Exception as exc:
        logger.exception("scan %s could not be rendered", args.scan)
        print(f"failed: {exc}")
        return EXIT_FAILED

    frames = result.get("frames")
    expected = result.get("frames_expected")
    path = result.get("path")
    if result.get("regenerated", True):
        skipped = ""
        if expected is not None and frames is not None and frames < expected:
            skipped = f" ({expected - frames} of {expected} could not be read)"
        truncated = ""
        if result.get("truncated"):
            truncated = "; the scan has more frames than the encoder's cap"
        print(f"rendered {frames} frames{skipped} to {path}{truncated}")
        return EXIT_OK

    # `frames` on this path describes the database, not the stored file: a video
    # recorded without a count reports the rows present now. Say what it is.
    print(f"kept: {path} is current for the {frames} frames now recorded")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
