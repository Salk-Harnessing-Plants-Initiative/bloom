"""
Integration tests for the contrast dimension on `scrna_de`
(migration 20260908120000_scrna_de_add_contrast.sql).

`scrna_de` used to record one kind of result: a cluster against every other
cell, as a pointer to a file. It now also records a comparison between two named
groups -- one genotype against another within a cell type -- and a comparison
that was never run, which has group sizes but no file.

Each rejection test names the constraint it expects, because a row usually
breaks more than one rule and the first to fire wins. Without that, a test can
pass while the rule it is named for does nothing -- which is how
`scrna_de_no_file_means_nothing_tested` shipped with no coverage at all.

The privilege matrix is here for a different reason: the first cut of this
migration copied a grant block from a pre-hardening migration and silently
re-granted `bloom_user` UPDATE and `bloom_admin` TRUNCATE/TRIGGER/REFERENCES.
Comparing against an untouched sibling catches that whole class.

LOCAL ONLY: the `pg_conn` fixture connects to 127.0.0.1 on POSTGRES_HOST_PORT and
mutates nothing -- every test rolls back, leaving the database untouched. The
fixture connects as `supabase_admin`, which is BYPASSRLS, so policy checks read
the catalog rather than claiming to prove enforcement.

Runs in CI's `compose-health-check` job after migrations are applied
(`uv run --extra test pytest tests/integration/ -v`).
"""

import csv
import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
TABLE = "scrna_de"
# Created in the same era, carries no explicit grants of its own, and is
# untouched by this migration -- so its privileges are what correct looks like.
SIBLING = "scrna_cells"

ADDED_COLUMNS = [
    "contrast", "group1", "group2", "n_group1", "n_group2",
    "n_genes_tested", "n_significant_fdr", "n_significant_fdr_lfc",
    "n_up", "n_down",
]

ORIGINAL_COLUMNS = {"id", "dataset_id", "file_path", "cluster_id"}

# A self-consistent summary. Rows that name a contrast must carry all five counts,
# so a test aimed at some other rule spreads these in to stay on that rule.
COUNTS = {
    "n_genes_tested": 100,
    "n_significant_fdr": 5,
    "n_significant_fdr_lfc": 4,
    "n_up": 3,
    "n_down": 1,
}

# A verbatim copy of the pipeline's own summary for the first dataset. On the 23
# skipped comparisons `n_up` and `n_down` are blank -- the other three counts are
# a literal 0 -- and the test below turns those two blanks into zeros, which is
# the encoding this schema settled on. Reading the file rather than a hand-typed
# table is the point: it is the shape ingest has to accept.
SUMMARY_TSV = Path(__file__).parent / "fixtures" / "LEVEL1_DE_SUMMARY.tsv"

# Read off the live schema. Absolute, so a regrant applied to both this table and
# the sibling is still caught.
EXPECTED_PRIVILEGES = {
    "bloom_user": {"SELECT", "INSERT"},
    "bloom_agent": {"SELECT"},
    "bloom_admin": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "bloom_writer": {"SELECT", "INSERT", "UPDATE"},
}


def _seed_dataset(cur) -> int:
    """A species and dataset to hang DE rows off. Rolled back by the caller.

    `species.common_name`, `genus` and `species` are each UNIQUE, so the names
    have to be per-call unique or the file only works on an empty database.
    """
    tag = uuid.uuid4().hex[:10]
    cur.execute(
        "INSERT INTO species (common_name, genus, species) "
        "VALUES (%s, %s, %s) RETURNING id",
        (f"de-contrast-{tag}", f"Testus-{tag}", f"integrationis-{tag}"),
    )
    species_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO scrna_datasets (name, species_id) VALUES (%s, %s) RETURNING id",
        (f"de-contrast-{tag}", species_id),
    )
    return cur.fetchone()[0]


