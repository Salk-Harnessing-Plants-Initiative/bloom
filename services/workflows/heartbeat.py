#!/usr/bin/env python3
"""Liveness heartbeat for the long-running workers, and the probe that reads it.

dispatch_worker and status_poller are `while` loops with no HTTP server, so a
container healthcheck has nothing to call and `docker compose ps` can only say
the process exists. A process that exists proves nothing: a loop wedged on a
hung socket looks identical to one doing its job (issue #163).

So each loop records a timestamp every time round, and the healthcheck fails
when that timestamp goes stale. `--max-age` must exceed the slowest legitimate
iteration, which is far longer than the poll interval: one dispatch submission
carries a 15s HTTP timeout plus its RPC round trips, and one poller sweep
issues a request per distinct workflow across every candidate run.

What this asserts is "the loop is turning", not "the work is succeeding". Both
loops deliberately keep spinning through a Supabase outage rather than
crash-looping, so the heartbeat is written at the top of each iteration and by
the startup connect-retry — a dependency being down is not this container
being broken.

Run as the probe:  python heartbeat.py --max-age 90
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# /tmp is the tmpfs mount these services get under `read_only: true`.
DEFAULT_PATH = Path(os.environ.get("WORKFLOWS_HEARTBEAT_PATH", "/tmp/heartbeat"))


def touch(path: Path | None = None) -> None:
    """Record that the loop is still turning.

    Never raises: a worker must not die because its liveness file could not be
    written. A write that keeps failing shows up as a stale heartbeat, which is
    what the probe is for.
    """
    target = path or DEFAULT_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def age_seconds(path: Path | None = None, now: float | None = None) -> float | None:
    """Seconds since the heartbeat was last written, or None if there is none."""
    target = path or DEFAULT_PATH
    try:
        written = target.stat().st_mtime
    except OSError:
        return None
    return (now if now is not None else time.time()) - written


def is_fresh(
    max_age: float, path: Path | None = None, now: float | None = None
) -> bool:
    """True when the loop has been round within max_age seconds."""
    age = age_seconds(path, now=now)
    return age is not None and age <= max_age


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--max-age",
        type=float,
        default=90.0,
        help="seconds before a heartbeat counts as stale (default: 90)",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help=f"heartbeat file (default: {DEFAULT_PATH})",
    )
    args = parser.parse_args(argv)

    age = age_seconds(args.path)
    if age is None:
        target = args.path or DEFAULT_PATH
        print(f"no heartbeat at {target} — the loop has not run once", file=sys.stderr)
        return 1
    if age > args.max_age:
        print(
            f"heartbeat is {age:.0f}s old (max {args.max_age:.0f}s) — "
            "the loop is running but not turning",
            file=sys.stderr,
        )
        return 1
    print(f"heartbeat {age:.0f}s old")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
