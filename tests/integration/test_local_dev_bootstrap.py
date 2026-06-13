"""Acceptance/guard test: a correctly initialised local stack has the right
roles, schemas, and a complete migration set.

This is NOT a red-first unit test — on a correctly initialised stack
(Linux/macOS/WSL2, or CI's prod compose) it passes. It fails loudly on a broken
init: a native-Windows CRLF clone (issue #124), a half-applied migration set, or
a stack brought up without `make migrate-local`. It runs in CI's
``compose-health-check`` after migrations are applied; ``auth.uid()`` is probed
with a bounded poll because CI only explicitly waits for ``storage.buckets``.

DB-substrate assertions are shared with ``scripts/check_health.py``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _check_health():
    spec = importlib.util.spec_from_file_location(
        "check_health", REPO_ROOT / "scripts" / "check_health.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_health = _check_health()


def test_required_roles_present(pg_conn):
    missing = check_health._missing_roles(
        pg_conn, check_health.REQUIRED_BASE_ROLES + check_health.REQUIRED_APP_ROLES
    )
    assert not missing, (
        f"missing roles {missing} — a clean init did not create them. On Windows "
        f"this is usually the CRLF init-script bug (#124); otherwise migrations "
        f"that create the bloom_* roles were not applied."
    )


def test_auth_schema_and_uid_present(pg_conn):
    assert check_health.wait_for_auth_uid(pg_conn), (
        "auth.uid() not present within 60s — GoTrue did not initialise the auth "
        "schema"
    )


def test_storage_schema_and_buckets_present(pg_conn):
    problems = [p for p in check_health.check_schemas(pg_conn) if "storage" in p]
    assert not problems, problems


def test_all_migrations_applied(pg_conn):
    problems = check_health.check_migrations(pg_conn)
    assert not problems, (
        f"migration set incomplete: {problems}. Run `make migrate-local`."
    )
