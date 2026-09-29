"""
Integration tests for rnaseq_samples and rnaseq_references, the choices the scRNA job form
offers: the name, source and size rules, who can read and write them, and the rollback.

Each test drops the two tables, applies the migration inside its own transaction, and
rolls it back, so the database is left unchanged.
"""

import pytest

from tests.integration.test_rnaseq_runs import _find_one, _sql_body

psycopg = pytest.importorskip("psycopg")

MIGRATION = _find_one("migrations", "*_create_rnaseq_samples_and_references.sql")
ROLLBACK = _find_one("rollbacks", "*_create_rnaseq_samples_and_references_rollback.sql")
TABLES = ("rnaseq_samples", "rnaseq_references")
READERS = ("bloom_user", "bloom_agent", "bloom_workflows")
# bloom_writer is a member of bloom_user, so it reads through that grant.
READ_ONLY = (*READERS, "bloom_writer")
NO_ACCESS = ("anon", "authenticated")
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")

GOOD_SAMPLES = ["tinygex", "Col0_root_rep1", "S1-rep1", "SRR1234567", "x" * 64]
BAD_SAMPLES = ["S1.rep1", "x" * 65, "-rep1", "_rep1", "a__b", "has space", "a/b", ""]
GOOD_REFERENCES = ["tiny_ref", "TAIR10.araport11", "GRCh38-2024-A", "r" * 100]
BAD_REFERENCES = [".hidden", "r" * 101, "a__b", "a/b", "has space", "../etc", ""]


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        for table in TABLES:
            c.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")
        c.execute(_sql_body(MIGRATION))
        yield c
    pg_conn.rollback()


def _refused(cur, sql, params, error):
    cur.execute("SAVEPOINT refused")
    try:
        with pytest.raises(error):
            cur.execute(sql, params)
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT refused")


def _add_sample(cur, name="tinygex", **columns):
    columns = {"name": name, **columns}
    cur.execute(
        f"INSERT INTO rnaseq_samples ({', '.join(columns)}) "
        f"VALUES ({', '.join(['%s'] * len(columns))}) RETURNING *",
        tuple(columns.values()),
    )
    return cur.fetchone()


def _has(cur, role, table, privilege):
    cur.execute("SELECT has_table_privilege(%s, %s, %s)", (role, f"public.{table}", privilege))
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# rnaseq_samples
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", GOOD_SAMPLES)
def test_a_sample_name_cellranger_can_use_is_accepted(cur, name):
    assert _add_sample(cur, name) is not None


@pytest.mark.parametrize("name", BAD_SAMPLES)
def test_a_sample_name_cellranger_cannot_use_is_refused(cur, name):
    _refused(
        cur,
        "INSERT INTO rnaseq_samples (name) VALUES (%s)",
        (name,),
        psycopg.errors.CheckViolation,
    )


def test_a_new_sample_defaults_to_s3_with_no_details(cur):
    cur.execute(
        "INSERT INTO rnaseq_samples (name) VALUES ('tinygex') "
        "RETURNING source, source_ref, fastq_count, total_bytes, registered_by, created_at"
    )
    source, source_ref, fastq_count, total_bytes, registered_by, created_at = cur.fetchone()
    assert (source, source_ref, fastq_count, total_bytes, registered_by) == (
        "s3",
        None,
        None,
        None,
        None,
    )
    assert created_at is not None


@pytest.mark.parametrize("source", ["s3", "igc", "sra"])
def test_the_three_sources_are_accepted(cur, source):
    _add_sample(cur, source=source)


@pytest.mark.parametrize("source", ["S3", "box", "", None])
def test_another_source_is_refused(cur, source):
    error = psycopg.errors.NotNullViolation if source is None else psycopg.errors.CheckViolation
    _refused(
        cur,
        "INSERT INTO rnaseq_samples (name, source) VALUES ('tinygex', %s)",
        (source,),
        error,
    )


@pytest.mark.parametrize("column", ["fastq_count", "total_bytes"])
def test_a_negative_count_or_size_is_refused(cur, column):
    _refused(
        cur,
        f"INSERT INTO rnaseq_samples (name, {column}) VALUES ('tinygex', -1)",
        None,
        psycopg.errors.CheckViolation,
    )


