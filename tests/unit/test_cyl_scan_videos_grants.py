"""`cyl_scan_videos` is recorded through a wrapper, not a client-side upsert.

`bloom_workflows` holds INSERT on (scan_id, path, frames) but UPDATE on (path, frames) only —
scan_id is deliberately immutable, so a row cannot be repointed at another scan. PostgREST's
`resolution=merge-duplicates` builds its DO UPDATE from every key in the payload, including the
conflict key, and Postgres checks SET-clause privileges statically. An upsert therefore fails
with 42501 whether or not a row conflicts, and `_record_video` swallows the error — which is
how production came to hold zero rows against 84,748 stored videos.

`record_cyl_scan_video` is the fix: it matches on scan_id without writing it. These tests pin
both halves — the wrapper exists, and the grant that would paper over the problem does not.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"

# Both the column list and the verb are matched loosely on purpose. `GRANT UPDATE ON ...` with
# no columns, and `GRANT ALL ...`, each make every column updatable — scan_id included — so a
# pattern that only caught column-scoped INSERT/UPDATE would miss the broadest versions of the
# very thing these tests forbid.
GRANT = re.compile(
    r"GRANT\s+(INSERT|UPDATE|ALL(?:\s+PRIVILEGES)?)\s*(?:\(([^)]*)\))?\s+ON\s+"
    r"(?:TABLE\s+)?public\.cyl_scan_videos",
    re.IGNORECASE,
)
ALL_COLUMNS = {"scan_id", "path", "frames"}


def _migration_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql"))
    )


def _granted_columns() -> dict[str, set[str]]:
    """Columns granted for INSERT and for UPDATE, across every migration.

    A grant with no column list reaches every column, so it counts as all of them, and ALL
    counts as both verbs.
    """
    granted: dict[str, set[str]] = {"insert": set(), "update": set()}
    for verb, columns in GRANT.findall(_migration_text()):
        reached = {c.strip() for c in columns.split(",")} if columns.strip() else ALL_COLUMNS
        verbs = ("insert", "update") if verb.lower().startswith("all") else (verb.lower(),)
        for v in verbs:
            granted[v] |= reached
    return granted


def test_scan_id_is_never_made_updatable():
    """Granting UPDATE on the conflict key is the fix this wrapper exists to avoid."""
    assert "scan_id" not in _granted_columns()["update"], (
        "scan_id must stay immutable — record videos through record_cyl_scan_video "
        "instead of granting UPDATE on the conflict key"
    )


def test_the_recording_wrapper_exists():
    text = _migration_text()
    assert "CREATE OR REPLACE FUNCTION public.record_cyl_scan_video" in text


def test_the_wrapper_does_not_write_the_conflict_key():
    """`DO UPDATE SET` must touch path and frames only — never scan_id."""
    text = _migration_text()
    body = text.split("record_cyl_scan_video", 1)[1]
    set_clause = re.search(r"DO UPDATE\s+SET(.*?);", body, re.IGNORECASE | re.DOTALL)
    assert set_clause, "no DO UPDATE SET clause found for record_cyl_scan_video"
    assert "scan_id" not in set_clause.group(1)


def test_the_wrapper_is_not_reachable_by_untrusted_roles():
    """It is SECURITY DEFINER on a table those roles cannot write, and sits under /rest/v1/rpc."""
    text = _migration_text()
    revoke = re.search(
        r"REVOKE\s+EXECUTE\s+ON\s+FUNCTION\s+public\.record_cyl_scan_video[^;]*;",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    assert revoke, "EXECUTE on record_cyl_scan_video is never revoked from the default grantees"
    # PUBLIC first: it is the grant a freshly created function actually carries, so dropping it
    # from the list would reopen the hole while the named roles still looked covered.
    for role in ("PUBLIC", "anon", "authenticated", "service_role"):
        assert role in revoke.group(0), f"EXECUTE is never revoked from {role}"


def test_the_wrapper_is_executable_by_the_service_that_needs_it():
    """Without this grant the call is denied, `_record_video` swallows it, and nothing records.

    The revoke tests assert who may not call it; this asserts who may. Losing the grant would
    restore the exact production state — videos stored, no rows — with a green suite.
    """
    assert re.search(
        r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+public\.record_cyl_scan_video\s*\([^)]*\)\s+"
        r"TO\s+bloom_workflows",
        _migration_text(),
        re.IGNORECASE,
    ), "bloom_workflows is never granted EXECUTE on record_cyl_scan_video"


def test_the_wrapper_has_a_pinned_owner():
    """A DEFINER owned by the wrong role cannot write the row — and the failure is swallowed.

    cyl_scan_videos has RLS with policies for bloom_workflows only, so an owner without
    BYPASSRLS or its own grants raises on the insert, `_record_video` logs it, and nothing is
    recorded: the exact silent failure this wrapper was written to end.
    """
    assert re.search(
        r"ALTER\s+FUNCTION\s+public\.record_cyl_scan_video\s*\([^)]*\)\s+OWNER\s+TO\s+\w+",
        _migration_text(),
        re.IGNORECASE,
    ), "record_cyl_scan_video's owner is left to whoever applies the migration"
