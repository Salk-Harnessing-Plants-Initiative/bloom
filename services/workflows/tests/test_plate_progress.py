"""What the page is told while a render runs."""

import plate_encode
import plate_progress


def teardown_function():
    plate_progress.finish()


def test_nothing_is_reported_when_no_render_is_running():
    assert plate_progress.current(1, "P1", 2) is None


def test_a_running_render_reports_its_stage_and_count():
    plate_progress.start(1, "P1", 2)
    plate_progress.advance("downloading", 47, 86)

    assert plate_progress.current(1, "P1", 2) == {
        "stage": "downloading",
        "done": 47,
        "total": 86,
    }


def test_progress_is_only_reported_for_the_plate_that_is_rendering():
    plate_progress.start(1, "P1", 2)

    assert plate_progress.current(1, "P2", 2) is None
    assert plate_progress.current(9, "P1", 2) is None
    assert plate_progress.current(1, "P1", 3) is None


def test_a_wave_less_plate_is_matched_on_none():
    plate_progress.start(1, "P1", None)

    assert plate_progress.current(1, "P1", None) is not None
    assert plate_progress.current(1, "P1", 0) is None


def test_nothing_is_reported_once_the_render_ends():
    # A stale entry would have the page report progress for a finished render.
    plate_progress.start(1, "P1", 2)
    plate_progress.advance("encoding", 86, 86)
    plate_progress.finish()

    assert plate_progress.current(1, "P1", 2) is None


def test_advancing_outside_a_render_is_ignored():
    plate_progress.advance("downloading", 3, 10)

    assert plate_progress.current(1, "P1", 2) is None


def test_one_slot_is_only_safe_while_one_render_runs_at_a_time():
    # The record is a single slot, so a second concurrent render would report
    # its frames under the first plate's name.
    assert plate_encode.MAX_CONCURRENT_ENCODES == 1
