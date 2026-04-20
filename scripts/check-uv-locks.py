#!/usr/bin/env python3
"""Cross-platform uv lockfile drift check.

Runs `uv lock --check` in each Python service directory and exits non-zero
if any service's uv.lock is out of sync with its pyproject.toml.

Invoked by the `uv-lock-check` pre-commit hook in `.pre-commit-config.yaml`.
Replaces an earlier `bash -c` one-liner so the hook works on Windows without
requiring Git Bash (pre-commit runs this script under its managed Python env,
which is guaranteed available on every supported platform).

Can also be run manually:

    python scripts/check-uv-locks.py

Requires `uv` to be installed and on PATH. If it isn't, this script exits
with a clear install hint rather than a cryptic "uv: not found".
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

# Services to check, relative to repo root. Keep in sync with the pre-commit
# hook's `files:` filter in .pre-commit-config.yaml.
SERVICES = (
    "langchain",
    "bloommcp",
    "services/video-worker",
)

UV_INSTALL_URL = "https://docs.astral.sh/uv/getting-started/installation/"


def check_services(repo_root: Path, services: Iterable[str] = SERVICES) -> int:
    """Run `uv lock --check` in each service under `repo_root`.

    Returns 0 when every (present) service is clean, 1 when at least one
    has lockfile drift. Services without a `pyproject.toml` are skipped
    (defensive — lets partially-migrated repos still pass the hook).

    `repo_root` is injected rather than derived from `__file__` so tests
    can point it at a temp directory.
    """
    failed: list[str] = []

    for service in services:
        service_dir = repo_root / service
        if not (service_dir / "pyproject.toml").exists():
            print(f"skip  {service}: no pyproject.toml")
            continue

        print(f"check {service} ...", flush=True)
        try:
            result = subprocess.run(
                ["uv", "lock", "--check"],
                cwd=service_dir,
                # Let uv's output stream through so drift errors are visible.
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            # A stuck `uv lock --check` (network partition, stale git lock,
            # etc.) must not wedge the pre-commit hook. Record the timeout
            # and continue so one slow service doesn't block the rest.
            print(
                f"timeout: {service} exceeded 120s during `uv lock --check`",
                file=sys.stderr,
            )
            failed.append(service)
            continue
        except FileNotFoundError:
            # Race: `uv` passed shutil.which() but disappeared before exec.
            # Surface the install hint and keep going.
            print(
                f"error: {service}: `uv` not found during exec "
                f"(race between probe and run). install: {UV_INSTALL_URL}",
                file=sys.stderr,
            )
            failed.append(service)
            continue

        if result.returncode != 0:
            failed.append(service)

    if failed:
        print(
            "\nlockfile drift detected in:\n  - "
            + "\n  - ".join(failed)
            + "\nrun `uv lock` in each listed directory and commit the updated uv.lock.",
            file=sys.stderr,
        )
        return 1

    return 0


def main() -> int:
    if shutil.which("uv") is None:
        print(
            "error: `uv` is required but not installed or not on PATH.\n"
            f"       install: {UV_INSTALL_URL}",
            file=sys.stderr,
        )
        return 127

    repo_root = Path(__file__).resolve().parent.parent
    return check_services(repo_root)


if __name__ == "__main__":
    sys.exit(main())
