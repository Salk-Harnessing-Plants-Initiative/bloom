"""`cyl_trait_sources` stays read-only to bloom_workflows, and column-scoped.

20260916120000 grants that role `SELECT (idempotency_key)` so bloomctl can tell whether a
delivery was already ingested before uploading its blobs (talmolab/sleap-roots-pipeline#76).
The grant is deliberately narrow, and two ways of widening it would be silent:

- Any write verb on the table would let write-back bypass insert_cyl_result_envelope, which
  `cyl-trait-writeback` specs as the sole writer of the trait tables.
- A column-less `GRANT SELECT` reaches EVERY column, including ones this role has no business
  reading, while still looking like a tightening in review.

A first version of this guard anchored the verb immediately after `GRANT` and required the
`public.` prefix. A review demonstrated 14 bypasses against it, every one with precedent in
this repo: `GRANT SELECT, UPDATE ON …` (the combined verb list at `20260414002000:48` and
`20260519130000:33`), an unqualified table name (the house style of `20240730223154`, which
creates this very table), `ON ALL TABLES IN SCHEMA public`, `GRANT bloom_agent TO
bloom_workflows` (role membership), `TO PUBLIC`, and `TRUNCATE`/`REFERENCES`, which were not in
the verb list at all. This version uses the "anything between GRANT and the table" form that
`test_schema_usage_grants.py` uses, and `BYPASS_FORMS` below pins every one of those shapes so
the guard is itself tested.

The ACL-level counterpart is `tests/integration/test_cyl_trait_source_idem_read.py`, which
asserts the real privilege surface with `has_column_privilege`/`has_table_privilege` against a
migrated database. That is the stronger check — it resolves PUBLIC, role membership and grants
made anywhere. This file is the no-database guard that runs in `python-audit`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
# Both trees are applied to real databases: migrations via `supabase db push`, and
# supabase/grants/schema_grants.sql piped into psql as supabase_admin by deploy.yml. A grant
# added to the latter runs with MORE authority, so scanning only migrations left a hole.
SOURCES = (REPO_ROOT / "supabase" / "migrations", REPO_ROOT / "supabase" / "grants")

ROLE = "bloom_workflows"
# Optional schema qualification, optional quoting: `public.cyl_trait_sources`,
# `public."cyl_trait_sources"`, `TABLE public.cyl_trait_sources`, or bare `cyl_trait_sources`.
_TABLE = r"(?:TABLE\s+)?(?:\"?public\"?\s*\.\s*)?\"?cyl_trait_sources\"?"

# Anything between GRANT and the table, so a combined verb list cannot slip past.
GRANT_ON_TABLE = re.compile(
    r"\bGRANT\b(?P<verbs>[^;]*?)\bON\s+" + _TABLE + r"\b(?P<rest>[^;]*)",
    re.IGNORECASE | re.DOTALL,
)
# `GRANT SELECT ON ALL TABLES IN SCHEMA public TO bloom_workflows` reaches this table too.
GRANT_ALL_TABLES = re.compile(
    r"\bGRANT\b[^;]*?\bON\s+ALL\s+TABLES\s+IN\s+SCHEMA\s+public\b(?P<rest>[^;]*)",
    re.IGNORECASE | re.DOTALL,
)
# `GRANT some_role TO bloom_workflows` inherits whatever that role holds.
GRANT_ROLE_MEMBERSHIP = re.compile(
    r"\bGRANT\s+(?!ALL\b|SELECT\b|INSERT\b|UPDATE\b|DELETE\b|TRUNCATE\b|REFERENCES\b|TRIGGER\b|USAGE\b|EXECUTE\b)"
    r"[A-Za-z_][A-Za-z0-9_]*\s+TO\s+[^;]*\b" + ROLE + r"\b",
    re.IGNORECASE,
)

WRITE_VERBS = ("insert", "update", "delete", "truncate", "references", "trigger", "all")


def _sql_text() -> str:
    parts = []
    for root in SOURCES:
        if root.is_dir():
            parts.extend(p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.sql")))
    return "\n".join(parts)


def _write_grants(sql: str) -> list[str]:
    """Statements granting a write verb on this table to bloom_workflows (or PUBLIC)."""
    offenders = []
    for m in GRANT_ON_TABLE.finditer(sql):
        verbs, rest = m.group("verbs").lower(), m.group("rest")
        if not any(v in verbs for v in WRITE_VERBS):
            continue
        if re.search(rf"\b{ROLE}\b", rest, re.IGNORECASE) or re.search(
            r"\bTO\s+PUBLIC\b", rest, re.IGNORECASE
        ):
            offenders.append(m.group(0).strip())
    return offenders


def _unscoped_selects(sql: str) -> list[str]:
    """Column-less SELECT grants on this table reaching bloom_workflows (or PUBLIC)."""
    offenders = []
    for m in GRANT_ON_TABLE.finditer(sql):
        verbs, rest = m.group("verbs"), m.group("rest")
        if "select" not in verbs.lower() or "(" in verbs:
            continue
        if re.search(rf"\b{ROLE}\b", rest, re.IGNORECASE) or re.search(
            r"\bTO\s+PUBLIC\b", rest, re.IGNORECASE
        ):
            offenders.append(m.group(0).strip())
    return offenders


REVOKE_ON_TABLE = re.compile(
    r"\bREVOKE\b(?P<verbs>[^;]*?)\bON\s+" + _TABLE + r"\b(?P<rest>[^;]*)",
    re.IGNORECASE | re.DOTALL,
)


def _bare_revokes(sql: str) -> list[str]:
    """Column-less REVOKEs on this table aimed at bloom_workflows.

    A bare `REVOKE SELECT ON public.cyl_trait_sources` wipes ALL four columns' ACLs, not
    just the one this change added — taking `(id, metadata)` with it.
    """
    return [
        m.group(0).strip()
        for m in REVOKE_ON_TABLE.finditer(sql)
        if "(" not in m.group("verbs")
        and re.search(rf"\b{ROLE}\b", m.group("rest"), re.IGNORECASE)
    ]


def test_bloom_workflows_is_never_granted_a_write_on_cyl_trait_sources():
    offenders = _write_grants(_sql_text())
    assert offenders == [], (
        "bloom_workflows must stay read-only on cyl_trait_sources — the write-back RPC is the "
        f"sole writer of the trait tables. Found: {offenders}"
    )


def test_bloom_workflows_select_grants_stay_column_scoped():
    offenders = _unscoped_selects(_sql_text())
    assert offenders == [], (
        "a GRANT SELECT on cyl_trait_sources with no column list reaches every column; "
        f"keep bloom_workflows column-scoped. Found: {offenders}"
    )


def test_no_blanket_schema_grant_reaches_the_table():
    sql = _sql_text()
    offenders = [
        m.group(0).strip()
        for m in GRANT_ALL_TABLES.finditer(sql)
        if re.search(rf"\b{ROLE}\b", m.group("rest"), re.IGNORECASE)
    ]
    assert offenders == [], f"ON ALL TABLES IN SCHEMA public reaches cyl_trait_sources: {offenders}"


def test_bloom_workflows_inherits_no_role():
    offenders = [m.group(0).strip() for m in GRANT_ROLE_MEMBERSHIP.finditer(_sql_text())]
    assert offenders == [], (
        f"role membership grants bloom_workflows whatever that role holds: {offenders}"
    )


def test_the_idempotency_key_grant_exists():
    """The read path bloomctl's idempotency gate depends on. Without it the gate fails open,
    and the #76 collision comes back."""
    columns: set[str] = set()
    for m in GRANT_ON_TABLE.finditer(_sql_text()):
        if "select" not in m.group("verbs").lower():
            continue
        if not re.search(rf"\b{ROLE}\b", m.group("rest"), re.IGNORECASE):
            continue
        cols = re.search(r"\(([^)]*)\)", m.group("verbs"))
        if cols:
            columns |= {c.strip() for c in cols.group(1).split(",")}
    assert "idempotency_key" in columns


