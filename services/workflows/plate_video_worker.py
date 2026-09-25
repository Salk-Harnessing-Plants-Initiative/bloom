"""Render one plate's time-lapse from the container.

`render` renders a single plate through the same `render_plate_video` the route
calls, so a video made here is the video the button would have made. The claim
loop that consumes the queue arrives with the queue itself.

Not to be run against staging or production until the render queue lands: the
per-plate lock lives in the service's own process and does not hold against
this one. See services/workflows/README.md.
"""

import argparse
import logging
import sys

from plate_encode import (
    EncoderBusy,
    FrameDepthUnsupported,
    FrameSizeMismatch,
    FrameTooLarge,
    FrameUnreadable,
    NotRecorded,
    PlateMismatch,
    VideoNotStored,
    render_plate_video,
)
from plate_request import MAX_EXPERIMENT_ID, MAX_WAVE_NUMBER, REFUSAL_STATUS
from supabase_client import app_client
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
    "the service renders the same plates, and neither process can see the other's lock."
)

# The status the route answers for each of these, so one classification serves
# both. Subclasses first, as in plate_request's own chain.
STATUS_BY_EXCEPTION = (
    (FrameDepthUnsupported, 422),
    (FrameSizeMismatch, 422),
    (FrameTooLarge, 413),
    (FrameUnreadable, 502),
    (VideoNotStored, 503),
    (NotRecorded, 500),
    (PlateMismatch, 500),
)


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
    overflowing the wave column and reporting itself as a database outage."""
    number = int(value)
    if number < 1 or number > MAX_EXPERIMENT_ID:
        raise argparse.ArgumentTypeError(
            f"an experiment id is between 1 and {MAX_EXPERIMENT_ID}"
        )
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render one plate's time-lapse.",
        epilog=f"{HOLD} Exit codes: 0 rendered or kept, 1 failed, 3 refused.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser(
        "render", help="render one plate and exit", epilog=parser.epilog
    )
    render.add_argument("--experiment", type=experiment, required=True)
    render.add_argument("--plate", required=True)
    render.add_argument(
        "--wave",
        type=wave,
        default=None,
        help="the plate's wave number, or 'none' for a plate with no wave",
    )
    return parser


def parse_args(argv=None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def frames_in(outcome: dict) -> int | None:
    """What the video holds: the encoder's count when one was made, the stored
    video's own when it was kept. Mirrors the route's own fallback order."""
    recorded = outcome.get("recorded") or {}
    held = recorded.get("frame_count", outcome.get("stored_frames"))
    if held is None and outcome.get("action") == "rendered":
        held = len(outcome.get("frames") or []) or None
    return held


def exit_for_exception(exc: Exception) -> int:
    for kind, status in STATUS_BY_EXCEPTION:
        if isinstance(exc, kind):
            return exit_for_status(status)
    return EXIT_FAILED


def main(argv=None) -> int:
    configure_logging()
    args = parse_args(argv)

    try:
        outcome = render_plate_video(
            app_client(), args.experiment, args.plate, args.wave
        )
    except EncoderBusy as exc:  # PlateBusy subclasses it
        # Not a failure of this render: the in-process locks the queue replaces.
        print(f"busy: {exc}")
        return EXIT_FAILED
    except NotRecorded as exc:
        # The video is stored; only the row is missing. Naming that matters.
        logger.exception("plate %r was stored but not recorded", args.plate)
        print(f"failed: stored but not recorded: {exc}")
        return exit_for_exception(exc)
    except Exception as exc:
        logger.exception("plate %r could not be rendered", args.plate)
        print(f"failed: {exc}")
        return exit_for_exception(exc)

    action = outcome.get("action")
    if action == "refuse":
        code = outcome.get("code")
        # Two of the five refusals are the route's own 503s — storage or the
        # database not answering. Those are this service failing, not the
        # renderer declining, so both the word and the code have to say retry.
        exit_code = exit_for_status(REFUSAL_STATUS.get(code, 409))
        label = "refused" if exit_code == EXIT_REFUSED else "failed"
        print(f"{label} ({code}): {outcome.get('reason')}")
        return exit_code

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
