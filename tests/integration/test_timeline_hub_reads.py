"""
Integration tests for the Timeline page's two reads: gravi_scan_timeline (plate scan
batches per day, experiment and wave, under the reader's own row-level security) and
rnaseq_run_requesters (the email of whoever started each given RNA-seq run).

Each test applies the migration inside its own transaction and rolls it back, so the
database is left unchanged.
"""

import uuid

import pytest

from tests.integration.test_rnaseq_runs import _find_one, _sql_body

psycopg = pytest.importorskip("psycopg")

MIGRATION = _find_one("migrations", "*_add_timeline_hub_reads.sql")
ROLLBACK = _find_one("rollbacks", "*_add_timeline_hub_reads_rollback.sql")
VIEW = "public.gravi_scan_timeline"
FN = "public.rnaseq_run_requesters(bigint[])"
VIEW_READERS = ("bloom_user", "bloom_writer", "bloom_admin", "bloom_agent", "bloom_workflows")
FN_CALLERS = ("bloom_user", "bloom_writer", "bloom_admin")


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        c.execute(_sql_body(MIGRATION))
        yield c
    pg_conn.rollback()


def _experiment(cur, name, species_id=None):
    cur.execute(
        "INSERT INTO public.gravi_experiments (name, species_id, system_name) "
        "VALUES (%s, %s, %s) RETURNING id",
        (name, species_id, f"TL-{uuid.uuid4().hex[:8]}"),
    )
    return cur.fetchone()[0]


def _scan(cur, experiment_id, captured, wave=None, plate=None):
    cur.execute(
        "INSERT INTO public.gravi_scans "
        "(experiment_id, plate_id, capture_date, grid_mode, plate_index, resolution, wave_number) "
        "VALUES (%s, %s, %s, '2x2', '00', 1200, %s)",
        (experiment_id, plate or uuid.uuid4().hex[:8], captured, wave),
    )


def _rows(cur, experiment_name):
    cur.execute(
        f"SELECT date_scanned::text, species_name, wave_number, count FROM {VIEW} "
        "WHERE experiment_name = %s ORDER BY date_scanned DESC, wave_number",
        (experiment_name,),
    )
    return cur.fetchall()


def _species(cur):
    cur.execute("SELECT id, common_name FROM public.species WHERE deleted_at IS NULL LIMIT 1")
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO public.species (common_name) VALUES ('timeline-probe') "
            "RETURNING id, common_name"
        )
        row = cur.fetchone()
    return row


def _user(cur):
    user_id = str(uuid.uuid4())
    email = f"timeline-{user_id[:8]}@salk.edu"
    cur.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (user_id, email))
    return user_id, email


def _run(cur, requested_by, sample="tinygex"):
    cur.execute(
        "SELECT public.request_scrna_cellranger_run(%s, 'tiny_ref', %s)", (sample, requested_by)
    )
    return cur.fetchone()[0]


def _requesters(cur, role, run_ids):
    cur.execute(f"SET LOCAL ROLE {role}")
    cur.execute("SELECT run_id, email FROM public.rnaseq_run_requesters(%s)", (run_ids,))
    rows = sorted(cur.fetchall())
    cur.execute("RESET ROLE")
    return rows


# --------------------------------------------------------------------------- #
# gravi_scan_timeline
# --------------------------------------------------------------------------- #


def test_plate_scans_are_counted_per_day_experiment_and_wave(cur):
    species_id, species_name = _species(cur)
    exp = _experiment(cur, "timeline-probe plates", species_id)
    for hour in (1, 5, 9):
        _scan(cur, exp, f"2026-06-16 {hour:02}:00+00", wave=8)
    _scan(cur, exp, "2026-06-16 10:00+00", wave=9)
    _scan(cur, exp, "2026-06-17 02:00+00", wave=8)
    assert _rows(cur, "timeline-probe plates") == [
        ("2026-06-17", species_name, 8, 1),
        ("2026-06-16", species_name, 8, 3),
        ("2026-06-16", species_name, 9, 1),
    ]


