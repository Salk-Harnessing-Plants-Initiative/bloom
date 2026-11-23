"""Every `docker compose ... up` in the Makefile removes containers no longer in the
compose file, as deploy.yml does, so a retired service can't fail `make check`."""

from pathlib import Path

MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"


def _up_lines() -> list[str]:
    lines = MAKEFILE.read_text().splitlines()
    return [line for line in lines if "docker compose" in line and " up -d" in line]


def test_the_makefile_starts_stacks():
    assert len(_up_lines()) >= 4


def test_every_up_removes_orphans():
    missing = [line.strip() for line in _up_lines() if "--remove-orphans" not in line]
    assert not missing, f"`up -d` without --remove-orphans: {missing}"