# Every form a review demonstrated slipping past the first version of this guard. Each must be
# caught by at least one assertion above, or the guard is decorative.
BYPASS_FORMS = [
    "GRANT SELECT, UPDATE ON public.cyl_trait_sources TO bloom_workflows;",
    "GRANT UPDATE, SELECT ON public.cyl_trait_sources TO bloom_workflows;",
    "GRANT INSERT ON cyl_trait_sources TO bloom_workflows;",
    'GRANT INSERT ON public."cyl_trait_sources" TO bloom_workflows;',
    "GRANT TRUNCATE ON public.cyl_trait_sources TO bloom_workflows;",
    "GRANT REFERENCES ON public.cyl_trait_sources TO bloom_workflows;",
    "GRANT TRIGGER ON public.cyl_trait_sources TO bloom_workflows;",
    "GRANT ALL ON public.cyl_trait_sources TO bloom_workflows;",
    "GRANT ALL PRIVILEGES ON TABLE public.cyl_trait_sources TO bloom_workflows;",
    "GRANT SELECT ON public.cyl_trait_sources TO PUBLIC;",
    "GRANT SELECT ON public.cyl_trait_sources TO bloom_workflows;",
    "GRANT SELECT ON cyl_trait_sources TO bloom_workflows;",
    "grant select on public.cyl_trait_sources to bloom_workflows;",
    "GRANT\n  SELECT\n  ON public.cyl_trait_sources\n  TO bloom_workflows;",
    "GRANT SELECT ON ALL TABLES IN SCHEMA public TO bloom_workflows;",
    "GRANT bloom_agent TO bloom_workflows;",
]


