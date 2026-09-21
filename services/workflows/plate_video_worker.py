"""Render one plate's time-lapse from the container.

`render` renders a single plate through the same `render_plate_video` the route
calls, so a video made here is the video the button would have made. The claim
loop that consumes the queue arrives with the queue itself.

Run:
    docker compose run --rm plate-video-worker python plate_video_worker.py \
        render --experiment 1886 --plate Plate_19 --wave 13
"""

import argparse
import logging
import sys

from plate_encode import render_plate_video
from supabase_client import app_client

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2


def wave(value: str) -> int | None:
    """`--wave none` is a plate with no wave, which is a real case."""
    if value.strip().lower() == "none":
        return None
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("a wave number is zero or greater, or 'none'")
    return number


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render one plate's time-lapse.")
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render", help="render one plate and exit")
    render.add_argument("--experiment", type=int, required=True)
    render.add_argument("--plate", required=True)
    render.add_argument(
        "--wave",
        type=wave,
        default=None,
        help="the plate's wave number, or 'none' for a plate with no wave",
    )
    return parser.parse_args(argv)


def frames_in(outcome: dict) -> int | None:
    """What the video holds: the encoder's count when one was made, the stored
    video's own when it was kept."""
    recorded = outcome.get("recorded") or {}
    return recorded.get("frame_count", outcome.get("stored_frames"))


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        outcome = render_plate_video(
            app_client(), args.experiment, args.plate, args.wave
        )
    except Exception as exc:
        logger.exception("plate %s could not be rendered", args.plate)
        print(f"failed: {exc}")
        return EXIT_FAILED

    action = outcome.get("action")
    if action == "refuse":
        print(f"refused ({outcome.get('code')}): {outcome.get('reason')}")
        return EXIT_REFUSED

    frames = frames_in(outcome)
    held = "an unrecorded number of" if frames is None else frames
    if action == "keep":
        print(f"kept: the stored video already holds {held} frames")
        return EXIT_OK
    if action == "rendered":
        print(f"rendered {held} frames to {outcome.get('key')}")
        return EXIT_OK

    # An action this command does not know is a contract change, not a success.
    print(f"failed: the renderer answered {action!r}")
    return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
