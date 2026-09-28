"""The exit-code contract both video worker commands share.

PR 3's claim loops read these codes to decide retry from dead-letter, so the
mapping from the route's own status to an exit code is pinned here once.
"""

import pytest

from video_worker_cli import EXIT_FAILED, EXIT_OK, EXIT_REFUSED, exit_for_status


def test_the_codes_are_distinct_and_avoid_argparse_s_own():
    """argparse exits 2 on a usage error; a loop must not read that as an
    outcome."""
    assert {EXIT_OK, EXIT_FAILED, EXIT_REFUSED} == {0, 1, 3}


@pytest.mark.parametrize("status", [400, 404, 409, 413, 422])
def test_the_renderer_declining_is_refused(status):
    assert exit_for_status(status) == EXIT_REFUSED


@pytest.mark.parametrize("status", [500, 502, 503])
def test_this_service_failing_is_a_failure(status):
    assert exit_for_status(status) == EXIT_FAILED


def test_busy_is_a_failure_not_a_refusal():
    """429 is the one 4xx that means come back: someone else holds the item."""
    assert exit_for_status(429) == EXIT_FAILED
