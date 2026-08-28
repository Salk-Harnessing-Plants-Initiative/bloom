"""`gravi_plate_videos` is written through a wrapper, not by the role directly.

`bloom_workflows` gets no INSERT or UPDATE on the table at all — it holds EXECUTE on
`record_gravi_plate_video` and nothing else. The cylinder equivalent exists because a
column-scoped role's upsert failed on every call and the caller swallowed the error, so
production held zero rows against 84,748 stored videos. These tests pin the shape that
avoids it: the wrapper exists, only the rendering service can call it, its owner is
pinned, and the grant that would let the role bypass it is never added.

Reads the migration text, so it needs no database.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"

FUNCTION = "public.record_gravi_plate_video"
TABLE = "public.gravi_plate_videos"

# Matches any GRANT naming the table, then checks the privilege list separately —
# a single pattern has to cope with `GRANT INSERT, UPDATE ON ...`, column lists, the
# optional TABLE keyword, and the optional schema prefix (the table's own migration
# writes the name unqualified).
GRANT_ON_TABLE = re.compile(
    r"GRANT\s+(?P<privs>[^;]*?)\s+ON\s+(?:TABLE\s+)?(?:public\.)?gravi_plate_videos"
    r"\s+TO\s+(?P<grantees>[\w,\s]+)",
    re.IGNORECASE,
)

# A blanket grant never names the table, so the pattern above cannot see it.
ALL_TABLES_GRANT = re.compile(
    r"GRANT\s+(?P<privs>[^;]*?)\s+ON\s+ALL\s+TABLES\s+IN\s+SCHEMA\s+public"
    r"\s+TO\s+(?P<grantees>[\w,\s]+)",
    re.IGNORECASE,
)

WRITE_VERB = re.compile(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALL)\b", re.IGNORECASE)


def _write_grants(text: str) -> list[str]:
    """Every GRANT in `text` giving bloom_workflows a write on the table."""
    found = []
    for pattern in (GRANT_ON_TABLE, ALL_TABLES_GRANT):
        for m in pattern.finditer(text):
            if "bloom_workflows" in m.group("grantees") and WRITE_VERB.search(
                m.group("privs")
            ):
                found.append(m.group(0).strip())
    return found


def _migration_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql"))
    )


def test_the_role_is_never_granted_a_direct_write():
    """The wrapper is pointless if the role can write the table around it."""
    offenders = _write_grants(_migration_text())
    assert not offenders, (
        f"bloom_workflows must write {TABLE} through {FUNCTION} only. Found: {offenders}"
    )


@pytest.mark.parametrize(
    "statement",
    [
        "GRANT INSERT ON public.gravi_plate_videos TO bloom_workflows;",
        "GRANT INSERT ON gravi_plate_videos TO bloom_workflows;",
        "GRANT INSERT, UPDATE ON gravi_plate_videos TO bloom_workflows;",
        "GRANT ALL ON TABLE gravi_plate_videos TO bloom_workflows;",
        "GRANT UPDATE (object_path) ON gravi_plate_videos TO bloom_workflows;",
        "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO bloom_workflows;",
    ],
)
def test_the_guard_matches_every_spelling_of_a_direct_write(statement):
    """The table's own migration writes the name unqualified, so the schema-qualified
    form is the least likely one to appear. Every one of these once passed the guard."""
    assert _write_grants(statement), f"the guard does not match: {statement}"


@pytest.mark.parametrize(
    "statement",
    [
        "GRANT SELECT ON gravi_plate_videos TO bloom_workflows;",
        "GRANT INSERT, UPDATE ON gravi_plate_videos TO bloom_user;",
        "GRANT EXECUTE ON FUNCTION public.record_gravi_plate_video() TO bloom_workflows;",
    ],
)
def test_the_guard_does_not_fire_on_a_read_or_another_role(statement):
    """A guard that matched these would forbid the grant this migration relies on."""
    assert not _write_grants(statement), f"the guard wrongly matches: {statement}"


def test_the_recording_wrapper_exists():
    assert f"CREATE OR REPLACE FUNCTION {FUNCTION}" in _migration_text()


def test_the_wrapper_does_not_write_the_key_columns():
    """A row must not be repointed at a different plate, so the three columns the
    conflict target matches on are never assigned."""
    body = _migration_text().split("record_gravi_plate_video", 1)[1]
    set_clause = re.search(r"DO UPDATE\s+SET(.*?);", body, re.IGNORECASE | re.DOTALL)
    assert set_clause, f"no DO UPDATE SET clause found for {FUNCTION}"
    for column in ("experiment_id", "plate_id", "wave_number"):
        assert column not in set_clause.group(1), (
            f"{column} is part of the conflict key and must not be assigned"
        )


def test_the_conflict_target_matches_the_unique_index():
    """`idx_gravi_plate_videos_unique` is on the COALESCE expression, so the ON CONFLICT
    has to name the same expression — a plain column list does not match it and raises."""
    assert re.search(
        r"ON\s+CONFLICT\s*\(\s*experiment_id\s*,\s*plate_id\s*,\s*"
        r"COALESCE\s*\(\s*wave_number\s*,\s*-1\s*\)\s*\)",
        _migration_text(),
        re.IGNORECASE,
    ), "the conflict target does not match idx_gravi_plate_videos_unique"


def test_the_wrapper_is_not_reachable_by_untrusted_roles():
    """It runs as its owner and sits under /rest/v1/rpc, so the default grants must go."""
    revoke = re.search(
        r"REVOKE\s+EXECUTE\s+ON\s+FUNCTION\s+public\.record_gravi_plate_video[^;]*;",
        _migration_text(),
        re.IGNORECASE | re.DOTALL,
    )
    assert revoke, f"EXECUTE on {FUNCTION} is never revoked from the default grantees"
    # PUBLIC first: it is the grant a freshly created function actually carries, so dropping
    # it from the list would reopen the hole while the named roles still looked covered.
    for role in ("PUBLIC", "anon", "authenticated", "service_role"):
        assert role in revoke.group(0), f"EXECUTE is never revoked from {role}"


def test_the_wrapper_is_executable_by_the_service_that_needs_it():
    """The revokes pin who may not call it; this pins who may. Without the grant the
    call is denied and nothing is ever recorded."""
    assert re.search(
        r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+public\.record_gravi_plate_video\s*\([^)]*\)\s+"
        r"TO\s+bloom_workflows",
        _migration_text(),
        re.IGNORECASE | re.DOTALL,
    ), f"bloom_workflows is never granted EXECUTE on {FUNCTION}"


def test_the_wrapper_has_a_pinned_owner():
    """SECURITY DEFINER runs as the owner, so who that is decides whether the write
    works. Left unset it is whoever applied the migration, which differs per environment."""
    assert re.search(
        r"ALTER\s+FUNCTION\s+public\.record_gravi_plate_video\s*\([^)]*\)\s+OWNER\s+TO\s+\w+",
        _migration_text(),
        re.IGNORECASE | re.DOTALL,
    ), f"{FUNCTION}'s owner is left to whoever applies the migration"


def test_the_search_path_is_pinned():
    """Without this a caller can point search_path at a table they control and have the
    owner write it."""
    definition = _migration_text().split("record_gravi_plate_video", 1)[1].split("$$")[0]
    assert re.search(r"SET\s+search_path\s*=", definition, re.IGNORECASE), (
        f"{FUNCTION} does not pin search_path"
    )
