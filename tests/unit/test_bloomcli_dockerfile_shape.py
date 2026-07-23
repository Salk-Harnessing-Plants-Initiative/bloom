"""Shape guard for bloomcli's Dockerfile.

bloomcli/Dockerfile is a CLI image (not a long-running service): it should build
from a digest-pinned base with no native build toolchain, run as a non-root
user, and exec straight into `bloomctl` with no service-shaped instructions
(EXPOSE/HEALTHCHECK). This mirrors bloommcp/Dockerfile's shape minus the
apt-get block bloomctl's pure-Python dependency set doesn't need.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
DOCKERFILE = REPO_ROOT / "bloomcli" / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / "bloomcli" / ".dockerignore"
UV_LOCK = REPO_ROOT / "bloomcli" / "uv.lock"


def _lines() -> list[str]:
    return DOCKERFILE.read_text(encoding="utf-8").splitlines()


def _instructions() -> list[tuple[str, str]]:
    """Return (INSTRUCTION, rest-of-line) for each non-comment, non-blank line."""
    out = []
    for line in _lines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        instr = parts[0].upper()
        rest = parts[1] if len(parts) > 1 else ""
        out.append((instr, rest))
    return out


def test_base_image_is_digest_pinned():
    instrs = _instructions()
    from_lines = [rest for instr, rest in instrs if instr == "FROM"]
    assert from_lines, "Dockerfile has no FROM instruction"
    assert re.match(r"^python:3\.11-slim@sha256:[a-f0-9]{64}", from_lines[0])


def test_uv_binary_is_copied_in_digest_pinned():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(
        r"COPY --from=ghcr\.io/astral-sh/uv:[^\s@]+@sha256:[a-f0-9]{64}", text
    ), "uv binary must be copied from a digest-pinned ghcr.io/astral-sh/uv image"


def test_no_apt_get_install():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "apt-get install" not in text


def test_non_root_user_before_entrypoint():
    instrs = _instructions()
    user_indices = [i for i, (instr, rest) in enumerate(instrs) if instr == "USER"]
    entrypoint_indices = [i for i, (instr, _) in enumerate(instrs) if instr == "ENTRYPOINT"]
    assert user_indices, "Dockerfile has no USER instruction"
    assert entrypoint_indices, "Dockerfile has no ENTRYPOINT instruction"
    last_user = [rest for instr, rest in instrs if instr == "USER"][-1].strip()
    assert last_user not in ("root", "0"), "must not run as root"
    assert user_indices[-1] < entrypoint_indices[-1], "USER must precede ENTRYPOINT"


def test_entrypoint_is_exec_form_bloomctl_no_cmd():
    instrs = _instructions()
    entrypoints = [rest.strip() for instr, rest in instrs if instr == "ENTRYPOINT"]
    assert len(entrypoints) == 1
    assert entrypoints[0] == '["bloomctl"]'
    assert not any(instr == "CMD" for instr, _ in instrs), "no CMD alongside ENTRYPOINT"


def test_entrypoint_is_the_final_instruction():
    instrs = _instructions()
    assert instrs[-1][0] == "ENTRYPOINT"


def test_no_expose_or_healthcheck():
    instrs = _instructions()
    assert not any(instr == "EXPOSE" for instr, _ in instrs)
    assert not any(instr == "HEALTHCHECK" for instr, _ in instrs)


@pytest.mark.parametrize(
    "pattern",
    ["tests/?", r"__pycache__/?", r"\.venv/?", "dist/?"],
    ids=["tests", "__pycache__", ".venv", "dist"],
)
def test_dockerignore_excludes_expected_entries(pattern: str):
    assert DOCKERIGNORE.exists()
    text = DOCKERIGNORE.read_text(encoding="utf-8")
    assert re.search(rf"^{pattern}\s*$", text, re.MULTILINE), (
        f"bloomcli/.dockerignore is missing an entry matching {pattern!r}"
    )


def test_uv_lock_stays_git_tracked():
    """Regression guard for a bug that already happened once: bloomcli/uv.lock
    was never committed to this repo at all (caught by a blanket .gitignore
    `uv.lock` rule) — `uv sync --frozen` in the Dockerfile would fail on a
    fresh `actions/checkout` in CI, since untracked/gitignored files aren't
    restored. Nothing previously guarded against this silently recurring
    (e.g. via a future `git rm --cached bloomcli/uv.lock` or a stricter
    .gitignore rule).
    """
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(UV_LOCK)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "bloomcli/uv.lock is not tracked by git — this would break the "
        "Dockerfile's `uv sync --frozen` on a fresh CI checkout. "
        f"stderr: {result.stderr}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