def _insert(cur, dataset_id, **cols):
    """Insert one scrna_de row. Unspecified columns keep their defaults."""
    cols.setdefault("cluster_id", "Phellem")
    names = ["dataset_id", *cols]
    values = [dataset_id, *cols.values()]
    placeholders = ", ".join(["%s"] * len(names))
    cur.execute(
        f"INSERT INTO {TABLE} ({', '.join(names)}) VALUES ({placeholders}) RETURNING id",
        values,
    )
    return cur.fetchone()[0]


def _rejects(cur, dataset_id, constraint, **cols):
    """Assert the insert is refused, and by which rule."""
    with pytest.raises(psycopg.errors.IntegrityError) as exc:
        _insert(cur, dataset_id, **cols)
    assert exc.value.diag.constraint_name == constraint, (
        f"expected {constraint}, got {exc.value.diag.constraint_name}"
    )


# --------------------------------------------------------------------------- #
# Shape
# --------------------------------------------------------------------------- #


def test_added_columns_exist(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (TABLE,),
        )
        present = {r[0] for r in cur.fetchall()}
        assert set(ADDED_COLUMNS) <= present


def test_file_path_is_nullable(pg_conn):
    """A comparison that was never run is a row with no file."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s "
            "AND column_name = 'file_path'",
            (TABLE,),
        )
        assert cur.fetchone()[0] == "YES"


# --------------------------------------------------------------------------- #
# Rows that must be accepted
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


def test_never_run_row_is_accepted(pg_conn):
    """No file, no results, but the group sizes say why."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _insert(
            cur, ds, file_path=None,
            contrast="pHORST_vs_Col-0", group1="pHORST", group2="Col-0",
            n_group1=0, n_group2=7,
            n_genes_tested=0, n_significant_fdr=0,
            n_significant_fdr_lfc=0, n_up=0, n_down=0,
        )
    pg_conn.rollback()


def test_one_cluster_holds_several_contrasts(pg_conn):
    """The feature. 23 cell types x 3 contrasts plus a marker list per cluster is
    the shape PRs after this one write, and it is what the uniqueness rule has to
    permit. Narrowing that rule's column list must turn this red."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        for contrast, g1, g2 in [
            ("pFACT_vs_Col-0", "pFACT", "Col-0"),
            ("pHORST_vs_Col-0", "pHORST", "Col-0"),
            ("pFACT_vs_pHORST", "pFACT", "pHORST"),
        ]:
            _insert(
                cur, ds, file_path=f"de/Phellem__{contrast}.json",
                contrast=contrast, group1=g1, group2=g2,
                n_group1=164, n_group2=21,
                n_genes_tested=12085, n_significant_fdr=1,
                n_significant_fdr_lfc=1, n_up=1, n_down=0,
            )
        _insert(cur, ds, file_path="de/markers_Phellem.json")

        cur.execute(
            f"SELECT count(*) FROM {TABLE} WHERE dataset_id = %s AND cluster_id = 'Phellem'",
            (ds,),
        )
        assert cur.fetchone()[0] == 4
    pg_conn.rollback()


def test_the_same_contrast_may_appear_on_different_clusters(pg_conn):
    """The other half of the key: one contrast spans every cell type."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        for cluster in ("Phellem", "Cortex", "Xylem"):
            _insert(
                cur, ds, cluster_id=cluster,
                file_path=f"de/{cluster}__pFACT_vs_Col-0.json",
                contrast="pFACT_vs_Col-0", group1="pFACT", group2="Col-0",
                n_group1=164, n_group2=21,
                n_genes_tested=12085, n_significant_fdr=0,
                n_significant_fdr_lfc=0, n_up=0, n_down=0,
            )
        cur.execute(f"SELECT count(*) FROM {TABLE} WHERE dataset_id = %s", (ds,))
        assert cur.fetchone()[0] == 3
    pg_conn.rollback()