def test_the_day_is_the_utc_capture_date(cur):
    exp = _experiment(cur, "timeline-probe utc")
    _scan(cur, exp, "2026-06-16 23:30-08:00")  # 07:30 UTC on the 17th
    assert _rows(cur, "timeline-probe utc")[0][0] == "2026-06-17"


def test_an_experiment_without_a_species_is_still_listed(cur):
    exp = _experiment(cur, "timeline-probe no species")
    _scan(cur, exp, "2026-06-16 01:00+00")
    assert _rows(cur, "timeline-probe no species") == [("2026-06-16", None, None, 1)]


def test_the_view_uses_the_readers_own_row_level_security(cur):
    cur.execute(
        "SELECT reloptions FROM pg_class WHERE oid = %s::regclass", (VIEW,)
    )
    assert "security_invoker=true" in (cur.fetchone()[0] or [])


def test_a_scientist_sees_plate_batches(cur):
    exp = _experiment(cur, "timeline-probe as user")
    _scan(cur, exp, "2026-06-16 01:00+00")
    cur.execute("SET LOCAL ROLE bloom_user")
    rows = _rows(cur, "timeline-probe as user")
    cur.execute("RESET ROLE")
    assert rows == [("2026-06-16", None, None, 1)]


@pytest.mark.parametrize("role", VIEW_READERS)
def test_the_view_is_readable_by_the_roles_that_read_plate_scans(cur, role):
    cur.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, VIEW))
    assert cur.fetchone()[0] is True


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_the_view_is_not_readable_by_anon_or_authenticated(cur, role):
    cur.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, VIEW))
    assert cur.fetchone()[0] is False


# --------------------------------------------------------------------------- #
# rnaseq_run_requesters
# --------------------------------------------------------------------------- #


def test_the_email_of_each_runs_starter_is_returned(cur):
    alice, alice_email = _user(cur)
    bob, bob_email = _user(cur)
    first = _run(cur, alice)
    second = _run(cur, bob, "root_rep1")
    assert _requesters(cur, "bloom_user", [first, second]) == sorted(
        [(first, alice_email), (second, bob_email)]
    )


def test_only_the_runs_asked_about_are_returned(cur):
    alice, alice_email = _user(cur)
    bob, _ = _user(cur)
    first = _run(cur, alice)
    _run(cur, bob, "root_rep1")
    assert _requesters(cur, "bloom_user", [first, 987654321]) == [(first, alice_email)]


def test_an_empty_list_returns_nothing(cur):
    assert _requesters(cur, "bloom_user", []) == []


@pytest.mark.parametrize(
    "role, allowed",
    [(r, True) for r in FN_CALLERS]
    + [(r, False) for r in ("anon", "authenticated", "bloom_agent", "bloom_workflows")],
)
def test_only_signed_in_bloom_roles_may_call_it(cur, role, allowed):
    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, FN))
    assert cur.fetchone()[0] is allowed


def test_it_is_security_definer_with_a_fixed_search_path(cur):
    cur.execute("SELECT prosecdef, proconfig FROM pg_proc WHERE oid = %s::regprocedure", (FN,))
    secdef, config = cur.fetchone()
    assert secdef is True
    assert config == ["search_path=pg_catalog, public"]


def test_a_scientist_cannot_read_auth_users_directly(cur):
    # Why the function exists.
    cur.execute("SELECT has_table_privilege('bloom_user', 'auth.users', 'SELECT')")
    assert cur.fetchone()[0] is False


# --------------------------------------------------------------------------- #
# Re-run and rollback
# --------------------------------------------------------------------------- #


def test_the_migration_can_be_run_again(cur):
    cur.execute(_sql_body(MIGRATION))
    cur.execute("SELECT has_table_privilege('bloom_user', %s, 'SELECT')", (VIEW,))
    assert cur.fetchone()[0] is True
    cur.execute("SELECT has_function_privilege('anon', %s, 'EXECUTE')", (FN,))
    assert cur.fetchone()[0] is False


def test_the_rollback_drops_both(cur):
    cur.execute(_sql_body(ROLLBACK))
    cur.execute("SELECT to_regclass(%s), to_regprocedure(%s)", (VIEW, FN))
    assert cur.fetchone() == (None, None)

