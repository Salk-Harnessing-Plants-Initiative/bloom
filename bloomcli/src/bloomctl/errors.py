"""error handling for the CLI entry point.

Commands raise `click.ClickException` with a message that says what to do about it.
This catches what is left: the failures nobody anticipated, which would otherwise reach the user as a stack trace.

The traceback still matters for diagnosis, so it goes to a file rather than the screen.
"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .credentials import default_config_dir

LOG_NAME = "errors.log"

# Keep the file from growing without bound on a machine that hits errors often. Big enough to
# hold the last several failures, small enough to paste into a message.
MAX_LOG_BYTES = 256 * 1024


def error_log_path(config_dir: Path | None = None) -> Path:
    """Where tracebacks are recorded (``~/.bloom/errors.log``).

    Beside the credentials rather than in the working directory, so it is the same path every
    time and stays writable when the output directory is not.
    """
    return (config_dir or default_config_dir()) / LOG_NAME


def explain(exc: BaseException) -> str:
    """One plain sentence describing ``exc``, for someone who did not write this program."""
    # OSError carries the useful half in strerror ("No space left on device"); str() wraps it
    # in an errno and a repr'd filename.
    if isinstance(exc, OSError):
        detail = exc.strerror or str(exc) or type(exc).__name__
        return f"{detail}: {exc.filename}" if exc.filename else detail

    # postgrest raises this for anything the API rejected, with the server's own wording.
    message = getattr(exc, "message", None)
    if isinstance(message, str) and message:
        return message

    if _is_network_error(exc):
        return f"could not reach Bloom ({type(exc).__name__}) — check your connection and retry"

    return str(exc) or type(exc).__name__


def _is_network_error(exc: BaseException) -> bool:
    """True for a connection-level httpx failure.

    Recognised by type: a dropped connection or a timeout often carries no message at all,
    so there is nothing to match text against.
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a hard dependency of supabase
        return False
    return isinstance(exc, httpx.TransportError)


def record(exc: BaseException, argv: list[str] | None = None, *, path: Path | None = None) -> Path:
    """Append ``exc``'s traceback to the error log and return where it went.

    Raises nothing of its own: this runs while already handling a failure, and the disk being
    full is one of the failures it has to survive. The caller checks whether the file appeared.
    """
    log = path or error_log_path()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    command = " ".join(argv or [])
    body = (
        f"\n{'=' * 70}\n{stamp}  {command}\n{'=' * 70}\n"
        + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    )
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        _trim(log)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(body)
    except OSError:
        pass  # nothing more can be done here; the message on screen is what the user gets
    return log


def _trim(log: Path) -> None:
    """Drop the oldest half once the log passes its cap, so it can't grow forever."""
    try:
        if log.stat().st_size <= MAX_LOG_BYTES:
            return
        keep = log.read_bytes()[-(MAX_LOG_BYTES // 2) :]
    except OSError:
        return
    log.write_bytes(b"(earlier entries dropped)\n" + keep)


def main(args: Any = None) -> int:
    """Entry point: run the CLI, and turn anything unhandled into a message plus a log entry.

    Only genuinely unexpected failures arrive here. `ClickException`, usage errors and Ctrl-C
    are all handled by click and leave as `SystemExit`, which is a `BaseException` and passes
    straight through.
    """
    import sys

    import click

    from .cli import cli

    try:
        cli(args=args)
    except Exception as exc:
        click.echo(f"Error: {explain(exc)}", err=True)
        log = record(exc, sys.argv)
        if log.exists():
            click.echo(f"Details written to {log}", err=True)
        return 1
    return 0