def test_the_real_summary_file_loads(pg_conn):
    """Every row of the pipeline's own summary, read off disk. The 23 skipped
    comparisons leave n_up and n_down blank; storing them as zeros is the encoding
    the schema requires, and this is what proves the rules accept real output
    rather than only hand-typed rows."""
    with SUMMARY_TSV.open() as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    assert len(rows) == 69, f"expected 69 summary rows, found {len(rows)}"

    def count(value: str) -> int:
        # A blank means the comparison never ran, which this schema stores as 0.
        return int(float(value)) if value else 0

    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        for row in rows:
            ran = row["tested"] == "True"
            _insert(
                cur, ds,
                cluster_id=row["celltype"],
                file_path=(
                    f"de/{row['celltype']}__{row['contrast']}.json" if ran else None
                ),
                contrast=row["contrast"],
                group1=row["group1"], group2=row["group2"],
                n_group1=int(row["n_group1"]), n_group2=int(row["n_group2"]),
                n_genes_tested=count(row["n_genes_tested"]),
                n_significant_fdr=count(row["n_FDR_0.05"]),
                n_significant_fdr_lfc=count(row["n_FDR_0.05_abs_log2FC_0.5"]),
                n_up=count(row["n_up"]), n_down=count(row["n_down"]),
            )

        cur.execute(
            f"SELECT count(*) FILTER (WHERE file_path IS NOT NULL), "
            f"count(*) FILTER (WHERE file_path IS NULL) FROM {TABLE} "
            f"WHERE dataset_id = %s",
            (ds,),
        )
        assert cur.fetchone() == (46, 23)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# One test per rule, each naming the rule it expects
# --------------------------------------------------------------------------- #


def test_groups_without_a_contrast_are_rejected(pg_conn):
    """A NULL contrast marks a row as one-vs-rest and readers filter on it, so a
    two-group result with the label left off would be served as cluster markers."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_contrast_names_both_groups",
                 file_path="x.json", group1="pFACT", group2="Col-0")
    pg_conn.rollback()


def test_only_one_group_named_is_rejected(pg_conn):
    """One of the three present is as broken as two."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_contrast_names_both_groups",
                 file_path="x.json", group1="pFACT")
    pg_conn.rollback()


def test_contrast_without_both_groups_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_contrast_names_both_groups",
                 file_path="x.json", contrast="pFACT_vs_Col-0", group1="pFACT",
                 **COUNTS)
    pg_conn.rollback()


def test_group_compared_against_itself_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_groups_differ",
                 file_path="x.json", contrast="pFACT_vs_pFACT",
                 group1="pFACT", group2="pFACT", **COUNTS)
    pg_conn.rollback()


def test_half_filled_group_sizes_are_rejected(pg_conn):
    """One side sized and the other blank is a half-written row -- the same shape
    the five summary counts are guarded against."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_group_sizes_all_or_none",
                 file_path="x.json", contrast="a_vs_b", group1="a", group2="b",
                 n_group1=164, **COUNTS)
    pg_conn.rollback()


def test_group_sizes_without_a_comparison_are_rejected(pg_conn):
    """A one-vs-rest row has no groups, so it cannot have their sizes."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_group_sizes_all_or_none",
                 file_path="x.json", n_group1=100, n_group2=200)
    pg_conn.rollback()


def test_contrast_row_without_counts_is_rejected(pg_conn):
    """A named comparison carries all five counts, so a skipped one stores zeros
    rather than blanks -- otherwise the documented test for "never ran" yields
    NULL and the row cannot be found."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_contrast_rows_carry_counts",
                 file_path=None, contrast="a_vs_b", group1="a", group2="b",
                 n_group1=0, n_group2=7)
    pg_conn.rollback()


def test_no_file_but_results_reported_is_rejected(pg_conn):
    """The counts are internally consistent, so only the no-file rule can reject
    this. A row that also broke the arithmetic would be caught by a different
    constraint and prove nothing about this one."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_no_file_means_nothing_tested",
                 file_path=None, contrast="a_vs_b", group1="a", group2="b",
                 n_group1=0, n_group2=7,
                 n_genes_tested=500, n_significant_fdr=5,
                 n_significant_fdr_lfc=4, n_up=3, n_down=1)
    pg_conn.rollback()


