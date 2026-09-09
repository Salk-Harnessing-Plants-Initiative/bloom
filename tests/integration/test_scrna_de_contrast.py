"""
Integration tests for the contrast dimension on `scrna_de`
(migration 20260908120000_scrna_de_add_contrast.sql).

`scrna_de` used to record one kind of result: a cluster against every other cell,
as a pointer to a file. It now also records a comparison between two named groups
— one genotype against another within a cell type — and a comparison that was
never run, which has group sizes but no file.

These tests assert: the added columns; that `file_path` is nullable; the four
CHECK constraints, each by the case it is there to reject; that legacy one-vs-rest
rows are unaffected; the bloom_* policies the migration adds; and — the reason
this file exists — that the migration grants no table privileges. It originally
copied a grant block from a pre-hardening migration and silently re-granted
`bloom_user` UPDATE (removed by 20260710000000) and `bloom_admin`
TRUNCATE/REFERENCES/TRIGGER (removed by 20260504000002). A privilege matrix
against an untouched sibling table catches that class of regression.

LOCAL ONLY: the `pg_conn` fixture connects to 127.0.0.1 on POSTGRES_HOST_PORT and
mutates nothing — every test rolls back, leaving the database untouched. The
fixture connects as `supabase_admin`, which is BYPASSRLS, so policy checks read
the catalog rather than claiming to prove enforcement.

Runs in CI's `compose-health-check` job after migrations are applied
(`uv run --extra test pytest tests/integration/ -v`).
"""

import re
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
TABLE = "scrna_de"
# Created in the same era, carries no explicit grants of its own, and is
# untouched by this migration — so its privileges are what correct looks like.
SIBLING = "scrna_cells"

ADDED_COLUMNS = [
    "contrast",
    "group1",
    "group2",
    "n_group1",
    "n_group2",
    "n_genes_tested",
    "n_significant_fdr",
    "n_significant_fdr_lfc",
    "n_up",
    "n_down",
]


def _seed_dataset(cur) -> int:
    """A species and dataset to hang DE rows off. Rolled back by the caller."""
    cur.execute(
        "INSERT INTO species (common_name, genus, species) "
        "VALUES ('test', 'Testus', 'integrationis') RETURNING id"
    )
    species_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO scrna_datasets (name, species_id) VALUES (%s, %s) RETURNING id",
        (f"de-contrast-test-{species_id}", species_id),
    )
    return cur.fetchone()[0]


def _insert(cur, dataset_id, **cols):
    """Insert one scrna_de row. Unspecified columns are left to their defaults."""
    cols.setdefault("cluster_id", "Phellem")
    names = ["dataset_id", *cols]
    values = [dataset_id, *cols.values()]
    placeholders = ", ".join(["%s"] * len(names))
    cur.execute(
        f"INSERT INTO {TABLE} ({', '.join(names)}) VALUES ({placeholders}) RETURNING id",
        values,
    )
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# Shape
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("column", ADDED_COLUMNS)
def test_added_column_exists(pg_conn, column):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
            (TABLE, column),
        )
        assert cur.fetchone() is not None, f"{column} missing from {TABLE}"


def test_file_path_is_nullable(pg_conn):
    """A comparison that was never run is a row with no file."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s AND column_name = 'file_path'",
            (TABLE,),
        )
        assert cur.fetchone()[0] == "YES"


# --------------------------------------------------------------------------- #
# A contrast and its two group names travel together
# --------------------------------------------------------------------------- #


def test_two_group_row_is_accepted(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _insert(
            cur, ds,
            file_path="de/Phellem__pFACT_vs_Col-0.json",
            contrast="pFACT_vs_Col-0", group1="pFACT", group2="Col-0",
            n_group1=164, n_group2=21,
            n_genes_tested=12085, n_significant_fdr=1,
            n_significant_fdr_lfc=1, n_up=1, n_down=0,
        )
    pg_conn.rollback()


def test_legacy_one_vs_rest_row_is_accepted(pg_conn):
    """Every row predating the migration: a file, a cluster, nothing else."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        rid = _insert(cur, ds, file_path="de/legacy_Phellem.json")
        cur.execute(f"SELECT contrast, group1, group2 FROM {TABLE} WHERE id = %s", (rid,))
        assert cur.fetchone() == (None, None, None)
    pg_conn.rollback()


def test_groups_without_a_contrast_are_rejected(pg_conn):
    """The one that matters: a NULL contrast marks a row as one-vs-rest, and
    readers filter on it. A two-group result with the label left off would be
    served as cluster markers."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(cur, ds, file_path="x.json", group1="pFACT", group2="Col-0")
    pg_conn.rollback()


def test_contrast_without_both_groups_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(cur, ds, file_path="x.json", contrast="pFACT_vs_Col-0", group1="pFACT")
    pg_conn.rollback()


def test_group_compared_against_itself_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(
                cur, ds, file_path="x.json",
                contrast="pFACT_vs_pFACT", group1="pFACT", group2="pFACT",
            )
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# A comparison that never ran reports nothing
# --------------------------------------------------------------------------- #


def test_never_run_row_is_accepted(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _insert(
            cur, ds, file_path=None,
            contrast="pHORST_vs_Col-0", group1="pHORST", group2="Col-0",
            n_group1=0, n_group2=7, n_genes_tested=0,
        )
    pg_conn.rollback()


def test_never_run_row_claiming_results_is_rejected(pg_conn):
    """No file means no result, so every count is zero — not just the gene count."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(
                cur, ds, file_path=None,
                contrast="pHORST_vs_Col-0", group1="pHORST", group2="Col-0",
                n_genes_tested=0, n_significant_fdr=500, n_up=300, n_down=200,
            )
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# The summary counts cannot contradict each other
# --------------------------------------------------------------------------- #


