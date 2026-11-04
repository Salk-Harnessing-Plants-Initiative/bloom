"""
Integration tests for the new-species email's schema:

- notification_recipients: who gets Bloom's team notifications. Admins write it, the
  workflows service reads it, and no other role can see it. An address is stored once in
  any casing.
- species.team_notified_at: the migration adds it and marks the species already in Bloom,
  re-applying it marks nothing new, and the workflows service can set it once on a species
  not yet emailed about, and nothing else.

The rollback drops both. Each test applies the files inside its own transaction and rolls
it back, so the database is left unchanged.
"""

import uuid

import pytest

from tests.integration.test_rnaseq_runs import _find_one, _sql_body

psycopg = pytest.importorskip("psycopg")

MIGRATION = _find_one("migrations", "*_add_species_notifications.sql")
ROLLBACK = _find_one("rollbacks", "*_add_species_notifications_rollback.sql")

RECIPIENTS = "public.notification_recipients"
MARK = "UPDATE public.species SET team_notified_at = now() WHERE id = %s RETURNING id"


def _add_species(cur, deleted=False) -> int:
    name = f"Testcress{uuid.uuid4().hex[:8]}"
    cur.execute(
        "INSERT INTO public.species (genus, species, common_name, deleted_at) "
        "VALUES ('Testus', %s, %s, %s) RETURNING id",
        (name.lower(), name, "2026-11-01T00:00:00Z" if deleted else None),
    )
    return cur.fetchone()[0]


def _notified_at(cur, species_id):
    cur.execute("SELECT team_notified_at FROM public.species WHERE id = %s", (species_id,))
    return cur.fetchone()[0]


def _as(cur, role, sql, params=()):
    cur.execute(f"SET LOCAL ROLE {role}")
    try:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None
    finally:
        cur.execute("RESET ROLE")


def _column_exists(cur, table, column) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
        "AND table_name = %s AND column_name = %s",
        (table, column),
    )
    return cur.fetchone() is not None


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        c.execute(_sql_body(MIGRATION))
        yield c
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# notification_recipients
# --------------------------------------------------------------------------- #


def test_an_admin_adds_changes_and_removes_a_recipient(cur):
    rows = _as(
        cur,
        "bloom_admin",
        f"INSERT INTO {RECIPIENTS} (email, description) "
        "VALUES ('jdoe@salk.edu', 'Bloom developer') RETURNING id, created_at",
    )
    recipient_id, created_at = rows[0]
    assert created_at is not None
    _as(
        cur,
        "bloom_admin",
        f"UPDATE {RECIPIENTS} SET description = 'Adds species icons' WHERE id = %s",
        (recipient_id,),
    )
    _as(cur, "bloom_admin", f"DELETE FROM {RECIPIENTS} WHERE id = %s", (recipient_id,))
    cur.execute(f"SELECT count(*) FROM {RECIPIENTS} WHERE id = %s", (recipient_id,))
    assert cur.fetchone()[0] == 0


def test_the_workflows_service_reads_the_recipients(cur):
    cur.execute(
        f"INSERT INTO {RECIPIENTS} (email, description) VALUES ('jdoe@salk.edu', 'Bloom developer')"
    )
    assert _as(cur, "bloom_workflows", f"SELECT email FROM {RECIPIENTS}") == [("jdoe@salk.edu",)]


@pytest.mark.parametrize("privilege", ["INSERT", "UPDATE", "DELETE"])
def test_the_workflows_service_cannot_change_the_recipients(cur, privilege):
    cur.execute("SELECT has_table_privilege('bloom_workflows', %s, %s)", (RECIPIENTS, privilege))
    assert cur.fetchone()[0] is False


@pytest.mark.parametrize(
    "role", ["bloom_user", "bloom_agent", "bloom_writer", "anon", "authenticated"]
)
@pytest.mark.parametrize("privilege", ["SELECT", "INSERT", "UPDATE", "DELETE"])
def test_no_other_role_can_see_or_change_the_recipients(cur, role, privilege):
    cur.execute("SELECT has_table_privilege(%s, %s, %s)", (role, RECIPIENTS, privilege))
    assert cur.fetchone()[0] is False