def test_a_row_with_no_file_must_name_a_comparison(pg_conn):
    """Otherwise a bare row is storable and splits the two ways of counting
    skipped comparisons."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_no_file_means_nothing_tested", file_path=None)
    pg_conn.rollback()


def test_more_up_and_down_than_significant_is_rejected(pg_conn):
    """The sum is an equality, so over-counting is as wrong as under-counting."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_up_plus_down_is_lfc_significant",
                 file_path="x.json",
                 n_genes_tested=100, n_significant_fdr=9,
                 n_significant_fdr_lfc=4, n_up=4, n_down=3)
    pg_conn.rollback()


@pytest.mark.parametrize("column", ["group1", "group2"])
def test_oversized_group_name_is_rejected(pg_conn, column):
    row = {"file_path": "x.json", "contrast": "a_vs_b",
           "group1": "a", "group2": "b", **COUNTS}
    row[column] = "g" * 300
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_name_lengths", **row)
    pg_conn.rollback()


def test_the_same_comparison_may_appear_in_different_datasets(pg_conn):
    """dataset_id is part of the key: two datasets are independent."""
    with pg_conn.cursor() as cur:
        for _ in range(2):
            ds = _seed_dataset(cur)
            _insert(cur, ds, file_path="de/x.json", contrast="a_vs_b",
                    group1="a", group2="b", n_group1=1, n_group2=1, **COUNTS)
    pg_conn.rollback()


@pytest.mark.parametrize(
    "partial",
    [
        {"n_significant_fdr_lfc": 900},
        {"n_significant_fdr": 999},
        {"n_up": 50},
        {"n_genes_tested": 10, "n_significant_fdr": 5},
        {"n_genes_tested": 10, "n_significant_fdr": 5,
         "n_significant_fdr_lfc": 4, "n_up": 4},
    ],
    ids=["lfc-only", "fdr-only", "up-only", "two-of-five", "four-of-five"],
)
def test_half_written_summary_is_rejected(pg_conn, partial):
    """Every arithmetic rule compares two counts, and a comparison with a NULL
    operand yields NULL, which a CHECK accepts. Without this rule one absent
    count switches off all of them."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_counts_all_or_none",
                 file_path="x.json", **partial)
    pg_conn.rollback()


def test_more_significant_than_tested_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_significant_within_tested",
                 file_path="x.json",
                 n_genes_tested=10, n_significant_fdr=1000,
                 n_significant_fdr_lfc=1000, n_up=1000, n_down=0)
    pg_conn.rollback()


def test_second_cut_wider_than_the_first_is_rejected(pg_conn):
    """The fold-change cut applies on top of the FDR cut, so it can only narrow."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_lfc_cut_narrows_fdr_cut",
                 file_path="x.json",
                 n_genes_tested=12085, n_significant_fdr=5,
                 n_significant_fdr_lfc=9, n_up=9, n_down=0)
    pg_conn.rollback()


def test_up_and_down_not_summing_is_rejected(pg_conn):
    """A gene clearing a fold-change cut moved up or down; there is no third bucket."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_up_plus_down_is_lfc_significant",
                 file_path="x.json",
                 n_genes_tested=12085, n_significant_fdr=7,
                 n_significant_fdr_lfc=6, n_up=1, n_down=1)
    pg_conn.rollback()


@pytest.mark.parametrize(
    "column", ["n_group1", "n_group2", "n_genes_tested", "n_significant_fdr",
               "n_significant_fdr_lfc", "n_up", "n_down"]
)
def test_negative_count_is_rejected(pg_conn, column):
    counts = {"n_genes_tested": 10, "n_significant_fdr": 5,
              "n_significant_fdr_lfc": 4, "n_up": 4, "n_down": 0}
    counts[column] = -1
    if column in ("n_group1", "n_group2"):
        counts.setdefault("n_group1", 0)
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_counts_non_negative",
                 file_path="x.json", **counts)
    pg_conn.rollback()


@pytest.mark.parametrize(
    "blank",
    ["", "   ", "\t", "\n", "\r", "\u00a0"],
    ids=["empty", "spaces", "tab", "newline", "carriage-return", "nbsp"],
)
@pytest.mark.parametrize("column", ["file_path", "contrast", "group1", "group2", "cluster_id"])
def test_blank_text_is_rejected(pg_conn, column, blank):
    """NULL is the only way to say nothing. An empty contrast reads as
    one-vs-rest; an empty file_path renders as a link that goes nowhere."""
    row = {"file_path": "x.json", "contrast": "a_vs_b", "group1": "a", "group2": "b",
           **COUNTS}
    row[column] = blank
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_text_not_blank", **row)
    pg_conn.rollback()


def test_oversized_contrast_is_rejected(pg_conn):
    """contrast is indexed, so an oversized value would otherwise fail at insert
    with a btree row-size error rather than anything a reader could act on."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_name_lengths",
                 file_path="x.json", contrast="x" * 3000, group1="a", group2="b",
                 **COUNTS)
    pg_conn.rollback()


