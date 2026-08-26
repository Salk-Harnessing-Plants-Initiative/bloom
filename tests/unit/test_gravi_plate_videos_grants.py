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

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"

FUNCTION = "public.record_gravi_plate_video"
TABLE = "public.gravi_plate_videos"

# `GRANT UPDATE ON ...` with no column list, and `GRANT ALL`, each reach every column — so a
# pattern matching only column-scoped grants would miss the broadest form of what is forbidden.
WRITE_GRANT = re.compile(
    r"GRANT\s+(INSERT|UPDATE|DELETE|ALL(?:\s+PRIVILEGES)?)\s*(?:\([^)]*\))?\s+ON\s+"
    r"(?:TABLE\s+)?public\.gravi_plate_videos\s+TO\s+([\w,\s]+)",
    re.IGNORECASE,
)


def _migration_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql"))
    )


def test_the_role_is_never_granted_a_direct_write():
    """The wrapper is pointless if the role can write the table around it."""
    offenders = [
        f"GRANT {verb.strip()} TO {grantees.strip()}"
        for verb, grantees in WRITE_GRANT.findall(_migration_text())
        if "bloom_workflows" in grantees
    ]
    assert not offenders, (
        f"bloom_workflows must write {TABLE} through {FUNCTION} only. Found: {offenders}"
    )


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
