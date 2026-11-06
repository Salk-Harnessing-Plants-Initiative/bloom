"""
Integration tests for migration 20261106180000_add_bloom_agent_data_reads.sql: what bloommcp's
data tools need from the database before they can answer questions through Claude.

- bloom_agent reads every public table with row security except the ones closed on purpose, and
  still cannot write them. The video job queues give it only the columns the web roles get.
- bloom_agent's queries stop after a fixed time, so one broad question can't hold the database.
- Gene search by id, symbol or standard name uses trigram indexes, not a scan of `genes`.
- bloom_agent can read the planner's row estimate for a table, so bloommcp can choose an exact or
  an approximate count; no other API role can call that function.

Requires a live compose stack with migrations applied. Uses `pg_conn` (supabase_admin); every
change is rolled back. Privileges are asserted with has_*_privilege, never by calling as a denied
role.
"""

import pytest

# The tables this migration opens to bloom_agent.
OPENED_TABLES = (
    "genes",
    "assemblies",
    "gene_candidate_support",
    "gene_candidate_scientists",
    "gene_orthologs",
    "ortho_gene_id_map",
    "gene_patents",
    "gene_progress_notes",
    "cyl_scan_videos",
    "cyl_video_jobs",
    "gravi_plate_video_jobs",
    "phenotypers",
    "plate_plant_traits_list",
    "plates_exp",
    "plates_scan_trait",
    "plates_trait_source",
    "translation_candidates",
    "translation_lines",
    "translation_project_users",
    "translation_projects",
)
# Internal bookkeeping bloom_agent may not read: the trait-count refresh's change log.
CLOSED_ON_PURPOSE = ("cyl_experiment_trait_count_changes",)
# Columns of the video job queues no API role reads: renderer error text and queue plumbing.
VIDEO_JOB_WITHHELD = ("error_message", "msg_id", "reads")
STATEMENT_TIMEOUT = "15s"
GENE_TRGM_INDEXES = {
    "genes_gene_id_trgm": "gene_id",
    "genes_symbol_trgm": "symbol",
    "genes_standard_name_trgm": "standard_name",
}
ROW_ESTIMATE_FN = "public.bloom_table_row_estimates(text[])"
OTHER_API_ROLES = ("anon", "authenticated", "bloom_user", "bloom_admin", "service_role")


@pytest.mark.parametrize("table", OPENED_TABLES)
def test_bloom_agent_has_a_read_policy_on_each_opened_table(pg_conn, table):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT cmd, qual FROM pg_policies
             WHERE schemaname = 'public' AND tablename = %s AND policyname = %s
               AND 'bloom_agent' = ANY (roles)
            """,
            (table, f"agent_read_{table}"),
        )
        assert cur.fetchall() == [("SELECT", "true")]


@pytest.mark.parametrize("table", OPENED_TABLES)
@pytest.mark.parametrize("privilege", ["INSERT", "UPDATE", "DELETE", "TRUNCATE"])
def test_bloom_agent_cannot_write_the_opened_tables(pg_conn, table, privilege):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_table_privilege('bloom_agent', %s, %s)",
            (f"public.{table}", privilege),
        )
        assert cur.fetchone()[0] is False


def test_every_table_with_row_security_is_readable_by_bloom_agent(pg_conn):
    """Both layers: a SELECT grant (on the table or some columns) and a read policy for bloom_agent
    (or for everyone). A new table missing either fails here unless it is closed on purpose."""
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relrowsecurity
               AND c.relname <> ALL (%s)
               AND (NOT has_any_column_privilege('bloom_agent', c.oid, 'SELECT')
                    OR NOT EXISTS (
                        SELECT 1 FROM pg_policies p
                         WHERE p.schemaname = 'public' AND p.tablename = c.relname
                           AND p.cmd IN ('SELECT', 'ALL')
                           AND (p.roles @> ARRAY['bloom_agent']::name[]
                                OR p.roles @> ARRAY['public']::name[])))
             ORDER BY 1
            """,
            (list(CLOSED_ON_PURPOSE),),
        )
        assert cur.fetchall() == []


@pytest.mark.parametrize("table", CLOSED_ON_PURPOSE)
def test_tables_closed_on_purpose_stay_closed(pg_conn, table):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_any_column_privilege('bloom_agent', %s, 'SELECT')",
            (f"public.{table}",),
        )
        assert cur.fetchone()[0] is False


