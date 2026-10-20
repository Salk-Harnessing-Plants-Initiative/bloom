"""
Integration tests for the Cell Ranger pipeline's GO and pathway steps: a run may report
`annotate-genome` and `go-pathways`, and a species may record its KEGG organism code. Each test
runs in its own transaction and rolls it back.
"""

import pytest

from tests.integration import _scrna_pipeline_load as load
from tests.integration.test_rnaseq_runs import _find_one, _sql_body

psycopg = pytest.importorskip("psycopg")

MIGRATION = _find_one("migrations", "*_add_pipeline_go_pathways_steps.sql")
ROLLBACK = _find_one("rollbacks", "*_add_pipeline_go_pathways_steps_rollback.sql")
NEW_STEPS = ("annotate-genome", "go-pathways")


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        c.execute(_sql_body(MIGRATION))
        yield c
    pg_conn.rollback()


@pytest.fixture
def scientist(cur):
    return load.user(cur)


@pytest.fixture
def species(cur):
    return load.species(cur)


def _step(cur, run_id):
    cur.execute("SELECT current_step FROM public.rnaseq_runs WHERE id = %s", (run_id,))
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# The steps
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("step", NEW_STEPS)
def test_a_run_can_report_the_go_steps(cur, scientist, species, step):
    run_id = load.run(cur, scientist, species)
    cur.execute("UPDATE public.rnaseq_runs SET current_step = %s WHERE id = %s", (step, run_id))
    assert _step(cur, run_id) == step


@pytest.mark.parametrize("step", ["count", "load-dataset", "cleanup"])
def test_the_earlier_steps_are_still_allowed(cur, scientist, species, step):
    run_id = load.run(cur, scientist, species)
    cur.execute("UPDATE public.rnaseq_runs SET current_step = %s WHERE id = %s", (step, run_id))
    assert _step(cur, run_id) == step


def test_an_unknown_step_is_still_refused(cur, scientist, species):
    run_id = load.run(cur, scientist, species)
    load.refused(cur, "UPDATE public.rnaseq_runs SET current_step = 'enrich' WHERE id = %s",
                 (run_id,), error=psycopg.errors.CheckViolation)


# --------------------------------------------------------------------------- #
# A species' KEGG code
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("code", ["sbi", "ath", "gmax", None])
def test_a_species_takes_a_kegg_code_or_none(cur, species, code):
    cur.execute("UPDATE public.species SET kegg_org = %s WHERE id = %s", (code, species))
    cur.execute("SELECT kegg_org FROM public.species WHERE id = %s", (species,))
    assert cur.fetchone()[0] == code


@pytest.mark.parametrize("code", ["SBI", "sb", "sbicolor", "s1i", ""])
def test_a_code_that_isnt_three_or_four_lowercase_letters_is_refused(cur, species, code):
    load.refused(cur, "UPDATE public.species SET kegg_org = %s WHERE id = %s", (code, species),
                 error=psycopg.errors.CheckViolation)


# --------------------------------------------------------------------------- #
# Re-running and rolling back
# --------------------------------------------------------------------------- #


def test_the_migration_runs_again_without_error(cur, scientist, species):
    cur.execute("UPDATE public.species SET kegg_org = 'sbi' WHERE id = %s", (species,))
    cur.execute(_sql_body(MIGRATION))
    cur.execute("SELECT kegg_org FROM public.species WHERE id = %s", (species,))
    assert cur.fetchone()[0] == "sbi", "the column and its values are kept"


def test_the_rollback_removes_the_steps_and_the_column(cur, scientist, species):
    cur.execute(_sql_body(ROLLBACK))
    run_id = load.run(cur, scientist, species)
    load.refused(cur, "UPDATE public.rnaseq_runs SET current_step = 'go-pathways' WHERE id = %s",
                 (run_id,), error=psycopg.errors.CheckViolation)
    cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'species' AND column_name = 'kegg_org'")
    assert cur.fetchone()[0] == 0


@pytest.mark.parametrize("step", NEW_STEPS)
def test_the_rollback_refuses_while_a_run_reports_a_go_step(cur, scientist, species, step):
    run_id = load.run(cur, scientist, species)
    cur.execute("UPDATE public.rnaseq_runs SET current_step = %s WHERE id = %s", (step, run_id))
    cur.execute("SAVEPOINT rb")
    with pytest.raises(psycopg.errors.RaiseException, match="clear current_step"):
        cur.execute(_sql_body(ROLLBACK))
    cur.execute("ROLLBACK TO SAVEPOINT rb")
