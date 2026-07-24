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
    shell-env-prefixed delegation, not just contain the right substrings.

    Asserts the two meaningful substrings independently (var assignment,
    delegated target) rather than one exact concatenated line — GNU Make's
    exact `-n` echo formatting isn't a documented, cross-platform-stable
    contract, so pinning the precise joined string would be fragile on a
    non-GNU-Make/non-Linux `make` even though the underlying recipe is fine.
    """
    result = subprocess.run(
        ["make", "-n", "dev-up-local"],
        cwd=REPO_ROOT,
        env={**os.environ},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"make -n dev-up-local failed: {result.stderr}"
    assert "BLOOM_STORAGE_BACKEND=local" in result.stdout, (
        f"expected the var assignment in dry-run output; got:\n{result.stdout}"
    )
    assert "dev-up" in result.stdout, (
        f"expected the delegated dev-up invocation in dry-run output; got:\n"
        f"{result.stdout}"
    )


def _dev_up_precheck_shell_snippet() -> str:
    """Extract dev-up's BLOOM_STORAGE_BACKEND pre-check (the lines before the
    doctor preflight), converting Make's `$$` escaping to a literal `$` so it
    can run directly under `sh` — a portable stand-in for actually invoking
    `make dev-up` (too heavy: doctor.sh + a full `docker compose up --build`)."""
    recipe = _recipe("dev-up")
    lines = []
    for line in recipe.splitlines():
        if "scripts/doctor.sh" in line:
            break
        lines.append(line.lstrip("\t@ "))
    return "\n".join(lines).replace("$$", "$")


@pytest.mark.parametrize(
    "env_dev_contents,shell_value,expect_note",
    [
        ("BLOOM_STORAGE_BACKEND=local\n", None, True),
        ("BLOOM_STORAGE_BACKEND=\n", "local", True),
        ("BLOOM_STORAGE_BACKEND=\n", None, False),
        ("", None, False),
    ],
    ids=["from-.env.dev", "from-shell-env", "empty-both", "no-.env.dev-key"],
)
def test_dev_up_warns_when_backend_is_preset(
    tmp_path, env_dev_contents, shell_value, expect_note
):
    """dev-up must print a foreground NOTE when BLOOM_STORAGE_BACKEND resolves
    non-empty from either the shell environment or .env.dev, BEFORE the doctor
    preflight/build steps — otherwise a leftover shell export (or a value left
    set in .env.dev) silently redirects a plain `make dev-up` into fully-local
    mode with no visible cue (round-2 PR #513 review finding)."""
    (tmp_path / ".env.dev").write_text(env_dev_contents)
    env = {**os.environ}
    env.pop("BLOOM_STORAGE_BACKEND", None)
    if shell_value is not None:
        env["BLOOM_STORAGE_BACKEND"] = shell_value

    result = subprocess.run(
        ["sh", "-c", _dev_up_precheck_shell_snippet()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, f"precheck snippet errored: {result.stderr}"
    if expect_note:
        assert "NOTE: BLOOM_STORAGE_BACKEND=" in result.stdout, (
            f"expected a warning NOTE; got stdout={result.stdout!r}"
        )
    else:
        assert "NOTE:" not in result.stdout, (
            f"expected no warning NOTE; got stdout={result.stdout!r}"
        )