@pytest.mark.parametrize("table", ["gravi_plate_video_jobs", "cyl_video_jobs"])
@pytest.mark.parametrize("column", VIDEO_JOB_WITHHELD)
def test_bloom_agent_never_reads_video_job_error_text(pg_conn, table, column):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_column_privilege('bloom_agent', %s, %s, 'SELECT')",
            (f"public.{table}", column),
        )
        assert cur.fetchone()[0] is False


def test_bloom_agent_reads_a_gene_row(pg_conn):
    """Before this migration the row was there but bloom_agent's select returned nothing."""
    with pg_conn.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO public.genes (gene_id, symbol) VALUES ('BLOOM-AGENT-PROBE-1', 'PROBE')"
            )
            cur.execute("SET LOCAL ROLE bloom_agent")
            cur.execute(
                "SELECT symbol FROM public.genes WHERE gene_id = 'BLOOM-AGENT-PROBE-1'"
            )
            assert cur.fetchall() == [("PROBE",)]
        finally:
            pg_conn.rollback()


def test_bloom_agent_queries_have_a_time_limit(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.setconfig FROM pg_db_role_setting s
              JOIN pg_roles r ON r.oid = s.setrole
             WHERE r.rolname = 'bloom_agent' AND s.setdatabase = 0
            """
        )
        settings = [c for row in cur.fetchall() for c in row[0]]
        assert f"statement_timeout={STATEMENT_TIMEOUT}" in settings


@pytest.mark.parametrize("index, column", GENE_TRGM_INDEXES.items())
def test_gene_search_columns_have_trigram_indexes(pg_conn, index, column):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = %s",
            (index,),
        )
        row = cur.fetchone()
        assert row is not None, f"{index} is missing"
        assert f"gin ({column} gin_trgm_ops)" in row[0]


def test_a_substring_gene_search_uses_the_trigram_index(pg_conn):
    """CI's genes table is tiny, so the planner would scan it anyway; with sequential scans off,
    the plan proves the index can serve an ILIKE '%...%' search."""
    with pg_conn.cursor() as cur:
        try:
            cur.execute("SET LOCAL enable_seqscan = off")
            cur.execute(
                "EXPLAIN SELECT gene_id FROM public.genes WHERE symbol ILIKE '%PHOT%'"
            )
            plan = "\n".join(r[0] for r in cur.fetchall())
            assert "genes_symbol_trgm" in plan, plan
        finally:
            pg_conn.rollback()


def test_only_bloom_agent_can_read_row_estimates(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_function_privilege('bloom_agent', %s, 'EXECUTE')",
            (ROW_ESTIMATE_FN,),
        )
        assert cur.fetchone()[0] is True
        for role in OTHER_API_ROLES:
            cur.execute(
                "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                (role, ROW_ESTIMATE_FN),
            )
            assert cur.fetchone()[0] is False, (
                f"{role} can call the row estimate function"
            )


def test_row_estimates_come_from_the_planner_for_public_tables_only(pg_conn):
    with pg_conn.cursor() as cur:
        try:
            cur.execute("ANALYZE public.species")
            cur.execute(
                "SELECT reltuples::bigint FROM pg_class WHERE oid = 'public.species'::regclass"
            )
            expected = cur.fetchone()[0]
            cur.execute("SET LOCAL ROLE bloom_agent")
            cur.execute(
                "SELECT table_name, row_estimate FROM public.bloom_table_row_estimates(%s)",
                (["species", "pg_class", "not_a_table"],),
            )
            assert cur.fetchall() == [("species", expected)]
        finally:
            pg_conn.rollback()


def test_a_never_analyzed_table_has_no_estimate(pg_conn):
    """reltuples is -1 until a table is first analyzed; that must read as unknown, not -1 rows."""
    with pg_conn.cursor() as cur:
        try:
            cur.execute("CREATE TABLE public.bloom_estimate_probe (id int)")
            cur.execute("SET LOCAL ROLE bloom_agent")
            cur.execute(
                "SELECT table_name, row_estimate FROM public.bloom_table_row_estimates(%s)",
                (["bloom_estimate_probe"],),
            )
            assert cur.fetchall() == [("bloom_estimate_probe", None)]
        finally:
            pg_conn.rollback()
