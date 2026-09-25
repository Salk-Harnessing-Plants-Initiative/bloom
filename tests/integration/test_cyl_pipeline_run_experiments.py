"""
Integration tests for the `cyl_pipeline_run_experiments` view (change `add-cyl-pipeline-ui`,
bloom#15 design §10, PR 1).

The view maps each pipeline run to every experiment its *requested* scans belong to
(`cyl_pipeline_run_scans` → `cyl_scans` → `cyl_plants` → `cyl_waves` → `cyl_experiments`). The web
UI's experiment-page runs panel reads it, because `cyl_pipeline_runs.target_id` has no FK and
`scan_ids` runs have no target at all. The migration also adds the `cyl_pipeline_run_scans(scan_id)`
index the join needs.

These tests pin: one row per (run, experiment); scans without a plant or wave contribute nothing;
`security_invoker` (base-table RLS and grants apply to the caller, so soft-deleted experiments stay
hidden from bloom_user); REVOKE-first grants (default privileges would otherwise hand out writes);
the index; the migration and rollback file layout (`lock_timeout`, `NOTIFY pgrst`, BEGIN/COMMIT
alone on their lines so `_sql_body` strips them); and rollback-then-reapply.

LOCAL ONLY: `pg_conn` connects as `supabase_admin` (BYPASSRLS). Every test re-applies the migration
body inside its own transaction and rolls back, so it works whether or not `make migrate-local` has
applied the file, and leaves the DB untouched. Runs in CI's `compose-health-check` job after
migrations are applied (`uv run --extra test pytest tests/integration/ -v`).
"""

import itertools
import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
VIEW = "cyl_pipeline_run_experiments"
INDEX = "cyl_pipeline_run_scans_scan_id_idx"

READ_ROLES = {"bloom_user", "bloom_agent", "bloom_admin"}
# Every role the migration REVOKEs from, plus the pseudo-role PUBLIC (spelled lowercase — that is
# the only spelling has_table_privilege() accepts for it).
CHECKED_ROLES = (
    "public",
    "anon",
    "authenticated",
    "service_role",
    "bloom_user",
    "bloom_writer",
    "bloom_agent",
    "bloom_admin",
    "bloom_workflows",
)
# bloom_writer holds no grant of its own; it reads through `GRANT bloom_user TO bloom_writer`.
EXPECTED_SELECT = READ_ROLES | {"bloom_writer"}
# Every table privilege; the default ACL grants all seven (arwdDxt), so all seven are pinned.
ALL_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
REQUESTED_BY = "00000000-0000-0000-0000-000000000001"


def _find_one(directory: str, glob: str) -> Path | None:
    matches = sorted((REPO_ROOT / "supabase" / directory).glob(glob))
    return matches[-1] if matches else None


MIGRATION = _find_one("migrations", "*_add_cyl_pipeline_run_experiments.sql")
ROLLBACK = _find_one("rollbacks", "*_add_cyl_pipeline_run_experiments_rollback.sql")


def _sql_body(path: Path) -> str:
    """The file minus its BEGIN;/COMMIT; lines, so it runs inside the test's own transaction
    (same helper as test_cyl_pipeline_dispatch.py)."""
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


@pytest.fixture
def cur(pg_conn):
    """A cursor on a transaction that has the migration applied. Always rolled back."""
    assert (
        MIGRATION is not None
    ), "migration *_add_cyl_pipeline_run_experiments.sql not written"
    with pg_conn.cursor() as c:
        c.execute(_sql_body(MIGRATION))
        yield c
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Seeding (the species → experiment → wave → plant → scan chain, as in
# test_cyl_read_model_views.py)
# --------------------------------------------------------------------------- #

_uniq = itertools.count(1)


def _experiment(cur) -> int:
    n = next(_uniq)
    cur.execute(
        "INSERT INTO species (common_name) VALUES (%s) RETURNING id", (f"sp-{n}",)
    )
    species_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_experiments (name, species_id) VALUES (%s, %s) RETURNING id",
        (f"exp-{n}", species_id),
    )
    return cur.fetchone()[0]


def _scan_in(cur, exp_id: int) -> int:
    # One wave per scan, numbered uniquely (cyl_waves is UNIQUE (experiment_id, number)).
    cur.execute(
        "INSERT INTO cyl_waves (experiment_id, number) VALUES (%s, %s) RETURNING id",
        (exp_id, next(_uniq)),
    )
    wave_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO accessions (name) VALUES (%s) RETURNING id",
        (f"acc-{next(_uniq)}",),
    )
    accession_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_plants (wave_id, accession_id) VALUES (%s, %s) RETURNING id",
        (wave_id, accession_id),
    )
    plant_id = cur.fetchone()[0]
    return _scan_for_plant(cur, plant_id)