def test_a_size_above_int32_is_kept(cur):
    row = _add_sample(cur, total_bytes=58_200_000_000)
    cur.execute("SELECT total_bytes FROM rnaseq_samples WHERE id = %s", (row[0],))
    assert cur.fetchone()[0] == 58_200_000_000


def test_a_sample_name_is_registered_once(cur):
    _add_sample(cur)
    _refused(
        cur,
        "INSERT INTO rnaseq_samples (name) VALUES ('tinygex')",
        None,
        psycopg.errors.UniqueViolation,
    )


# --------------------------------------------------------------------------- #
# rnaseq_references
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", GOOD_REFERENCES)
def test_a_reference_name_is_accepted(cur, name):
    cur.execute("INSERT INTO rnaseq_references (name) VALUES (%s)", (name,))


@pytest.mark.parametrize("name", BAD_REFERENCES)
def test_a_bad_reference_name_is_refused(cur, name):
    _refused(
        cur,
        "INSERT INTO rnaseq_references (name) VALUES (%s)",
        (name,),
        psycopg.errors.CheckViolation,
    )


def test_a_reference_keeps_its_description(cur):
    cur.execute(
        "INSERT INTO rnaseq_references (name, description) "
        "VALUES ('tiny_ref', 'Arabidopsis TAIR10') RETURNING description, created_at"
    )
    description, created_at = cur.fetchone()
    assert description == "Arabidopsis TAIR10"
    assert created_at is not None


def test_a_reference_name_is_registered_once(cur):
    cur.execute("INSERT INTO rnaseq_references (name) VALUES ('tiny_ref')")
    _refused(
        cur,
        "INSERT INTO rnaseq_references (name) VALUES ('tiny_ref')",
        None,
        psycopg.errors.UniqueViolation,
    )


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("table", TABLES)
def test_row_level_security_is_on(cur, table):
    cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass", (f"public.{table}",))
    assert cur.fetchone()[0] is True


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("role", READ_ONLY)
def test_readers_can_only_read(cur, table, role):
    assert [p for p in PRIVILEGES if _has(cur, role, table, p)] == ["SELECT"]


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("role", NO_ACCESS)
def test_other_roles_have_no_access(cur, table, role):
    assert [p for p in PRIVILEGES if _has(cur, role, table, p)] == []


@pytest.mark.parametrize("table", TABLES)
def test_bloom_admin_can_add_change_and_remove(cur, table):
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert _has(cur, "bloom_admin", table, privilege)


@pytest.mark.parametrize("table", TABLES)
def test_service_role_cannot_change_or_remove(cur, table):
    for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
        assert not _has(cur, "service_role", table, privilege)


@pytest.mark.parametrize("role", READERS)
def test_readers_see_every_row_through_their_policy(cur, role):
    _add_sample(cur, "tinygex")
    _add_sample(cur, "root_rep2")
    cur.execute("INSERT INTO rnaseq_references (name) VALUES ('tiny_ref')")
    cur.execute(f"SET LOCAL ROLE {role}")
    cur.execute("SELECT count(*) FROM rnaseq_samples")
    samples = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM rnaseq_references")
    references = cur.fetchone()[0]
    cur.execute("RESET ROLE")
    assert (samples, references) == (2, 1)


def test_bloom_admin_can_register_a_sample_through_its_policy(cur):
    cur.execute("SET LOCAL ROLE bloom_admin")
    cur.execute("INSERT INTO rnaseq_samples (name, source) VALUES ('tinygex', 'sra') RETURNING id")
    assert cur.fetchone() is not None
    cur.execute("RESET ROLE")


# --------------------------------------------------------------------------- #
# Re-run and rollback
# --------------------------------------------------------------------------- #


def test_the_migration_can_be_run_again_without_losing_rows(cur):
    _add_sample(cur)
    cur.execute(_sql_body(MIGRATION))
    cur.execute("SELECT count(*) FROM rnaseq_samples")
    assert cur.fetchone()[0] == 1
    assert [p for p in PRIVILEGES if _has(cur, "bloom_user", "rnaseq_samples", p)] == ["SELECT"]


def test_the_rollback_drops_both_tables(cur):
    cur.execute(_sql_body(ROLLBACK))
    for table in TABLES:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        assert cur.fetchone()[0] is None
