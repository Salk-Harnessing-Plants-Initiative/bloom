"""The exit codes commands share, and the exceptions that carry them.

0 done; 1 a network or server error, worth retrying; 2 a usage error; 3 refused, or nothing to
fetch (asking again won't help); 4 something is still in progress (worth waiting for).
"""

from __future__ import annotations

import click

EXIT_REFUSED = 3
EXIT_IN_PROGRESS = 4


class Refused(click.ClickException):
    """Bloom said no, or there is nothing to fetch; asking again won't help."""

    exit_code = EXIT_REFUSED


class InProgress(click.ClickException):
    """A build is still uploading; it may be ready later."""

    exit_code = EXIT_IN_PROGRESS


class Transient(click.ClickException):
    """A server or network error; running the command again may work."""