def _scan_for_plant(cur, plant_id) -> int:
    cur.execute(
        "INSERT INTO cyl_scans (plant_id, date_scanned, plant_age_days) "
        "VALUES (%s, '2026-01-01', 10) RETURNING id",
        (plant_id,),
    )
    return cur.fetchone()[0]


def _run(cur, *scan_ids: int) -> tuple[int, object]:
    cur.execute(
        "INSERT INTO cyl_pipeline_runs (target_level, params, requested_by, scan_count) "
        "VALUES ('scan_ids', '{}'::jsonb, %s, %s) RETURNING id, created_at",
        (REQUESTED_BY, len(scan_ids)),
    )
    run_id, created_at = cur.fetchone()
    for scan_id in scan_ids:
        # batch_index plays no part in the view.
        cur.execute(
            "INSERT INTO cyl_pipeline_run_scans (run_id, scan_id, batch_index) "
            "VALUES (%s, %s, 0)",
            (run_id, scan_id),
        )
    return run_id, created_at


def _view_rows(cur, run_id: int) -> list[tuple]:
    cur.execute(
        f"SELECT run_id, experiment_id, created_at FROM {VIEW} WHERE run_id = %s "
        "ORDER BY experiment_id",
        (run_id,),
    )
    return cur.fetchall()


# --------------------------------------------------------------------------- #
# Row semantics
# --------------------------------------------------------------------------- #


def test_multi_scan_run_in_one_experiment_yields_one_row(cur):
    exp = _experiment(cur)
    run_id, created_at = _run(cur, _scan_in(cur, exp), _scan_in(cur, exp))
    assert _view_rows(cur, run_id) == [(run_id, exp, created_at)]


def test_run_spanning_two_experiments_yields_one_row_each(cur):
    exp_a, exp_b = _experiment(cur), _experiment(cur)
    run_id, _ = _run(cur, _scan_in(cur, exp_a), _scan_in(cur, exp_b))
    assert [r[1] for r in _view_rows(cur, run_id)] == sorted([exp_a, exp_b])


def test_scan_without_plant_contributes_no_row(cur):
    cur.execute("INSERT INTO cyl_scans DEFAULT VALUES RETURNING id")
    run_id, _ = _run(cur, cur.fetchone()[0])
    assert _view_rows(cur, run_id) == []


def test_plant_without_wave_contributes_no_row(cur):
    cur.execute(
        "INSERT INTO accessions (name) VALUES (%s) RETURNING id",
        (f"acc-{next(_uniq)}",),
    )
    accession_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_plants (wave_id, accession_id) VALUES (NULL, %s) RETURNING id",
        (accession_id,),
    )
    run_id, _ = _run(cur, _scan_for_plant(cur, cur.fetchone()[0]))
    assert _view_rows(cur, run_id) == []


def test_run_with_no_scan_rows_yields_no_row(cur):
    run_id, _ = _run(cur)
    assert _view_rows(cur, run_id) == []


# --------------------------------------------------------------------------- #
# security_invoker: the caller's own base-table access applies
# --------------------------------------------------------------------------- #


def test_bloom_user_reads_the_view(cur):
    exp = _experiment(cur)
    run_id, created_at = _run(cur, _scan_in(cur, exp))
    cur.execute("SET LOCAL ROLE bloom_user")
    assert _view_rows(cur, run_id) == [(run_id, exp, created_at)]


@pytest.mark.parametrize(
    "role, sees_it",
    [
        ("bloom_user", False),
        ("bloom_agent", False),
        ("bloom_admin", True),
        # bloom_writer's own cyl_experiments policy (writer_select_cyl_experiments, USING true)
        # is OR'd with the deleted_at filter it inherits from bloom_user, so it sees them.
        ("bloom_writer", True),
    ],
)
def test_soft_deleted_experiment_visibility_follows_each_roles_policy(
    cur, role, sees_it
):
    """The hiding is a UI filter, not an access boundary: the base tables still expose the
    experiment id. It only follows each role's cyl_experiments policy."""
    exp = _experiment(cur)
    run_id, _ = _run(cur, _scan_in(cur, exp))
    cur.execute("UPDATE cyl_experiments SET deleted_at = now() WHERE id = %s", (exp,))
    cur.execute(f"SET LOCAL ROLE {role}")
    assert [r[1] for r in _view_rows(cur, run_id)] == ([exp] if sees_it else [])


def test_panel_query_returns_each_run_once_newest_first(cur):
    """The experiment panel's query: 10 most recent runs, newest first, ties broken by run id
    (every run seeded in one transaction shares created_at). A run with several scans in the
    experiment appears once."""
    exp = _experiment(cur)
    run_ids = [_run(cur, _scan_in(cur, exp), _scan_in(cur, exp))[0] for _ in range(12)]
    cur.execute("SET LOCAL ROLE bloom_user")
    cur.execute(
        f"SELECT run_id FROM {VIEW} WHERE experiment_id = %s "
        "ORDER BY created_at DESC, run_id DESC LIMIT 10",
        (exp,),
    )
    assert [r[0] for r in cur.fetchall()] == sorted(run_ids, reverse=True)[:10]


