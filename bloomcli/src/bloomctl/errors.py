"""error handling for the CLI entry point.

Commands raise `click.ClickException` with a message that says what to do about it.
This catches what is left: the failures nobody anticipated, which would otherwise reach the user as a stack trace.

The traceback still matters for diagnosis, so it goes to a file rather than the screen.
"""

from __future__ import annotations

import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .credentials import default_config_dir

LOG_NAME = "errors.log"

# Options whose value must never reach the log. The log sits beside the credentials and users
# are told where it is so they can send it on, so a password in it would travel with it.
# `--password` has no short form (`-p` is `--profile`), so matching the long name is enough.
# `--anon-key` takes a key that is meant to be public, but it is the flag a service-role key
# gets pasted into by mistake, and no traceback is worth reading for the value of either.
SECRET_OPTIONS = ("--password", "--anon-key")

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


def redact(argv: list[str]) -> list[str]:
    """Blank out the value of any secret option, so the log is safe to pass on."""
    safe: list[str] = []
    hide_next = False
    for arg in argv:
        if hide_next:
            safe.append("***")
            hide_next = False
        elif arg in SECRET_OPTIONS:
            safe.append(arg)
            hide_next = True
        elif arg.split("=", 1)[0] in SECRET_OPTIONS:
            safe.append(f"{arg.split('=', 1)[0]}=***")
        else:
            safe.append(arg)
    return safe


def record(
    exc: BaseException, argv: list[str] | None = None, *, path: Path | None = None
) -> Path | None:
    """Append ``exc``'s traceback to the error log and return where it went, or None if it
    could not be written.

    Written 0600 in a 0700 directory, matching the credentials stored alongside it.

    Raises nothing of its own: this runs while already handling a failure, so every step is
    inside the guard — including rendering the traceback, which is not the safe half it looks
    like. An argument that reached us as undecodable bytes carries surrogates, and encoding
    those raises ValueError, which an `except OSError` would let straight back out.
    """
    log = path or error_log_path()
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        command = " ".join(redact(argv or []))
        body = (
            f"\n{'=' * 70}\n{stamp}  {command}\n{'=' * 70}\n"
            + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        )
        # 0700 because this may be what creates ~/.bloom, and mkdir(exist_ok=True) later
        # cannot tighten a directory that already exists.
        log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _trim(log)
        log.touch(mode=0o600)
        log.chmod(0o600)  # tighten a log left readable by an earlier version
        # errors="replace" so an undecodable path costs a few question marks in the command
        # line rather than the traceback it was written to preserve.
        with log.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write(body)
    except Exception:
        return None  # nothing more can be done; the message on screen is what the user gets
    return log


def _trim(log: Path) -> None:
    """Drop the oldest half once the log passes its cap, so it can't grow forever.

    Rotated through a temp file. Rewriting the log where it lies would empty it before the
    kept half was written, so a rotation that failed would cost every traceback it was called
    to preserve — and rotation only happens on a machine that has been failing often enough
    to fill the log, which is exactly when those tracebacks are worth something.

    A rotation that cannot be finished is abandoned rather than raised: the caller can still
    append to the untrimmed log, and an oversized log beats no log.
    """
    try:
        if log.stat().st_size <= MAX_LOG_BYTES:
            return
        keep = log.read_bytes()[-(MAX_LOG_BYTES // 2) :]
    except OSError:
        return
    tmp = log.with_name(f".{log.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(b"(earlier entries dropped)\n" + keep)
        tmp.chmod(0o600)  # the log's own mode, in case this is what replaces it
        os.replace(tmp, log)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


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
        if log is not None:  # not `log.exists()`: a touched file with no traceback in it
            click.echo(f"Details written to {log}", err=True)
        return 1
    return 0
