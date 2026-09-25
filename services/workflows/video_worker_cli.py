"""What the video worker commands agree on: their exit codes and their logging.

PR 3's claim loops read these codes to decide retry from dead-letter, so both
commands have to mean the same thing by them, and one definition is how that
stays true.
"""

import logging

EXIT_OK = 0
# Retrying may help: this service, storage or the database failed, or something
# else holds the item right now.
EXIT_FAILED = 1
# Not 2: argparse exits 2 on a usage error, and a caller looping over items has
# to tell a typo'd flag from an item the renderer declined.
EXIT_REFUSED = 3


def exit_for_status(status: int) -> int:
    """The route's own answer, read as an exit code.

    A 4xx is the renderer declining what was asked, which a retry cannot change
    — except 429, which means someone else is rendering and later will do. Every
    5xx is this service, storage or the database failing.
    """
    if status == 429:
        return EXIT_FAILED
    return EXIT_FAILED if status >= 500 else EXIT_REFUSED


def configure_logging() -> None:
    """Timestamps, like the sibling workers: a render is minutes long and the
    log is how its stages are read."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
