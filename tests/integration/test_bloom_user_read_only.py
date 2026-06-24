"""
Integration tests for the `bloom_user` read-only contract (issue #341).

`bloom_user` is intended read-only: it may SELECT and INSERT on `public`, but
UPDATE is denied on every `public` table EXCEPT `public.experiment_progress_logs`
(the gene-page "Progress" panel writes there via a `USING (true)` RLS policy that
never touches the `auth` schema).

Migration `20260624000000_bloom_user_read_only_cleanup.sql` enforces this by
revoking the blanket table-level UPDATE grant (so a non-exempt UPDATE is rejected
at the privilege check, SQLSTATE 42501 — *before* RLS is evaluated) and dropping
the five now-dead `user_update_*` policies, four of which gated on
`created_by = auth.uid()` and were inert only because `bloom_user` lacks `auth`
USAGE. Removing the grant makes the read-only design explicit instead of relying
on that withheld grant.

Requires a live compose stack with migrations applied (the same harness as
`test_migrations.py`). Uses the `pg_conn` fixture (connects as `supabase_admin`);
every role switch / mutation is rolled back so no state leaks.

NOTE: this test does NOT grant `auth` USAGE to any role — the intentional
auth-schema gap (#341/#333) is owned by the schema-USAGE health-check matrix.
"""

import psycopg
import pytest

# The five inert UPDATE policies the migration drops (all on public tables).
DROPPED_UPDATE_POLICIES = {
    "user_update_accessions": "accessions",
    "user_update_chat_threads": "chat_threads",
    "user_update_cyl_experiments": "cyl_experiments",
    "user_update_gene_candidates": "gene_candidates",
    "user_update_species": "species",
}
RETAINED_UPDATE_POLICY = (
    "user_update_experiment_progress_logs",
    "experiment_progress_logs",
)


def _has_priv(cur, role: str, table: str, priv: str) -> bool:
    cur.execute("SELECT has_table_privilege(%s, %s, %s)", (role, table, priv))
    return cur.fetchone()[0]


def test_update_general_public_table_denied_at_privilege_layer(pg_conn):
    """UPDATE public.species as bloom_user raises 42501 (grant revoked, not just
    the RLS policy), and the row is unchanged."""
    with pg_conn.cursor() as cur:
        try:
            # Seed a row inside the txn (superuser bypasses RLS); SAVEPOINT before
            # the failing UPDATE so we can recover the aborted transaction and read
            # the row back to prove it is unchanged.
            cur.execute(
                "INSERT INTO public.species (common_name) VALUES ('rls-probe') RETURNING id"
            )
            species_id = cur.fetchone()[0]
            cur.execute("SAVEPOINT before_update")
            cur.execute("SET LOCAL ROLE bloom_user")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(
                    "UPDATE public.species SET common_name = 'mutated' WHERE id = %s",
                    (species_id,),
                )
            # Aborted txn: recover to the savepoint (also reverts SET LOCAL ROLE).
            cur.execute("ROLLBACK TO SAVEPOINT before_update")
            cur.execute(
                "SELECT common_name FROM public.species WHERE id = %s", (species_id,)
            )
            assert cur.fetchone()[0] == "rls-probe", "row must be unchanged"
        finally:
            pg_conn.rollback()


def test_update_accessions_denied(pg_conn):
    """accessions is the ONE dropped policy that was USING (true) — i.e. the only
    real capability removal (bloom_user could UPDATE accessions before this change;
    the other four user_update_* policies gated on the unreachable auth.uid()).
    Pin that it is now denied at the privilege layer, and the row is unchanged."""
    with pg_conn.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO public.accessions (name) VALUES ('acc-probe') RETURNING id"
            )
            accession_id = cur.fetchone()[0]
            cur.execute("SAVEPOINT before_update")
            cur.execute("SET LOCAL ROLE bloom_user")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(
                    "UPDATE public.accessions SET name = 'mutated' WHERE id = %s",
                    (accession_id,),
                )
            cur.execute("ROLLBACK TO SAVEPOINT before_update")
            cur.execute(
                "SELECT name FROM public.accessions WHERE id = %s", (accession_id,)
            )
            assert cur.fetchone()[0] == "acc-probe", "row must be unchanged"
        finally:
            pg_conn.rollback()


def test_update_experiment_progress_logs_allowed(pg_conn):
    """The one retained write path: bloom_user may UPDATE experiment_progress_logs."""
    with pg_conn.cursor() as cur:
        try:
            # experiment_progress_logs.gene → gene_candidates.gene → genes.gene.
            # Seed the FK chain in the same (rolled-back) txn.
            cur.execute("INSERT INTO public.genes (gene_id) VALUES ('TEST_GENE_341')")
            cur.execute(
                "INSERT INTO public.gene_candidates (gene) VALUES ('TEST_GENE_341')"
            )
            cur.execute(
                "INSERT INTO public.experiment_progress_logs (gene, message) "
                "VALUES ('TEST_GENE_341', 'seed') RETURNING id"
            )
            log_id = cur.fetchone()[0]
            cur.execute("SET LOCAL ROLE bloom_user")
            cur.execute(
                "UPDATE public.experiment_progress_logs SET message = 'updated' WHERE id = %s",
                (log_id,),
            )
            assert cur.rowcount == 1
        finally:
            pg_conn.rollback()
    with pg_conn.cursor() as cur:
        assert (
            _has_priv(cur, "bloom_user", "public.experiment_progress_logs", "UPDATE")
            is True
        )


