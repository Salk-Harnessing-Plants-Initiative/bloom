"""`make migrate-local` must connect with the *configured* credentials/port.

The old recipe hardcoded ``@127.0.0.1:5432/postgres`` with a
``POSTGRES_PASSWORD:-postgres`` fallback. That breaks with a generated local
password and, worse, under the documented ``POSTGRES_HOST_PORT=5433`` override it
would silently migrate the *wrong* Postgres on 5432 — a data-integrity footgun.
It must also pass ``--debug`` so ``sslmode=disable`` is honoured on the TLS-less
local DB (supabase-cli #4839), mirroring CI.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"


def _migrate_local_recipe() -> str:
    """The body of the migrate-local rule (lines until the next top-level rule)."""
    lines = MAKEFILE.read_text(encoding="utf-8").splitlines()
    out, capturing = [], False
    for line in lines:
        if line.startswith("migrate-local:"):
            capturing = True
            continue
        if capturing:
            # A recipe line is tab-indented; a new top-level target/blank/.PHONY ends it.
            if line and not line.startswith("\t") and not line.startswith(" "):
                break
            out.append(line)
    return "\n".join(out)


def test_recipe_passes_debug():
    assert "--debug" in _migrate_local_recipe(), (
        "migrate-local must pass --debug (sslmode=disable workaround, supabase-cli #4839)"
    )


def test_recipe_uses_host_port_var_not_hardcoded_5432():
    recipe = _migrate_local_recipe()
    assert "POSTGRES_HOST_PORT" in recipe, "migrate-local must use POSTGRES_HOST_PORT"
    assert "@127.0.0.1:5432/postgres" not in recipe, (
        "migrate-local must not hardcode :5432/postgres — build the URL from the "
        "configured port/db"
    )


def test_recipe_does_not_hardcode_postgres_password_fallback():
    recipe = _migrate_local_recipe()
    assert "POSTGRES_PASSWORD:-postgres" not in recipe, (
        "migrate-local must use the configured (generated) password, not a "
        "':-postgres' fallback that masks it"
    )


def test_recipe_sources_env_dev():
    assert ".env.dev" in _migrate_local_recipe(), (
        "migrate-local should source credentials from .env.dev"
    )


@pytest.mark.skipif(shutil.which("make") is None, reason="make not on PATH")
def test_port_override_changes_db_url():
    """Behavioural: POSTGRES_HOST_PORT=5433 must reach the db-url (not 5432)."""
    env = {**os.environ, "POSTGRES_HOST_PORT": "5433"}
    result = subprocess.run(
        ["make", "-n", "migrate-local"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"`make -n` unavailable here: {result.stderr.strip()[:200]}")
    out = result.stdout
    assert ":5433/" in out, "POSTGRES_HOST_PORT=5433 must appear in the db-url"
    assert ":5432/" not in out, "the 5432 default must not survive the override"
