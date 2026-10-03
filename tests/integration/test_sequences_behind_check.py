"""
Integration tests for the read-only "sequences behind" guard (bloom#1022, PR 2 of
`fix-prod-sequences-behind`): `scripts/sql/sequences_behind.sql` must flag exactly the
sequences the sequence-advance body advances, and nothing once they are advanced.

Same isolation as test_advance_lagging_id_sequences.py: `_seq1022_<uid>_*` scratch tables
inside the test's own transaction, rolled back at the end.
"""

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.cyl_recipe_helpers import REPO_ROOT  # noqa: E402
from tests.integration.sequence_fixtures import Fixtures, behind  # noqa: E402

GUARD_SQL = REPO_ROOT / "scripts" / "sql" / "sequences_behind.sql"


@pytest.fixture
def fx(pg_conn):
    f = Fixtures(pg_conn)
    with pg_conn.cursor() as cur:
        real_behind = behind(cur)
    if real_behind:
        pg_conn.rollback()
        pytest.skip(
            f"real public sequences are behind on this database {sorted(real_behind)}; "
            "apply the advance migration first"
        )
    yield f
    pg_conn.rollback()


def _guard_rows(conn) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(GUARD_SQL.read_text(encoding="utf-8"))
        return [tuple(r) for r in cur.fetchall()]


def _unquote(ident: str) -> str:
    if ident.startswith('"') and ident.endswith('"'):
        return ident[1:-1].replace('""', '"')
    return ident


def _scratch(rows, uid: str) -> set[tuple[str, str]]:
    return {(r[0], r[1]) for r in rows if uid in r[0]}


def _build_fixtures(fx):
    """One table per case the body must tell apart (tasks 1.2-1.10, 1.17)."""

    def identity(label, ids, **kw):
        t = fx.table(label, **kw)
        fx.insert(t, ids)
        return t

    identity("behind", range(1, 6))  # behind: at 1, not called
    partly = fx.table("partly", kind="serial", type_="integer")
    fx.insert(partly, range(1, 11))
    fx.setval(partly, 4, True)  # behind: next 5
    equal_nc = identity("equal_nc", range(1, 4))
    fx.setval(equal_nc, 3, False)  # behind: next 3
    equal_c = identity("equal_c", range(1, 4))
    fx.setval(equal_c, 3, True)  # not behind: next 4
    ahead = identity("ahead", range(1, 4))
    fx.setval(ahead, 50, False)  # not behind: next 50
    fx.table("empty")  # not behind: no rows
    landing = fx.table("landing", kind="serial", type_="integer")
    fx.insert(landing, range(1, 26))
    fx.alter_seq(landing, "INCREMENT BY 5")
    fx.setval(landing, 20, True)  # behind: next 25
    wide = fx.table("wide", kind="serial", type_="integer")
    fx.insert(wide, range(1, 26))
    fx.alter_seq(wide, "INCREMENT BY 10")
    fx.setval(wide, 20, True)  # not behind: next 30
    down = fx.table("down", kind="serial", type_="integer")
    fx.insert(down, range(1, 6))
    fx.alter_seq(down, "INCREMENT BY -1")  # descending: out of scope
    identity("Mixed", [3_000_000_000], col="Id")  # behind, quoted names


def test_guard_flags_exactly_what_the_body_advances(fx):
    _build_fixtures(fx)
    flagged = _scratch(_guard_rows(fx.conn), fx.uid)
    fx.run()
    # NOTICEs carry %I-quoted names; the guard returns the raw relname/attname.
    advanced = {(_unquote(a["table"]), _unquote(a["col"])) for a in fx.advanced()}
    advanced = {pair for pair in advanced if fx.uid in pair[0]}
    assert flagged == advanced
    assert {t for t, _ in flagged} == {
        fx.name(label) for label in ("behind", "partly", "equal_nc", "landing", "Mixed")
    }


def test_guard_reports_max_and_next_value(fx):
    t = fx.table("report")
    fx.insert(t, range(1, 6))
    [row] = [r for r in _guard_rows(fx.conn) if r[0] == t.name]
    assert row[1:] == ("id", 5, 1)


def test_guard_is_clean_after_the_body_runs(fx):
    _build_fixtures(fx)
    fx.run()
    assert _scratch(_guard_rows(fx.conn), fx.uid) == set()


def test_guard_runs_in_a_read_only_transaction(pg_conn):
    try:
        with pg_conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(GUARD_SQL.read_text(encoding="utf-8"))
            cur.fetchall()
    finally:
        pg_conn.rollback()
