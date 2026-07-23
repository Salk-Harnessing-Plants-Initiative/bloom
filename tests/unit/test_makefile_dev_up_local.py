"""`make dev-up-local` (#478) must delegate to `dev-up`, never duplicate it.

A duplicated recipe body could silently drift from `dev-up` (new prerequisite,
changed install logic, new step) with nothing enforcing parity. This pins: the
target exists; its recipe sets BLOOM_STORAGE_BACKEND=local and invokes
`$(MAKE) dev-up` rather than its own `docker compose` line; and a dry-run
(`make -n`) actually resolves that way.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"


def _recipe(target: str) -> str:
    lines = MAKEFILE.read_text(encoding="utf-8").splitlines()
    out, capturing = [], False
    for line in lines:
        if line.startswith(f"{target}:"):
            capturing = True
            continue
        if capturing:
            if line and line[0] not in (" ", "\t"):
                break
            out.append(line)
    return "\n".join(out)


def _real_targets() -> set[str]:
    import re

    return {
        m.group(1)
        for line in MAKEFILE.read_text(encoding="utf-8").splitlines()
        if (m := re.match(r"^([a-zA-Z0-9_][a-zA-Z0-9_-]*):", line))
    }


def test_dev_up_local_target_exists():
    assert "dev-up-local" in _real_targets(), "a `make dev-up-local` target must exist"


def test_dev_up_local_delegates_to_dev_up_without_duplicating_it():
    recipe = _recipe("dev-up-local")
    assert "BLOOM_STORAGE_BACKEND=local" in recipe, (
        "dev-up-local must set BLOOM_STORAGE_BACKEND=local for its invocation"
    )
    assert "$(MAKE) dev-up" in recipe, (
        "dev-up-local must delegate to dev-up via $(MAKE), not reimplement it"
    )
    assert "docker compose" not in recipe, (
        "dev-up-local must not duplicate dev-up's own `docker compose up` line — "
        "that would let the two recipes silently drift apart"
    )


def test_dev_up_local_listed_in_help():
    help_recipe = _recipe("help")
    assert "dev-up-local" in help_recipe, (
        "`make dev-up-local` must be discoverable via `make help`"
    )


@pytest.mark.skipif(shutil.which("make") is None, reason="make not on PATH")
def test_dev_up_local_dry_run_prefixes_backend_before_delegating():
    """Behavioral counterpart: `make -n` must actually resolve to the expected
    shell-env-prefixed delegation, not just contain the right substrings."""
    result = subprocess.run(
        ["make", "-n", "dev-up-local"],
        cwd=REPO_ROOT,
        env={**os.environ},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"make -n dev-up-local failed: {result.stderr}"
    assert "BLOOM_STORAGE_BACKEND=local make dev-up" in result.stdout, (
        f"expected the delegated invocation line in dry-run output; got:\n"
        f"{result.stdout}"
    )
