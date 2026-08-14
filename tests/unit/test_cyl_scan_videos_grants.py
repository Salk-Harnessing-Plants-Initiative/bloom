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

# The column list is optional on purpose: `GRANT UPDATE ON ...` with no columns makes every
# column updatable, scan_id included, so a pattern that only matched column-scoped grants
# would miss the broadest possible version of the thing these tests forbid.
GRANT = re.compile(
    r"GRANT\s+(INSERT|UPDATE)\s*(?:\(([^)]*)\))?\s+ON\s+(?:TABLE\s+)?public\.cyl_scan_videos",
    re.IGNORECASE,
)
ALL_COLUMNS = {"scan_id", "path", "frames"}


def _migration_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql"))
    )


def _granted_columns() -> dict[str, set[str]]:
    """Columns granted for INSERT and for UPDATE, across every migration.

    A grant with no column list reaches every column, so it counts as all of them.
    """
    granted: dict[str, set[str]] = {"insert": set(), "update": set()}
    for verb, columns in GRANT.findall(_migration_text()):
        granted[verb.lower()] |= (
            {c.strip() for c in columns.split(",")} if columns.strip() else ALL_COLUMNS
        )
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
    for role in ("anon", "authenticated"):
        assert role in revoke.group(0)