def test_read_and_insert_preserved(pg_conn):
    """The revoke is UPDATE-only: SELECT and INSERT on public.species are kept."""
    with pg_conn.cursor() as cur:
        assert _has_priv(cur, "bloom_user", "public.species", "SELECT") is True
        assert _has_priv(cur, "bloom_user", "public.species", "INSERT") is True
        assert _has_priv(cur, "bloom_user", "public.species", "UPDATE") is False


def test_future_tables_do_not_regrant_update(pg_conn):
    """ALTER DEFAULT PRIVILEGES FOR ROLE postgres REVOKE UPDATE: a new postgres-
    created public table grants bloom_user SELECT+INSERT but not UPDATE."""
    with pg_conn.cursor() as cur:
        try:
            # Create as postgres — the role whose default privileges the migration
            # altered. (db push applies migration DDL as postgres.)
            cur.execute("SET LOCAL ROLE postgres")
            cur.execute("CREATE TABLE public._adp_probe_341 (id int primary key)")
            assert (
                _has_priv(cur, "bloom_user", "public._adp_probe_341", "UPDATE") is False
            )
            assert (
                _has_priv(cur, "bloom_user", "public._adp_probe_341", "SELECT") is True
            )
            assert (
                _has_priv(cur, "bloom_user", "public._adp_probe_341", "INSERT") is True
            )
        finally:
            pg_conn.rollback()  # drops the probe table


def test_admin_and_agent_unaffected(pg_conn):
    """Regression guard: bloom_admin keeps full CRUD, bloom_agent stays read-only."""
    with pg_conn.cursor() as cur:
        assert _has_priv(cur, "bloom_admin", "public.species", "UPDATE") is True
        assert _has_priv(cur, "bloom_agent", "public.species", "UPDATE") is False
        assert _has_priv(cur, "bloom_agent", "public.species", "SELECT") is True


def test_dropped_policies_absent_retained_present(pg_conn):
    """The five inert user_update_* policies are gone; the epl one is kept."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT policyname FROM pg_policies "
            "WHERE schemaname = 'public' AND policyname = ANY(%s)",
            (list(DROPPED_UPDATE_POLICIES) + [RETAINED_UPDATE_POLICY[0]],),
        )
        present = {row[0] for row in cur.fetchall()}
    not_dropped = present & set(DROPPED_UPDATE_POLICIES)
    assert not not_dropped, f"should have been dropped: {not_dropped}"
    assert RETAINED_UPDATE_POLICY[0] in present, "epl UPDATE policy must remain"


def test_no_structural_path_to_reenable_writes(pg_conn):
    """A hypothetical future `GRANT USAGE ON SCHEMA auth TO bloom_user` cannot
    re-enable writes, because neither precondition survives: no blanket public
    UPDATE grant AND no created_by = auth.uid() UPDATE policy. Asserted from schema
    state only — this test never grants auth USAGE."""
    with pg_conn.cursor() as cur:
        # No blanket UPDATE grant: only experiment_progress_logs remains updatable.
        cur.execute(
            "SELECT table_name FROM information_schema.role_table_grants "
            "WHERE grantee = 'bloom_user' AND privilege_type = 'UPDATE' "
            "AND table_schema = 'public'"
        )
        updatable = {row[0] for row in cur.fetchall()}
        assert updatable == {"experiment_progress_logs"}, updatable
        # No created_by = auth.uid() UPDATE policy remains for bloom_user.
        cur.execute(
            "SELECT policyname FROM pg_policies "
            "WHERE schemaname = 'public' AND policyname = ANY(%s)",
            (list(DROPPED_UPDATE_POLICIES),),
        )
        assert cur.fetchall() == []


def test_cleanup_statements_are_idempotent(pg_conn):
    """The migration's idempotent constructs (DROP POLICY IF EXISTS + REVOKE/GRANT)
    re-run cleanly. Runs them twice inside a rolled-back txn — no COMMIT, so the
    live DB is untouched. (End-to-end db push idempotency is covered separately by
    test_migrations.py::test_db_push_is_idempotent.)"""
    statements = [
        "REVOKE UPDATE ON ALL TABLES IN SCHEMA public FROM bloom_user",
        "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public "
        "REVOKE UPDATE ON TABLES FROM bloom_user",
        "GRANT UPDATE ON public.experiment_progress_logs TO bloom_user",
        *[
            f"DROP POLICY IF EXISTS {name} ON public.{tbl}"
            for name, tbl in DROPPED_UPDATE_POLICIES.items()
        ],
    ]
    with pg_conn.cursor() as cur:
        try:
            for _ in range(2):
                for stmt in statements:
                    cur.execute(stmt)
        finally:
            pg_conn.rollback()