def test_oversized_cluster_id_is_rejected(pg_conn):
    """cluster_id is in the uniqueness index too, so it needs the same bound."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _rejects(cur, ds, "scrna_de_name_lengths",
                 cluster_id="c" * 3000, file_path="x.json")
    pg_conn.rollback()


def test_same_comparison_twice_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _insert(cur, ds, file_path="a.json", contrast="a_vs_b",
                group1="a", group2="b", **COUNTS)
        _rejects(cur, ds, "scrna_de_comparison_uniqueness",
                 file_path="b.json", contrast="a_vs_b", group1="a", group2="b",
                 **COUNTS)
    pg_conn.rollback()


def test_two_one_vs_rest_rows_for_one_cluster_are_rejected(pg_conn):
    """NULLS NOT DISTINCT. Without it Postgres treats every NULL contrast as
    unique and the rows every existing reader fetches stay unconstrained."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _insert(cur, ds, file_path="markers.json")
        _rejects(cur, ds, "scrna_de_comparison_uniqueness", file_path="other.json")
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Privileges -- the regression this file was written for
# --------------------------------------------------------------------------- #


def _table_privileges(cur, table: str) -> dict[str, set[str]]:
    cur.execute(
        "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema = 'public' AND table_name = %s",
        (table,),
    )
    out: dict[str, set[str]] = {}
    for grantee, privilege in cur.fetchall():
        out.setdefault(grantee, set()).add(privilege)
    return out


def test_privileges_match_an_untouched_sibling(pg_conn):
    """The migration grants nothing. Every role already holds what it needs from
    the ALL TABLES grant in 20260414002000, so any difference from a sibling
    means this migration re-granted something."""
    with pg_conn.cursor() as cur:
        assert _table_privileges(cur, TABLE) == _table_privileges(cur, SIBLING)


def test_bloom_role_privileges_are_exactly_as_expected(pg_conn):
    """Absolute, not relative: a regrant applied to both tables would pass the
    sibling comparison above."""
    with pg_conn.cursor() as cur:
        actual = {
            role: privs
            for role, privs in _table_privileges(cur, TABLE).items()
            if role.startswith("bloom_")
        }
        assert actual == EXPECTED_PRIVILEGES


def test_bloom_user_cannot_update(pg_conn):
    """20260710000000 removed bloom_user UPDATE across public."""
    with pg_conn.cursor() as cur:
        assert "UPDATE" not in _table_privileges(cur, TABLE).get("bloom_user", set())


