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
import re
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


def test_recipe_preflights_env_dev_and_password():
    """A missing .env.dev or empty POSTGRES_PASSWORD must fail with a clear
    message, not masquerade as a 'storage.buckets not ready' timeout."""
    recipe = _migrate_local_recipe()
    assert "-f .env.dev" in recipe, "migrate-local should preflight that .env.dev exists"
    assert "-z" in recipe and "PG_PASSWORD" in recipe, (
        "migrate-local should fail fast if POSTGRES_PASSWORD is empty"
    )


def _verify_dev_recipe() -> str:
    lines = MAKEFILE.read_text(encoding="utf-8").splitlines()
    out, capturing = [], False
    for line in lines:
        if line.startswith("verify-dev:"):
            capturing = True
            continue
        if capturing:
            if line and line[0] not in (" ", "\t"):
                break
            out.append(line)
    return "\n".join(out)


def test_verify_dev_fails_fast_if_db_never_ready():
    """verify-dev's db-dev wait loop must fail (exit 1) on timeout instead of
    silently continuing into migrate-local/check with a not-ready DB."""
    recipe = _verify_dev_recipe()
    assert "pg_isready" in recipe
    assert "exit 1" in recipe, "verify-dev must fail fast if db-dev never accepts connections"


def test_verify_dev_rm_is_anchored_to_repo_root():
    """verify-dev's destructive bind-mount wipe must be anchored to the repo root
    ($(CURDIR)), not a bare relative `volumes/db/data` that resolves wrong (and
    could delete the wrong thing) when `make` is invoked from another CWD."""
    recipe = _verify_dev_recipe()
    assert "rm -rf volumes/db/data" not in recipe, (
        "verify-dev must not `rm -rf` a bare relative volumes/db/data path"
    )
    assert "$(CURDIR)/volumes/db/data" in recipe, (
        "verify-dev should anchor the db-data wipe to $(CURDIR)"
    )


def test_recipe_waits_for_storage_schema_before_push():
    """storage-api provisions storage.buckets (incl. the `public` column) at
    runtime; bucket migrations INSERT into it. migrate-local must wait for that
    before `supabase db push`, or running it right after `make dev-up` races
    storage-api and fails with SQLSTATE 42703."""
    recipe = _migrate_local_recipe()
    assert "supabase db push" in recipe
    pre_push = recipe[: recipe.index("supabase db push")]
    assert "storage" in pre_push and "buckets" in pre_push, (
        "migrate-local must poll for the storage schema (storage.buckets) before "
        "pushing migrations"
    )


def test_init_target_has_check_uv_preflight():
    """`make init` runs `uv run ...`; it must declare the check-uv prerequisite so
    a missing uv gives the actionable install hint other targets provide."""
    text = MAKEFILE.read_text(encoding="utf-8")
    m = re.search(r"^init:[ \t]*(.*)$", text, re.MULTILINE)
    assert m, "no `init:` target found"
    assert "check-uv" in m.group(1), "`init` target must depend on `check-uv`"


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
