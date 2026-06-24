"""Guards for the bloom_* schema-USAGE grant mechanism (issue #333).

Single source of truth: `supabase/grants/schema_grants.sql`, applied as
`supabase_admin` after migrations (by `make migrate-local`, CI compose-health-check,
and the manual prod/staging step). These are pure, no-DB checks:

1. **CI guard** — no `supabase/migrations/*.sql` may contain a raw
   `GRANT`/`REVOKE … ON SCHEMA (auth|storage)`; such statements silently no-op
   under `supabase db push` (applied as the downgraded `postgres`). Two historical
   files are allowlisted and pinned byte-stable. Would have caught #333 and #341.
2. **Grant set** — `schema_grants.sql` grants the expected matrix, and `auth` USAGE
   only to `bloom_writer` (#341 intentional gap).
3. **Applied post-`db push`** — `migrate-local` and the CI job apply the file as
   `supabase_admin` after migrations.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
GRANTS_FILE = REPO_ROOT / "supabase" / "grants" / "schema_grants.sql"

# Historical migrations with raw schema grants that no-op under db push. Already
# applied everywhere and MUST NOT be edited (that breaks `supabase db push` history
# validation), so they are allowlisted and pinned. Authoritative path is
# schema_grants.sql.
ALLOWLISTED_RAW_SCHEMA_GRANTS = {
    "20260428130000_storage_grants_for_bloom_roles.sql": "475d5e28e359711edea27a043a2b921c6940bb97bed068c12329d6a7151eb18c",
    "20260519130000_add_bloom_writer_role.sql": "2478481b32532b50a8893de1f71307dcd57f05556ad6ec6611dfc01c5d979d61",
}

# GRANT/REVOKE ... ON SCHEMA (auth|storage). `ON SCHEMA`, not `IN SCHEMA` (the latter
# — GRANT ... ON ALL TABLES IN SCHEMA storage — is a table grant that survives).
_RAW_SCHEMA_GRANT = re.compile(
    r"\b(?:GRANT|REVOKE)\b[^;]*?\bON\s+SCHEMA\s+(?:auth|storage)\b",
    re.IGNORECASE | re.DOTALL,
)
_GRANT_USAGE = re.compile(
    r"GRANT\s+USAGE\s+ON\s+SCHEMA\s+(\w+)\s+TO\s+([^;]+);", re.IGNORECASE
)


def _strip_sql_comments(sql: str) -> str:
    """Remove -- line comments and /* */ block comments (best-effort lint)."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def _grant_pairs(text: str) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for schema, roles in _GRANT_USAGE.findall(text):
        for role in roles.split(","):
            pairs.add((schema, role.strip()))
    return pairs


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
        "`supabase db push` (applied as postgres). Put schema grants in "
        "supabase/grants/schema_grants.sql instead. Offenders:\n  "
        + "\n  ".join(offenders)
    )


def test_allowlisted_historical_migrations_are_unedited():
    """Pinned byte-stability: applied migrations must not be edited."""
    for name, expected_sha in ALLOWLISTED_RAW_SCHEMA_GRANTS.items():
        actual = hashlib.sha256((MIGRATIONS_DIR / name).read_bytes()).hexdigest()
        assert actual == expected_sha, (
            f"{name} changed (sha256 {actual} != pinned {expected_sha}). Editing an "
            "applied migration breaks `supabase db push` history validation; add "
            "grants via supabase/grants/schema_grants.sql instead."
        )


def test_schema_grants_file_grants_expected_matrix():
    """schema_grants.sql grants storage to all four roles + auth to writer."""
    pairs = _grant_pairs(GRANTS_FILE.read_text(encoding="utf-8"))
    assert ("storage", "bloom_user") in pairs
    assert ("storage", "bloom_admin") in pairs
    assert ("storage", "bloom_agent") in pairs
    assert ("storage", "bloom_writer") in pairs
    assert ("auth", "bloom_writer") in pairs


def test_auth_usage_only_for_writer():
    """#341 intentional gap: only bloom_writer gets auth USAGE."""
    pairs = _grant_pairs(GRANTS_FILE.read_text(encoding="utf-8"))
    auth_roles = {role for (schema, role) in pairs if schema == "auth"}
    assert auth_roles == {"bloom_writer"}, (
        "auth USAGE must be granted to bloom_writer only (#341 intentional "
        f"read-only gap for user/admin/agent); got {sorted(auth_roles)}"
    )


def test_migrate_local_applies_grants_after_db_push():
    """`make migrate-local` applies schema_grants.sql, ordered after `db push`."""
    mk = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "supabase/grants/schema_grants.sql" in mk
    push = mk.find("supabase db push")
    grant = mk.find("supabase/grants/schema_grants.sql")
    assert (
        push != -1 and grant != -1 and grant > push
    ), "schema_grants.sql must be applied AFTER `supabase db push` (roles exist)"


def test_ci_compose_health_check_applies_grants():
    """CI compose-health-check applies the grants file (db push downgrades role)."""
    ci = (REPO_ROOT / ".github" / "workflows" / "pr-checks.yml").read_text(
        encoding="utf-8"
    )
    assert "supabase/grants/schema_grants.sql" in ci
