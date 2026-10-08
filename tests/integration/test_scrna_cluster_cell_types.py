"""
Integration tests for migration 20261008230000_add_scrna_cluster_cell_types.

The migration adds `scrna_cluster_cell_types`: the predicted cell types of each cluster, each
with an optional source and the share of the cluster's cells that carry it.

LOCAL ONLY: `pg_conn` connects to 127.0.0.1 on POSTGRES_HOST_PORT and mutates nothing --
every test rolls back. It connects as `supabase_admin`, which is BYPASSRLS, so access checks
read the catalog rather than claiming to prove enforcement.

Runs in CI's `compose-health-check` job after migrations are applied.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MIGRATION = REPO_ROOT / "supabase/migrations/20261008230000_add_scrna_cluster_cell_types.sql"
TABLE = "public.scrna_cluster_cell_types"


def dataset(cur, clusters=("0", "1")) -> int:
    tag = uuid.uuid4().hex[:10]
    cur.execute(
        "INSERT INTO species (common_name, genus, species) VALUES (%s, %s, %s) RETURNING id",
        (f"celltype-{tag}", f"Celltypus-{tag}", f"testis-{tag}"),
    )
    species_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO scrna_datasets (name, species_id) VALUES (%s, %s) RETURNING id",
        (f"celltype-{tag}", species_id),
    )
    dataset_id = cur.fetchone()[0]
    cur.executemany(
        "INSERT INTO scrna_clusters (dataset_id, cluster_id, ordinal, color) "
        "VALUES (%s, %s, %s, '#4E79A7')",
        [(dataset_id, c, i) for i, c in enumerate(clusters)],
    )
    return dataset_id


def predict(cur, dataset_id: int, cluster_id: str = "0", cell_type: str = "Columella",
            **kwargs) -> None:
    columns = {"dataset_id": dataset_id, "cluster_id": cluster_id, "cell_type": cell_type,
               **kwargs}
    cur.execute(
        f"INSERT INTO scrna_cluster_cell_types ({', '.join(columns)}) "
        f"VALUES ({', '.join(['%s'] * len(columns))})",
        tuple(columns.values()),
    )


def refused(pg_conn, constraint: str, **kwargs) -> None:
    """The insert is refused by the named constraint."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur)
        with pytest.raises(psycopg.errors.IntegrityError) as err:
            predict(cur, ds, **kwargs)
    assert err.value.diag.constraint_name == constraint
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# What a row holds
# --------------------------------------------------------------------------- #


def test_the_table_has_its_columns(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'scrna_cluster_cell_types'"
        )
        columns = dict(cur.fetchall())
    assert columns == {
        "dataset_id": "NO", "cluster_id": "NO", "cell_type": "NO",
        "source": "YES", "fraction": "YES", "created_at": "NO",
    }


def test_a_cluster_records_its_predicted_cell_type(pg_conn):
    with pg_conn.cursor() as cur:
        ds = dataset(cur)
        predict(cur, ds, "0", "Columella", source="nuclei atlas", fraction=0.82)
        cur.execute(
            "SELECT cell_type, source, fraction FROM scrna_cluster_cell_types "
            "WHERE dataset_id = %s AND cluster_id = '0'", (ds,),
        )
        cell_type, source, fraction = cur.fetchone()
    assert (cell_type, source) == ("Columella", "nuclei atlas")
    assert fraction == pytest.approx(0.82)
    pg_conn.rollback()


def test_source_and_fraction_may_be_left_out(pg_conn):
    with pg_conn.cursor() as cur:
        ds = dataset(cur)
        predict(cur, ds)
        cur.execute(
            "SELECT source, fraction FROM scrna_cluster_cell_types WHERE dataset_id = %s", (ds,)
        )
        assert cur.fetchone() == (None, None)
    pg_conn.rollback()


def test_a_cluster_may_have_several_cell_types(pg_conn):
    with pg_conn.cursor() as cur:
        ds = dataset(cur)
        predict(cur, ds, "0", "Columella", fraction=0.7)
        predict(cur, ds, "0", "Lateral root cap", fraction=0.3)
        cur.execute(
            "SELECT cell_type FROM scrna_cluster_cell_types "
            "WHERE dataset_id = %s AND cluster_id = '0' ORDER BY fraction DESC", (ds,),
        )
        assert [r[0] for r in cur.fetchall()] == ["Columella", "Lateral root cap"]
    pg_conn.rollback()


def test_a_cluster_names_each_cell_type_once(pg_conn):
    with pg_conn.cursor() as cur:
        ds = dataset(cur)
        predict(cur, ds, "0", "Columella", source="nuclei atlas")
        with pytest.raises(psycopg.errors.UniqueViolation) as err:
            predict(cur, ds, "0", "Columella", source="protoplast atlas")
    assert err.value.diag.constraint_name == "scrna_cluster_cell_types_pkey"
    pg_conn.rollback()


def test_two_clusters_may_share_a_cell_type(pg_conn):
    with pg_conn.cursor() as cur:
        ds = dataset(cur)
        predict(cur, ds, "0", "Columella")
        predict(cur, ds, "1", "Columella")
        cur.execute(
            "SELECT count(*) FROM scrna_cluster_cell_types WHERE dataset_id = %s", (ds,)
        )
        assert cur.fetchone()[0] == 2
    pg_conn.rollback()


