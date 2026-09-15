"""Shape guard for the Box object backup's Dockerfile.

The image runs the nightly mirror on production's network, holding MinIO's
root credentials and the Box token, so what goes into it is pinned: the base
the sibling services use, rclone by digest, the job's modules without its
tests, and a non-root default user.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
JOB_DIR = REPO_ROOT / "scheduled-jobs" / "box-object-backup"
DOCKERFILE = JOB_DIR / "Dockerfile"
DOCKERIGNORE = JOB_DIR / ".dockerignore"
SIBLING_DOCKERFILE = REPO_ROOT / "bloomcli" / "Dockerfile"

RCLONE_IMAGE = (
    "rclone/rclone:1.75.1"
    "@sha256:45401ad7410db1d67ffdb58e19059ad20b0d8e0285a60e38bbec55cc1019c7a5"
)


def _instructions(path: Path = DOCKERFILE) -> list[tuple[str, str]]:
    """(INSTRUCTION, rest) per instruction, with line continuations joined."""
    text = re.sub(r"\\\r?\n", " ", path.read_text(encoding="utf-8"))
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        instr, _, rest = stripped.partition(" ")
        out.append((instr.upper(), rest.strip()))
    return out


def _all(instr: str) -> list[str]:
    return [rest for name, rest in _instructions() if name == instr]


def test_base_image_is_the_sibling_services_pinned_base():
    froms = _all("FROM")
    sibling = [rest for name, rest in _instructions(SIBLING_DOCKERFILE) if name == "FROM"]
    assert len(froms) == 1
    assert re.fullmatch(r"python:3\.11-slim@sha256:[a-f0-9]{64}", froms[0])
    assert froms == sibling


def test_rclone_is_copied_from_the_pinned_digest():
    copies = [rest for rest in _all("COPY") if rest.startswith("--from=")]
    assert copies == [f"--from={RCLONE_IMAGE} /usr/local/bin/rclone /usr/local/bin/rclone"]


def test_postgres_client_is_installed_without_recommends():
    runs = " ".join(_all("RUN"))
    assert "apt-get install -y --no-install-recommends postgresql-client" in runs
    assert "rm -rf /var/lib/apt/lists/*" in runs


def test_build_fails_when_either_binary_cannot_run():
    assert "rclone version && psql --version" in _all("RUN")


def test_python_writes_no_bytecode():
    env = " ".join(_all("ENV"))
    assert "PYTHONDONTWRITEBYTECODE=1" in env


def test_only_the_jobs_modules_are_copied():
    local = [rest for rest in _all("COPY") if not rest.startswith("--from=")]
    assert local == ["*.py ./"]
    assert _all("WORKDIR") == ["/app"]


@pytest.mark.parametrize("entry", ["*_test.py", "conftest.py", "__pycache__/"])
def test_dockerignore_drops_tests_and_caches(entry: str):
    lines = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    assert entry in [line.strip() for line in lines]


def test_default_user_is_not_root():
    instrs = _instructions()
    users = [i for i, (name, _) in enumerate(instrs) if name == "USER"]
    entry = [i for i, (name, _) in enumerate(instrs) if name == "ENTRYPOINT"]
    assert users and entry
    assert instrs[users[-1]][1] not in ("root", "0")
    assert users[-1] < entry[-1]


def test_entrypoint_runs_the_job_and_is_last():
    instrs = _instructions()
    assert instrs[-1] == ("ENTRYPOINT", '["python3", "/app/backup_objects.py"]')
    assert not _all("CMD")


def test_no_expose_or_healthcheck():
    assert not _all("EXPOSE")
    assert not _all("HEALTHCHECK")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