@pytest.mark.parametrize("statement", BYPASS_FORMS)
def test_the_guard_catches_every_known_bypass_form(statement):
    caught = bool(
        _write_grants(statement)
        or _unscoped_selects(statement)
        or [
            m
            for m in GRANT_ALL_TABLES.finditer(statement)
            if re.search(rf"\b{ROLE}\b", m.group("rest"), re.IGNORECASE)
        ]
        or list(GRANT_ROLE_MEMBERSHIP.finditer(statement))
    )
    assert caught, f"guard does not catch: {statement!r}"


def test_no_rollback_strips_the_pre_existing_column_grant():
    """`cyl-trait-writeback` specs "the rollback leaves the pre-existing grant intact", and the
    rollback's own header warns that a bare `REVOKE SELECT ON public.cyl_trait_sources` would
    wipe all four columns' ACLs — taking `(id, metadata)`, which the dedup-preview read path in
    production depends on. Nothing enforced that. This does, without a database."""
    offenders = [
        f"{path.name}: {stmt}"
        for path in sorted((REPO_ROOT / "supabase" / "rollbacks").rglob("*.sql"))
        for stmt in _bare_revokes(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        "a column-less REVOKE on cyl_trait_sources strips every column's ACL, including the "
        f"pre-existing SELECT (id, metadata): {offenders}"
    )


@pytest.mark.parametrize(
    ("statement", "should_catch"),
    [
        ("REVOKE SELECT ON public.cyl_trait_sources FROM bloom_workflows;", True),
        ("REVOKE ALL ON public.cyl_trait_sources FROM bloom_workflows;", True),
        ("REVOKE SELECT ON cyl_trait_sources FROM bloom_workflows;", True),
        (
            "REVOKE SELECT (idempotency_key) ON public.cyl_trait_sources "
            "FROM bloom_workflows;",
            False,
        ),
    ],
)
def test_the_rollback_guard_distinguishes_bare_from_column_scoped(statement, should_catch):
    """Without this the guard above could pass simply by never matching anything."""
    assert bool(_bare_revokes(statement)) is should_catch


def test_the_guard_does_not_flag_the_legitimate_grants():
    """The shipped grants must stay clean, or the guard is just noise."""
    for ok in (
        "GRANT SELECT (idempotency_key) ON public.cyl_trait_sources TO bloom_workflows;",
        "GRANT SELECT (id, metadata) ON public.cyl_trait_sources TO bloom_workflows;",
        "REVOKE SELECT (idempotency_key) ON public.cyl_trait_sources FROM bloom_workflows;",
    ):
        assert not _write_grants(ok), ok
        assert not _unscoped_selects(ok), ok