def test_more_significant_than_tested_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(
                cur, ds, file_path="x.json",
                n_genes_tested=10, n_significant_fdr=1000,
            )
    pg_conn.rollback()


def test_second_cut_wider_than_the_first_is_rejected(pg_conn):
    """The fold-change cut applies on top of the FDR cut, so it can only narrow."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(
                cur, ds, file_path="x.json",
                n_genes_tested=12085, n_significant_fdr=5,
                n_significant_fdr_lfc=9, n_up=9, n_down=0,
            )
    pg_conn.rollback()


def test_up_and_down_not_summing_is_rejected(pg_conn):
    """A gene clearing a fold-change cut moved up or down; there is no third bucket."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(
                cur, ds, file_path="x.json",
                n_genes_tested=12085, n_significant_fdr=7,
                n_significant_fdr_lfc=6, n_up=1, n_down=1,
            )
    pg_conn.rollback()


def test_negative_count_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(cur, ds, file_path="x.json", n_group1=-1)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Privileges — the regression this file was written for
# --------------------------------------------------------------------------- #


def _table_privileges(cur, table: str) -> dict[str, set[str]]:
    cur.execute(
        "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema = 'public' AND table_name = %s AND grantee LIKE 'bloom%%'",
        (table,),
    )
    out: dict[str, set[str]] = {}
    for grantee, privilege in cur.fetchall():
        out.setdefault(grantee, set()).add(privilege)
    return out


def test_privileges_match_an_untouched_sibling(pg_conn):
    """The migration must grant nothing. Every bloom_* role already holds what it
    needs from the ALL TABLES grant in 20260414002000, so any difference from a
    sibling table means this migration re-granted something — which is how it
    first re-opened two deliberately closed doors."""
    with pg_conn.cursor() as cur:
        assert _table_privileges(cur, TABLE) == _table_privileges(cur, SIBLING)


def test_bloom_user_cannot_update(pg_conn):
    """20260710000000 removed bloom_user UPDATE across public. Re-granting it here
    also fails test_bloom_user_read_only.py, which asserts the updatable set."""
    with pg_conn.cursor() as cur:
        assert "UPDATE" not in _table_privileges(cur, TABLE).get("bloom_user", set())


def test_bloom_admin_has_no_truncate_trigger_or_references(pg_conn):
    """20260504000002 stripped these because TRUNCATE bypasses RLS and TRIGGER is
    a privilege-escalation vector. `GRANT ALL` puts them back."""
    with pg_conn.cursor() as cur:
        held = _table_privileges(cur, TABLE).get("bloom_admin", set())
        assert not held & {"TRUNCATE", "TRIGGER", "REFERENCES"}


# --------------------------------------------------------------------------- #
# Policies
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "policy,role",
    [
        ("admin_all_scrna_de", "bloom_admin"),
        ("agent_read_scrna_de", "bloom_agent"),
        ("user_read_scrna_de", "bloom_user"),
    ],
)
def test_role_policy_exists(pg_conn, policy, role):
    """20260506000001 gave every scrna_* table these except scrna_de, so bloom_user
    reads returned no rows. The migration closes that gap."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT roles FROM pg_policies WHERE tablename = %s AND policyname = %s",
            (TABLE, policy),
        )
        row = cur.fetchone()
        assert row is not None, f"{policy} missing"
        assert role in row[0]


def test_pre_existing_policies_are_left_alone(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT policyname FROM pg_policies WHERE tablename = %s", (TABLE,))
        names = {r[0] for r in cur.fetchall()}
        assert {
            "Authenticated users can insert scrna_de",
            "Authenticated users can update scrna_de",
            "Authenticated users can read scrna_de",
            "Anon users can select scrna_de",
        } <= names


# --------------------------------------------------------------------------- #
# Rollback
# --------------------------------------------------------------------------- #


def _rollback_sql() -> Path | None:
    matches = sorted(
        (REPO_ROOT / "supabase" / "rollbacks").glob("*_scrna_de_add_contrast_rollback.sql")
    )
    return matches[-1] if matches else None


def test_rollback_restores_the_original_shape(pg_conn):
    """CI only rolls forward, so apply the rollback body inside the fixture's
    uncommitted transaction, assert, then ROLLBACK so nothing else is affected."""
    path = _rollback_sql()
    if path is None:
        pytest.skip("rollback script not written yet")

    body = "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )
    with pg_conn.cursor() as cur:
        before = _table_privileges(cur, TABLE)
        cur.execute(body)

        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (TABLE,),
        )
        remaining = {r[0] for r in cur.fetchall()}
        assert remaining == {"id", "dataset_id", "file_path", "cluster_id"}

        cur.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s AND column_name = 'file_path'",
            (TABLE,),
        )
        assert cur.fetchone()[0] == "NO", "file_path should be NOT NULL again"

        # The rollback revokes nothing: those privileges predate the migration.
        assert _table_privileges(cur, TABLE) == before
    pg_conn.rollback()