def test_the_same_cluster_number_in_another_dataset_is_separate(pg_conn):
    with pg_conn.cursor() as cur:
        first, second = dataset(cur), dataset(cur)
        predict(cur, first, "0", "Columella")
        predict(cur, second, "0", "Phellem")
        cur.execute(
            "SELECT count(*) FROM scrna_cluster_cell_types WHERE dataset_id IN (%s, %s)",
            (first, second),
        )
        assert cur.fetchone()[0] == 2
    pg_conn.rollback()


def test_a_cluster_the_dataset_does_not_have_is_refused(pg_conn):
    with pg_conn.cursor() as cur:
        ds = dataset(cur, clusters=("0",))
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as err:
            predict(cur, ds, "7")
    assert err.value.diag.constraint_name == "scrna_cluster_cell_types_cluster_fkey"
    pg_conn.rollback()


def test_removing_a_cluster_removes_its_prediction(pg_conn):
    with pg_conn.cursor() as cur:
        ds = dataset(cur)
        predict(cur, ds, "1")
        cur.execute("DELETE FROM scrna_clusters WHERE dataset_id = %s AND cluster_id = '1'", (ds,))
        cur.execute("SELECT count(*) FROM scrna_cluster_cell_types WHERE dataset_id = %s", (ds,))
        assert cur.fetchone()[0] == 0
    pg_conn.rollback()


def test_renaming_a_cluster_carries_its_prediction(pg_conn):
    with pg_conn.cursor() as cur:
        ds = dataset(cur)
        predict(cur, ds, "1", "Phellem")
        cur.execute(
            "UPDATE scrna_clusters SET cluster_id = '10' WHERE dataset_id = %s AND cluster_id = '1'",
            (ds,),
        )
        cur.execute("SELECT cluster_id FROM scrna_cluster_cell_types WHERE dataset_id = %s", (ds,))
        assert cur.fetchone()[0] == "10"
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# What a row may not hold
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("blank", ["", "   ", "\t\n", " "])
def test_a_blank_cell_type_is_refused(pg_conn, blank):
    refused(pg_conn, "scrna_cluster_cell_types_cell_type_not_blank", cell_type=blank)


@pytest.mark.parametrize("blank", ["", "  ", " "])
def test_a_blank_source_is_refused(pg_conn, blank):
    refused(pg_conn, "scrna_cluster_cell_types_source_not_blank", source=blank)


@pytest.mark.parametrize("column, length", [("cell_type", 101), ("source", 201)])
def test_an_overlong_label_is_refused(pg_conn, column, length):
    refused(pg_conn, "scrna_cluster_cell_types_lengths", **{column: "x" * length})


@pytest.mark.parametrize("fraction", [0, -0.1, 1.01, float("nan")])
def test_a_fraction_outside_zero_to_one_is_refused(pg_conn, fraction):
    refused(pg_conn, "scrna_cluster_cell_types_fraction_range", fraction=fraction)


def test_a_fraction_of_exactly_one_is_allowed(pg_conn):
    with pg_conn.cursor() as cur:
        ds = dataset(cur)
        predict(cur, ds, fraction=1.0)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Who may read and write
# --------------------------------------------------------------------------- #

PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")
EXPECTED = {
    "anon": {"SELECT"},
    "authenticated": {"SELECT"},
    "bloom_user": {"SELECT"},
    "bloom_agent": {"SELECT"},
    "bloom_writer": {"SELECT", "INSERT", "UPDATE"},
    "bloom_admin": {"SELECT", "INSERT", "UPDATE", "DELETE"},
}


@pytest.mark.parametrize("role", sorted(EXPECTED))
def test_each_role_has_only_its_privileges(pg_conn, role):
    with pg_conn.cursor() as cur:
        held = set()
        for privilege in PRIVILEGES:
            cur.execute("SELECT has_table_privilege(%s, %s, %s)", (role, TABLE, privilege))
            if cur.fetchone()[0]:
                held.add(privilege)
    assert held == EXPECTED[role]


def test_row_level_security_is_on(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass", (TABLE,))
        assert cur.fetchone()[0] is True


def test_each_role_has_a_policy_for_what_it_may_do(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT unnest(roles)::text, cmd FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = 'scrna_cluster_cell_types'"
        )
        policies = set(cur.fetchall())
    assert policies == {
        ("anon", "SELECT"), ("authenticated", "SELECT"), ("bloom_user", "SELECT"),
        ("bloom_agent", "SELECT"), ("bloom_writer", "SELECT"), ("bloom_writer", "INSERT"),
        ("bloom_writer", "UPDATE"), ("bloom_admin", "ALL"),
    }


# --------------------------------------------------------------------------- #
# The migration runs again cleanly
# --------------------------------------------------------------------------- #


def test_the_migration_runs_again_as_the_newest(pg_conn):
    body = re.sub(r"^\s*(BEGIN|COMMIT);\s*$", "", MIGRATION.read_text(), flags=re.MULTILINE)
    with pg_conn.cursor() as cur:
        cur.execute(body)
        cur.execute(
            "SELECT count(*) FROM pg_constraint WHERE conrelid = %s::regclass", (TABLE,)
        )
        assert cur.fetchone()[0] == 6
    pg_conn.rollback()