def test_bloom_admin_has_no_truncate_trigger_or_references(pg_conn):
    """20260504000002 stripped these: TRUNCATE bypasses RLS and TRIGGER is a
    privilege-escalation vector."""
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
    """20260506000001 gave every scrna_* table these except scrna_de, so
    bloom_user reads returned no rows. The migration closes that gap."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT roles FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = %s AND policyname = %s",
            (TABLE, policy),
        )
        row = cur.fetchone()
        assert row is not None, f"{policy} missing"
        assert role in row[0]


def test_row_level_security_stays_enabled(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass", (TABLE,)
        )
        assert cur.fetchone()[0] is True


@pytest.mark.parametrize(
    "policy,cmd",
    [("admin_all_scrna_de", "ALL"),
     ("agent_read_scrna_de", "SELECT"),
     ("user_read_scrna_de", "SELECT")],
)
def test_role_policy_is_scoped_to_the_right_command(pg_conn, policy, cmd):
    """bloom_user holds the INSERT privilege, so a read policy widened to ALL
    would silently grant writes."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT cmd FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = %s AND policyname = %s",
            (TABLE, policy),
        )
        assert cur.fetchone()[0] == cmd


def test_pre_existing_policies_are_left_alone(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT policyname FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = %s",
            (TABLE,),
        )
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


def _rollback_body() -> str:
    """The rollback script without its BEGIN/COMMIT wrapper, so it runs inside
    the fixture's uncommitted transaction and leaves the schema untouched."""
    matches = sorted(
        (REPO_ROOT / "supabase" / "rollbacks").glob("*_scrna_de_add_contrast_rollback.sql")
    )
    assert matches, "rollback script not found"
    return "\n".join(
        line
        for line in matches[-1].read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def _table_is_empty(cur) -> bool:
    """The rollback guard counts committed rows, which a test transaction cannot
    hide. Once ingest has run these two cases are exercised by the real data."""
    cur.execute(f"SELECT count(*) FROM {TABLE}")
    return cur.fetchone()[0] == 0


def test_rollback_refuses_when_contrast_data_exists(pg_conn):
    """Nothing automated runs these scripts, so the only time one runs is by hand
    against a table someone has already filled. Dropping the columns would
    discard every contrast."""
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _insert(cur, ds, file_path="a.json", contrast="a_vs_b",
                group1="a", group2="b", **COUNTS)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            cur.execute(_rollback_body())
        assert "Refusing to roll back" in str(exc.value)
    pg_conn.rollback()


def test_rollback_refuses_when_a_row_has_no_file(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _seed_dataset(cur)
        _insert(cur, ds, file_path=None,
                contrast="a_vs_b", group1="a", group2="b",
                n_genes_tested=0, n_significant_fdr=0,
                n_significant_fdr_lfc=0, n_up=0, n_down=0)
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(_rollback_body())
    pg_conn.rollback()


def test_rollback_restores_the_original_shape(pg_conn):
    """With only old-style rows there is nothing to lose, so it runs."""
    with pg_conn.cursor() as cur:
        if not _table_is_empty(cur):
            pytest.skip("table already holds rows; the guard would refuse")
        ds = _seed_dataset(cur)
        _insert(cur, ds, file_path="de/legacy.json")
        before = _table_privileges(cur, TABLE)

        cur.execute(_rollback_body())

        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (TABLE,),
        )
        assert {r[0] for r in cur.fetchall()} == ORIGINAL_COLUMNS

        cur.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s "
            "AND column_name = 'file_path'",
            (TABLE,),
        )
        assert cur.fetchone()[0] == "NO"

        cur.execute(f"SELECT count(*) FROM {TABLE} WHERE dataset_id = %s", (ds,))
        assert cur.fetchone()[0] == 1, "the legacy row must survive"

        cur.execute(
            "SELECT policyname FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = %s",
            (TABLE,),
        )
        remaining = {r[0] for r in cur.fetchall()}
        assert not remaining & {
            "admin_all_scrna_de", "agent_read_scrna_de", "user_read_scrna_de"
        }

        cur.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = %s",
            (TABLE,),
        )
        indexes = {r[0] for r in cur.fetchall()}
        assert "scrna_de_comparison_uniqueness" not in indexes
        assert "idx_scrna_de_dataset_cluster" in indexes, "the older index comes back"

        # A guard, not an observation: the rollback revokes nothing, and adding a
        # REVOKE would strip access the table had before the migration.
        assert _table_privileges(cur, TABLE) == before
    pg_conn.rollback()
