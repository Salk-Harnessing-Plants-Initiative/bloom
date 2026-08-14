"""An upsert needs UPDATE on every column it writes, including the conflict key.

`_record_video` writes `cyl_scan_videos` with `upsert(on_conflict="scan_id")`. PostgREST turns
that into `ON CONFLICT (scan_id) DO UPDATE SET scan_id = ..., path = ..., frames = ...`, and
Postgres checks SET-clause column privileges statically — so a column granted on INSERT but not
on UPDATE fails the statement outright, conflict or not.

That is what happened: `scan_id` was granted on INSERT only, every write was refused with 42501,
and `_record_video` swallowed the error. Production held zero rows against 84,748 stored videos
until the table was backfilled by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"

GRANT = re.compile(
    r"GRANT\s+(INSERT|UPDATE)\s*\(([^)]*)\)\s+ON\s+public\.cyl_scan_videos",
    re.IGNORECASE,
)


def _granted_columns() -> dict[str, set[str]]:
    """Columns granted for INSERT and for UPDATE, across every migration."""
    granted: dict[str, set[str]] = {"insert": set(), "update": set()}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        for verb, columns in GRANT.findall(path.read_text(encoding="utf-8")):
            granted[verb.lower()] |= {c.strip() for c in columns.split(",")}
    return granted


def test_every_insertable_column_is_also_updatable():
    granted = _granted_columns()

    assert granted["insert"], "no column-level INSERT grant found for cyl_scan_videos"
    missing = granted["insert"] - granted["update"]
    assert not missing, (
        f"{sorted(missing)} can be inserted but not updated, so the upsert in _record_video "
        f"is refused with 42501 — and it swallows the error, so nothing is recorded"
    )


def test_the_conflict_key_is_updatable():
    """`scan_id` is the `on_conflict` target, so PostgREST always writes it in the SET clause."""
    assert "scan_id" in _granted_columns()["update"]
