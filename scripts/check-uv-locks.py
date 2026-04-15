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

# Services to check, relative to repo root. Keep in sync with the pre-commit
# hook's `files:` filter in .pre-commit-config.yaml.
SERVICES = (
    "langchain",
    "bloommcp",
    "services/video-worker",
)

UV_INSTALL_URL = "https://docs.astral.sh/uv/getting-started/installation/"


def main() -> int:
    if shutil.which("uv") is None:
        print(
            "error: `uv` is required but not installed or not on PATH.\n"
            f"       install: {UV_INSTALL_URL}",
            file=sys.stderr,
        )
        return 127

    repo_root = Path(__file__).resolve().parent.parent
    failed: list[str] = []

    for service in SERVICES:
        service_dir = repo_root / service
        if not (service_dir / "pyproject.toml").exists():
            # Skip services that haven't been migrated yet (defensive).
            print(f"skip  {service}: no pyproject.toml")
            continue

        print(f"check {service} ...", flush=True)
        result = subprocess.run(
            ["uv", "lock", "--check"],
            cwd=service_dir,
            # Let uv's output stream through so drift errors are visible.
        )
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


if __name__ == "__main__":
    sys.exit(main())