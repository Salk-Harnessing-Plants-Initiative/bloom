"""Render one plate's time-lapse from the container.

`render` renders a single plate through the same `render_plate_video` the route
calls, so a video made here is the video the button would have made. The claim
loop that consumes the queue arrives with the queue itself.

Until the queue enforces one active job per plate, this command and the button
are two processes with no lock between them: run it only for a plate nobody is
generating from the app. See the README.

Exit codes: 0 rendered or kept, 1 failed (retry may help), 3 refused (it will
not). 2 is argparse's own usage error.

Run:
    docker compose run --rm plate-video-worker python plate_video_worker.py \
        render --experiment 1886 --plate Plate_19 --wave 13
"""

import argparse
import logging
import sys

from plate_encode import EncoderBusy, render_plate_video
from plate_request import MAX_EXPERIMENT_ID, MAX_WAVE_NUMBER
from supabase_client import app_client

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1
# Not 2: argparse exits 2 on a usage error, and a caller looping over plates has
# to tell a typo'd flag from a plate the renderer declined.
EXIT_REFUSED = 3


def wave(value: str) -> int | None:
    """`--wave none` is a plate with no wave, which is a real case."""
    if value.strip().lower() == "none":
        return None
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("a wave number is zero or greater, or 'none'")
    if number > MAX_WAVE_NUMBER:
        raise argparse.ArgumentTypeError(f"a wave number is at most {MAX_WAVE_NUMBER}")
    return number


def experiment(value: str) -> int:
    """The route's own bounds, so an out-of-range id is refused here rather than
    overflowing an INT column and reporting itself as a database outage."""
    number = int(value)
    if number < 1 or number > MAX_EXPERIMENT_ID:
        raise argparse.ArgumentTypeError(
            f"an experiment id is between 1 and {MAX_EXPERIMENT_ID}"
        )
    return number


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render one plate's time-lapse.")
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render", help="render one plate and exit")
    render.add_argument("--experiment", type=experiment, required=True)
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
    video's own when it was kept. Mirrors the route's own fallback order."""
    recorded = outcome.get("recorded") or {}
    held = recorded.get("frame_count", outcome.get("stored_frames"))
    if held is None and outcome.get("action") == "rendered":
        held = len(outcome.get("frames") or []) or None
    return held


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    args = parse_args(argv)

    try:
        outcome = render_plate_video(
            app_client(), args.experiment, args.plate, args.wave
        )
    except EncoderBusy as exc:  # PlateBusy subclasses it
        # Not a failure of this render — the in-process locks the queue replaces.
        print(f"busy: {exc}")
        return EXIT_FAILED
    except Exception as exc:
        logger.exception("plate %r could not be rendered", args.plate)
        print(f"failed: {exc}")
        return EXIT_FAILED

    action = outcome.get("action")
    if action == "refuse":
        print(f"refused ({outcome.get('code')}): {outcome.get('reason')}")
        return EXIT_REFUSED

    frames = frames_in(outcome)
    held = "an unrecorded number of" if frames is None else frames
    if action == "keep":
        # The reason distinguishes "already covers every frame" from the two
        # anomalies: no frames visible, and frames that have gone missing.
        print(f"kept ({held} frames): {outcome.get('reason')}")
        return EXIT_OK
    if action == "rendered":
        print(f"rendered {held} frames to {outcome.get('key')}")
        return EXIT_OK

    # An action this command does not know is a contract change, not a success.
    print(f"failed: the renderer answered {action!r}")
    return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