def test_view_checks_base_table_privileges_as_the_invoker(pg_conn, cur):
    """A role with SELECT on the view but none on the base tables must be refused by a base
    table. Under a definer view the owner's rights would apply and the query would succeed.
    """
    probe = f"pipeline_probe_{uuid.uuid4().hex[:8]}"
    cur.execute(f"CREATE ROLE {probe} NOLOGIN")
    cur.execute(f"GRANT USAGE ON SCHEMA public TO {probe}")
    cur.execute(f"GRANT SELECT ON public.{VIEW} TO {probe}")

    # The savepoint keeps the fixture's transaction usable after the expected error.
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege) as err,
        pg_conn.transaction(),
    ):
        cur.execute(f"SET LOCAL ROLE {probe}")
        cur.execute(f"SELECT * FROM public.{VIEW} LIMIT 1")

    message = str(err.value)
    assert "permission denied for table" in message
    assert VIEW not in message, "the view itself was refused, not a base table"


def test_view_is_security_invoker(cur):
    cur.execute(
        "SELECT reloptions && ARRAY['security_invoker=on', 'security_invoker=true'] "
        "FROM pg_class WHERE oid = %s::regclass",
        (f"public.{VIEW}",),
    )
    assert cur.fetchone()[0] is True


# --------------------------------------------------------------------------- #
# Grants: REVOKE-first, SELECT only to the read roles
# --------------------------------------------------------------------------- #


def _assert_privileges(cur, role: str) -> None:
    for privilege in ALL_PRIVILEGES:
        cur.execute(
            "SELECT has_table_privilege(%s, %s, %s)",
            (role, f"public.{VIEW}", privilege),
        )
        held = cur.fetchone()[0]
        expected = privilege == "SELECT" and role in EXPECTED_SELECT
        assert held is expected, f"{role} {privilege}: expected {expected}, got {held}"


@pytest.mark.parametrize("role", CHECKED_ROLES)
def test_privilege_matrix(cur, role):
    _assert_privileges(cur, role)


# --------------------------------------------------------------------------- #
# Index
# --------------------------------------------------------------------------- #


def test_scan_id_index_exists(cur):
    cur.execute(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = %s",
        (INDEX,),
    )
    row = cur.fetchone()
    assert row is not None, f"{INDEX} missing"
    assert re.search(r"cyl_pipeline_run_scans\b.*\(scan_id\)", row[0]), row[0]


# --------------------------------------------------------------------------- #
# File layout
# --------------------------------------------------------------------------- #


def test_migration_and_rollback_files_have_the_required_statements():
    assert MIGRATION is not None and ROLLBACK is not None
    migration = MIGRATION.read_text()
    rollback = ROLLBACK.read_text()
    for text in (migration, rollback):
        assert "SET LOCAL lock_timeout" in text
        # NOTIFY must follow COMMIT: PostgREST should reload only once the DDL is committed.
        notify = text.index("NOTIFY pgrst, 'reload schema'")
        commit = re.search(r"^\s*COMMIT\s*;\s*$", text, re.MULTILINE | re.IGNORECASE)
        assert commit is not None and commit.start() < notify


@pytest.mark.parametrize("which", ["migration", "rollback"])
def test_transaction_wrapper_is_fully_stripped(which):
    """BEGIN;/COMMIT; must sit alone on their lines. Otherwise `_sql_body` leaves them in, and a
    test applying the file would really COMMIT inside the fixture's transaction."""
    path = MIGRATION if which == "migration" else ROLLBACK
    assert path is not None
    body = _sql_body(path)
    code = "\n".join(line.split("--", 1)[0] for line in body.splitlines())
    assert not re.search(r"\b(BEGIN|COMMIT)\b", code, re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Rollback, then re-apply
# --------------------------------------------------------------------------- #


def _exists(cur) -> tuple[bool, bool]:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{VIEW}",))
    view = cur.fetchone()[0]
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{INDEX}",))
    return view, cur.fetchone()[0]


def test_rollback_then_reapply(cur):
    assert (
        ROLLBACK is not None
    ), "rollback *_add_cyl_pipeline_run_experiments_rollback.sql not written"
    assert _exists(cur) == (True, True)
    cur.execute(_sql_body(ROLLBACK))
    assert _exists(cur) == (False, False)
    cur.execute(_sql_body(MIGRATION))
    assert _exists(cur) == (True, True)
    # The re-create runs as supabase_admin, whose default privileges fire on it, so REVOKE-first
    # and security_invoker have to hold again here, not only on CI's fresh db push.
    for role in CHECKED_ROLES:
        _assert_privileges(cur, role)
    test_view_is_security_invoker(cur)