def test_an_address_is_stored_once_in_any_casing(cur):
    cur.execute(
        f"INSERT INTO {RECIPIENTS} (email, description) VALUES ('jdoe@salk.edu', 'Bloom developer')"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        cur.execute(
            f"INSERT INTO {RECIPIENTS} (email, description) VALUES ('JDoe@Salk.edu', 'Again')"
        )


@pytest.mark.parametrize(
    "email", ["jdoe", "jdoe@salk", "@salk.edu", "jdoe @salk.edu", "j@doe@salk.edu", " jdoe@salk.edu"]
)
def test_an_address_must_look_like_one(cur, email):
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(
            f"INSERT INTO {RECIPIENTS} (email, description) VALUES (%s, 'Bloom developer')",
            (email,),
        )


@pytest.mark.parametrize("description", ["", "   "])
def test_a_recipient_needs_a_description(cur, description):
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(
            f"INSERT INTO {RECIPIENTS} (email, description) VALUES ('jdoe@salk.edu', %s)",
            (description,),
        )


def test_reapplying_the_migration_keeps_the_recipients(cur):
    cur.execute(
        f"INSERT INTO {RECIPIENTS} (email, description) VALUES ('jdoe@salk.edu', 'Bloom developer')"
    )
    cur.execute(_sql_body(MIGRATION))
    cur.execute(f"SELECT email FROM {RECIPIENTS}")
    assert cur.fetchall() == [("jdoe@salk.edu",)]


# --------------------------------------------------------------------------- #
# species.team_notified_at
# --------------------------------------------------------------------------- #


def test_species_already_in_bloom_are_marked_when_the_column_is_added(cur):
    cur.execute(_sql_body(ROLLBACK))
    species_id = _add_species(cur)
    cur.execute(_sql_body(MIGRATION))
    assert _notified_at(cur, species_id) is not None


def test_reapplying_the_migration_leaves_a_new_species_unmarked(cur):
    species_id = _add_species(cur)
    cur.execute(_sql_body(MIGRATION))
    assert _notified_at(cur, species_id) is None


def test_the_workflows_service_marks_a_new_species_once(cur):
    species_id = _add_species(cur)
    assert _as(cur, "bloom_workflows", MARK, (species_id,)) == [(species_id,)]
    assert _notified_at(cur, species_id) is not None
    # Already marked: nothing to claim, so a second email is never sent.
    assert _as(cur, "bloom_workflows", MARK, (species_id,)) == []


def test_the_workflows_service_cannot_mark_a_removed_species(cur):
    species_id = _add_species(cur, deleted=True)
    assert _as(cur, "bloom_workflows", MARK, (species_id,)) == []


def test_the_workflows_service_cannot_clear_the_mark(cur):
    species_id = _add_species(cur)
    _as(cur, "bloom_workflows", MARK, (species_id,))
    rows = _as(
        cur,
        "bloom_workflows",
        "UPDATE public.species SET team_notified_at = NULL WHERE id = %s RETURNING id",
        (species_id,),
    )
    assert rows == []
    assert _notified_at(cur, species_id) is not None


@pytest.mark.parametrize("column", ["common_name", "genus", "species", "created_by", "deleted_at"])
def test_the_workflows_service_can_update_no_other_species_column(cur, column):
    cur.execute(
        "SELECT has_column_privilege('bloom_workflows', 'public.species', %s, 'UPDATE')",
        (column,),
    )
    assert cur.fetchone()[0] is False


def test_signed_in_users_have_no_update_policy_on_species(cur):
    """So no one but an admin can clear the mark and have a species emailed about again."""
    cur.execute(
        "SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'species' "
        "AND cmd IN ('UPDATE', 'ALL') AND 'bloom_user' = ANY(roles)"
    )
    assert cur.fetchone() is None


# --------------------------------------------------------------------------- #
# Rollback
# --------------------------------------------------------------------------- #


def test_the_rollback_drops_both_and_can_run_twice(cur):
    cur.execute(_sql_body(ROLLBACK))
    cur.execute(_sql_body(ROLLBACK))
    assert not _column_exists(cur, "species", "team_notified_at")
    cur.execute("SELECT to_regclass(%s)", (RECIPIENTS,))
    assert cur.fetchone()[0] is None
