"""The host-exposed Postgres port must use one variable name everywhere.

`.env.dev` historically used `POSTGRES_EXTERNAL_PORT` while CI, `conftest.py`,
and `docker-compose.prod.yml` use `POSTGRES_HOST_PORT`. The split is confusing
and bug-prone (the port-conflict override touches the wrong name). This test
pins the canonical name `POSTGRES_HOST_PORT` across the tracked dev config.

(The `migrate-local` recipe is covered by ``test_makefile_migrate_local.py``;
`conftest.py` already reads `POSTGRES_HOST_PORT`.)
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Tracked files that configure the local dev stack's host port.
_FILES = [
    REPO_ROOT / "docker-compose.dev.yml",
    REPO_ROOT / ".env.dev.example",
]


def _existing_files() -> list[Path]:
    return [p for p in _FILES if p.exists()]


@pytest.mark.parametrize("path", _existing_files(), ids=lambda p: p.name)
def test_no_legacy_external_port_var(path: Path):
    text = path.read_text(encoding="utf-8")
    assert "POSTGRES_EXTERNAL_PORT" not in text, (
        f"{path.name} still references the legacy POSTGRES_EXTERNAL_PORT; "
        f"the canonical name is POSTGRES_HOST_PORT (matches CI, conftest, prod)."
    )


@pytest.mark.parametrize("path", _existing_files(), ids=lambda p: p.name)
def test_uses_canonical_host_port_var(path: Path):
    text = path.read_text(encoding="utf-8")
    assert "POSTGRES_HOST_PORT" in text, (
        f"{path.name} should configure the host port via POSTGRES_HOST_PORT."
    )
