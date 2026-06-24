"""Guards for the bloom_* schema-USAGE grant mechanism (issue #333).

These are pure, no-DB checks:

1. **CI guard** — no `supabase/migrations/*.sql` may contain a raw
   `GRANT`/`REVOKE … ON SCHEMA (auth|storage)`; such statements silently no-op
   under `supabase db push` (applied as the downgraded `postgres`). Two historical
   files are allowlisted and pinned byte-stable (editing an applied migration breaks
   `db push` history validation). Would have caught #333 and #341.
2. **Anti-drift** — the helper-calling migration's grant set equals
   `supabase/grants/bloom_grant_matrix.json`.
3. **Init wiring** — the helper install is mounted into the db init layer in both
   compose files.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
GRANTS_DIR = REPO_ROOT / "supabase" / "grants"
HELPER_MIGRATION = (
    MIGRATIONS_DIR / "20260624120000_apply_bloom_schema_usage_via_helper.sql"
)
MATRIX_JSON = GRANTS_DIR / "bloom_grant_matrix.json"
HELPER_INSTALL = GRANTS_DIR / "install_bloom_grant_helper.sql"

# Historical migrations that contain raw schema grants which no-op under db push.
# They are already applied everywhere and MUST NOT be edited (that would break
# `supabase db push` history validation), so they are allowlisted and pinned. The
# authoritative path is HELPER_MIGRATION, which calls the helper instead.
ALLOWLISTED_RAW_SCHEMA_GRANTS = {
    "20260428130000_storage_grants_for_bloom_roles.sql": "475d5e28e359711edea27a043a2b921c6940bb97bed068c12329d6a7151eb18c",
    "20260519130000_add_bloom_writer_role.sql": "2478481b32532b50a8893de1f71307dcd57f05556ad6ec6611dfc01c5d979d61",
}

# GRANT/REVOKE ... ON SCHEMA (auth|storage). Note `ON SCHEMA`, not `IN SCHEMA`
# (the latter — e.g. GRANT ... ON ALL TABLES IN SCHEMA storage — is a table grant
# that survives db push and is fine).
_RAW_SCHEMA_GRANT = re.compile(
    r"\b(?:GRANT|REVOKE)\b[^;]*?\bON\s+SCHEMA\s+(?:auth|storage)\b",
    re.IGNORECASE | re.DOTALL,
)


def _strip_sql_comments(sql: str) -> str:
    """Remove -- line comments and /* */ block comments (best-effort lint)."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def test_no_raw_schema_grants_in_migrations():
    """Fail any non-allowlisted migration with a raw schema GRANT/REVOKE."""
    offenders: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in ALLOWLISTED_RAW_SCHEMA_GRANTS:
            continue
        body = _strip_sql_comments(path.read_text(encoding="utf-8"))
        match = _RAW_SCHEMA_GRANT.search(body)
        if match:
            offenders.append(f"{path.name}: {match.group(0).strip()!r}")
    assert not offenders, (
        "raw GRANT/REVOKE ... ON SCHEMA (auth|storage) silently no-ops under "
        "`supabase db push` (applied as postgres). Call the helper instead "
        "(see supabase/grants/). Offenders:\n  " + "\n  ".join(offenders)
    )


def test_helper_calling_migration_is_not_flagged():
    """The authoritative migration calls the helper, so it is not a raw grant."""
    body = _strip_sql_comments(HELPER_MIGRATION.read_text(encoding="utf-8"))
    assert _RAW_SCHEMA_GRANT.search(body) is None
    assert "bloom_grant_schema_usage(" in body


def test_allowlisted_historical_migrations_are_unedited():
    """Pinned byte-stability: applied migrations must not be edited."""
    for name, expected_sha in ALLOWLISTED_RAW_SCHEMA_GRANTS.items():
        actual = hashlib.sha256((MIGRATIONS_DIR / name).read_bytes()).hexdigest()
        assert actual == expected_sha, (
            f"{name} changed (sha256 {actual} != pinned {expected_sha}). Editing an "
            "applied migration breaks `supabase db push` history validation; add "
            "grants via the helper-calling migration instead."
        )


def _matrix_pairs() -> set[tuple[str, str]]:
    data = json.loads(MATRIX_JSON.read_text(encoding="utf-8"))["schema_usage"]
    return {(schema, role) for schema, roles in data.items() for role in roles}


def _migration_pairs() -> set[tuple[str, str]]:
    body = _strip_sql_comments(HELPER_MIGRATION.read_text(encoding="utf-8"))
    calls = re.findall(
        r"bloom_grant_schema_usage\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)", body
    )
    return set(calls)


def test_migration_grant_set_matches_matrix():
    """Anti-drift: helper-calling migration grant set == committed matrix."""
    assert _migration_pairs() == _matrix_pairs()


def test_auth_usage_not_granted_to_user_admin_agent():
    """#341 intentional gap: only bloom_writer gets auth USAGE."""
    auth_roles = {role for (schema, role) in _matrix_pairs() if schema == "auth"}
    assert auth_roles == {"bloom_writer"}, (
        "auth USAGE must be granted to bloom_writer only (#341 intentional "
        f"read-only gap for user/admin/agent); got {sorted(auth_roles)}"
    )


def test_helper_install_mounted_in_both_compose_files():
    """The init layer installs the helper on fresh init in dev and prod."""
    needle = (
        "./supabase/grants/install_bloom_grant_helper.sql:"
        "/docker-entrypoint-initdb.d/init-scripts/"
    )
    for compose in ("docker-compose.dev.yml", "docker-compose.prod.yml"):
        text = (REPO_ROOT / compose).read_text(encoding="utf-8")
        assert (
            needle in text
        ), f"helper install not mounted into init layer in {compose}"
