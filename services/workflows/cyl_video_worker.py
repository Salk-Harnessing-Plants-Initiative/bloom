"""Render one cylinder scan's video from the container.

`render` renders a single scan through the same `generate_experiment_scan_video`
the route calls, so a video made here is the video the button would have made.
The claim loop that consumes the queue arrives with the queue itself.

Not to be run against staging or production until the render queue lands: the
per-scan lock lives in the service's own process and does not hold against this
one. See services/workflows/README.md.
"""

import argparse
import logging
import sys

from fastapi import HTTPException

from supabase_client import app_client
from video import generate_experiment_scan_video
from video_worker_cli import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_REFUSED,
    configure_logging,
    exit_for_status,
)

logger = logging.getLogger(__name__)

HOLD = (
    "Not to be run against staging or production until the render queue lands: "
    "the service renders the same scans, and neither process can see the other's lock."
)


def identifier(value: str) -> int:
    """No upper bound: the cyl id columns are BIGINT, and the route bounds
    nothing either. A too-large id simply finds no row and is refused."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("an id is 1 or greater")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render one cyl scan's video.",
        epilog=f"{HOLD} Exit codes: 0 rendered or kept, 1 failed, 3 refused.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
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

    try:
        result = generate_experiment_scan_video(
            args.experiment, args.scan, client=app_client()
        )
    except HTTPException as exc:
        exit_code = exit_for_status(exc.status_code)
        if exit_code == EXIT_REFUSED:
            print(f"refused ({exc.status_code}): {exc.detail}")
            return exit_code
        # A 5xx can be raised after the object was uploaded — signing its URL is
        # the last step before the row is written — so the stored video and the
        # recorded row may now disagree.
        logger.error("scan %s could not be rendered: %s", args.scan, exc.detail)
        print(f"failed ({exc.status_code}): {exc.detail}")
        print("  check the stored video against its record before retrying")
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
