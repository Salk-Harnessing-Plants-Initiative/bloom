"""Render plate time-lapses from the container.

`run` consumes the plate video queue: claim a job, render it, complete or fail
it. `render` renders a single plate on command, through the same
`render_plate_video`, for an operator. See services/workflows/README.md.
"""

import argparse
import logging
import sys

from plate_encode import (
    FrameDepthUnsupported,
    FrameSizeMismatch,
    FrameTooLarge,
    FrameUnreadable,
    NotRecorded,
    PlateMismatch,
    VideoNotStored,
    render_plate_video,
)
from video_writer import VideoEncodeError
from plate_request import MAX_EXPERIMENT_ID, MAX_WAVE_NUMBER, REFUSAL_STATUS
from supabase_client import app_client
from video_queue import KEPT, PLATE_QUEUE, RENDERED, RenderFailed, UNEXPECTED
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
    "A `render` here does not take the queue's one-job-per-plate hold, so do not "
    "run one for a plate the queue may be rendering."
)

# The HTTP-style status each of these reads as, for `render`'s exit code: a 4xx
# refuses, a 5xx says retry. Subclasses first.
#
# `PlateMismatch` is 409 rather than 500: there is nothing to repair and nothing
# to retry — the key and the identity cannot both be right, and they will not
# become right on a second attempt.
STATUS_BY_EXCEPTION = (
    (FrameDepthUnsupported, 422),
    (FrameSizeMismatch, 422),
    (FrameTooLarge, 413),
    (FrameUnreadable, 502),
    (VideoNotStored, 503),
    (NotRecorded, 500),
    (VideoEncodeError, 500),
    (BrokenPipeError, 500),
    (PlateMismatch, 409),
)


# The job's error code for each failure, the page's vocabulary. Subclasses first.
CODE_BY_EXCEPTION = (
    (FrameDepthUnsupported, "frame_depth_unsupported"),
    (FrameSizeMismatch, "frame_size_mismatch"),
    (FrameTooLarge, "frame_too_large"),
    (FrameUnreadable, "frame_unreadable"),
    (VideoNotStored, "not_stored"),
    (NotRecorded, "not_recorded"),
    (VideoEncodeError, "encode_failed"),
    (BrokenPipeError, "encode_failed"),
    (PlateMismatch, "plate_mismatch"),
)


def code_for_exception(exc: Exception) -> str:
    for kind, code in CODE_BY_EXCEPTION:
        if isinstance(exc, kind):
            return code
    return UNEXPECTED


def render_job(client, job: dict, progress) -> str:
    """One claimed job, rendered: 'rendered' or 'kept', or RenderFailed."""
    try:
        outcome = render_plate_video(
            client, job["experiment_id"], job["plate_id"], job["wave_number"], progress
        )
    except Exception as exc:
        code = code_for_exception(exc)
        if code == UNEXPECTED:
            raise
        raise RenderFailed(code, str(exc)) from exc

    action = outcome.get("action")
    if action == "refuse":
        raise RenderFailed(
            outcome.get("code") or UNEXPECTED, outcome.get("reason") or ""
        )
    if action == "keep":
        # The reason says why: current, no frames visible, or frames gone missing.
        logger.info("plate job %s kept: %s", job["job_id"], outcome.get("reason"))
        return KEPT
    if action == "rendered":
        return RENDERED
    raise RenderFailed(UNEXPECTED, f"the renderer answered {action!r}")


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
    """The column's own bounds, so an out-of-range id is refused by name here
    rather than reaching the database and reporting itself as an outage."""
    number = int(value)
    if number < 1 or number > MAX_EXPERIMENT_ID:
        raise argparse.ArgumentTypeError(
            f"an experiment id is between 1 and {MAX_EXPERIMENT_ID}"
        )
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render plate time-lapses.",
        epilog=f"{HOLD} Exit codes: 0 rendered or kept, 1 failed, 3 refused.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="consume the plate video queue until stopped")
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
    video's own when it was kept."""
    recorded = outcome.get("recorded") or {}
    held = recorded.get("frame_count", outcome.get("stored_frames"))
    if held is None and outcome.get("action") == "rendered":
        held = len(outcome.get("frames") or []) or None
    return held


def label_for(exit_code: int) -> str:
    """The word an operator reads, taken from the code a caller reads, so the
    two can never say opposite things about the same outcome."""
    return "refused" if exit_code == EXIT_REFUSED else "failed"


def exit_for_exception(exc: Exception) -> int:
    for kind, status in STATUS_BY_EXCEPTION:
        if isinstance(exc, kind):
            return exit_for_status(status)
    return EXIT_FAILED


def main(argv=None) -> int:
    configure_logging()
    args = parse_args(argv)

    if args.command == "run":
        # The render's own reads use this client, so it keeps the default timeout.
        Worker(PLATE_QUEUE, render_job, app_client).run()
        return EXIT_OK

    try:
        outcome = render_plate_video(
            app_client(), args.experiment, args.plate, args.wave
        )
    except NotRecorded as exc:
        # The video is stored; only the row is missing. Naming that matters.
        logger.exception("plate %r was stored but not recorded", args.plate)
        print(f"failed: stored but not recorded: {exc}")
        return exit_for_exception(exc)
    except Exception as exc:
        logger.exception("plate %r could not be rendered", args.plate)
        # The word has to come from the code: an oversized or unsupported frame
        # is the plate's own, and telling an operator "failed" invites a retry
        # of something that cannot succeed.
        exit_code = exit_for_exception(exc)
        print(f"{label_for(exit_code)}: {exc}")
        return exit_code

    action = outcome.get("action")
    if action == "refuse":
        code = outcome.get("code")
        # Two of the five refusals are 503s — storage or the database not
        # answering. Those are this service failing, not the
        # renderer declining, so both the word and the code have to say retry.
        exit_code = exit_for_status(REFUSAL_STATUS.get(code, 409))
        print(f"{label_for(exit_code)} ({code}): {outcome.get('reason')}")
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
