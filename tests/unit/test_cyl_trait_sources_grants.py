"""`cyl_trait_sources` stays read-only to bloom_workflows, and column-scoped.

20260916120000 grants that role `SELECT (idempotency_key)` so bloomctl can tell whether a
delivery was already ingested before uploading its blobs (talmolab/sleap-roots-pipeline#76).
The grant is deliberately narrow, and two ways of widening it would be silent:

- Any write verb on the table would let write-back bypass insert_cyl_result_envelope, which
  `cyl-trait-writeback` specs as the sole writer of the trait tables.
- A column-less `GRANT SELECT ON public.cyl_trait_sources` reaches EVERY column, including ones
  this role has no business reading, while still looking like a tightening in review.

These pin both from the migration text alone, so they run in the `python-audit` job without a
database. Pattern follows test_cyl_scan_videos_grants.py; verbs and column lists are matched
loosely on purpose, because the broadest forms are the ones worth catching.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"

_TABLE = r"(?:TABLE\s+)?public\.cyl_trait_sources"

WRITE_GRANT = re.compile(
    r"GRANT\s+(INSERT|UPDATE|DELETE|ALL(?:\s+PRIVILEGES)?)\s*(?:\([^)]*\))?\s+ON\s+"
    + _TABLE
    + r"\s+TO\s+([^;]+)",
    re.IGNORECASE,
)

SELECT_GRANT = re.compile(
    r"GRANT\s+SELECT\s*(\([^)]*\))?\s+ON\s+" + _TABLE + r"\s+TO\s+([^;]+)",
    re.IGNORECASE,
)


def _migration_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql"))
    )


def test_bloom_workflows_is_never_granted_a_write_on_cyl_trait_sources():
    offenders = [
        (verb, grantees.strip())
        for verb, grantees in WRITE_GRANT.findall(_migration_text())
        if "bloom_workflows" in grantees
    ]
    assert offenders == [], (
        "bloom_workflows must stay read-only on cyl_trait_sources — the write-back RPC is the "
        f"sole writer of the trait tables. Found: {offenders}"
    )


def test_bloom_workflows_select_grants_stay_column_scoped():
    """A column-less SELECT would silently reach every column and void the spec's
    'Other columns remain ungranted' guarantee."""
    unscoped = [
        grantees.strip()
        for columns, grantees in SELECT_GRANT.findall(_migration_text())
        if "bloom_workflows" in grantees and not (columns or "").strip("() ")
    ]
    assert unscoped == [], (
        "a GRANT SELECT on cyl_trait_sources with no column list reaches every column; "
        f"keep bloom_workflows column-scoped. Found grants to: {unscoped}"
    )


def test_the_idempotency_key_grant_exists():
    """The read path bloomctl's idempotency gate depends on. Without it the gate fails open,
    silently, and the #76 collision comes back."""
    columns = {
        c.strip()
        for cols, grantees in SELECT_GRANT.findall(_migration_text())
        if "bloom_workflows" in grantees
        for c in (cols or "").strip("()").split(",")
    }
    assert "idempotency_key" in columns
