"""
Integration tests for the pipeline loading a Cell Ranger run's dataset into Bloom:
- bloom_workflows can create a dataset and add its clusters, genotypes, cells, genes and
  counts, and finish it, as `bloomctl scrna hdf5 upload` does;
- it reads every table the load reads, through its own row rules;
- it writes only datasets it created and hasn't finished, only the columns the load sets, and
  deletes nothing;
- in the `scrna` bucket it writes only under h5ad/ and counts/;
- only bloom_workflows may call link_rnaseq_run_dataset (its behaviour is in
  test_rnaseq_run_dataset_link.py);
- a run can report the load-dataset step.

LOCAL ONLY: `pg_conn` connects as `supabase_admin` (BYPASSRLS) and every test rolls back.
Privileges a role is denied are checked with has_*_privilege, not by calling as that role.
"""

import pytest

from tests.integration import _scrna_pipeline_load as load

psycopg = pytest.importorskip("psycopg")

# The statement storage-api v1.48.14 runs as the caller's role for an upload with upsert.
_STORAGE_UPSERT = """
    INSERT INTO storage.objects
      (name, owner, owner_id, bucket_id, metadata, user_metadata, version)
    VALUES (%s, NULL, NULL, %s, '{}', '{}', %s)
    ON CONFLICT (name, bucket_id) DO UPDATE
      SET metadata = EXCLUDED.metadata,
          user_metadata = EXCLUDED.user_metadata,
          version = EXCLUDED.version,
          owner = EXCLUDED.owner,
          owner_id = EXCLUDED.owner_id
    RETURNING *
"""


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        yield c
    pg_conn.rollback()


@pytest.fixture
def pipeline(cur):
    return load.user(cur, workflows=True)


@pytest.fixture
def scientist(cur):
    return load.user(cur)


@pytest.fixture
def species(cur):
    return load.species(cur)


def _bucket(cur, bucket="scrna"):
    cur.execute("INSERT INTO storage.buckets (id, name) VALUES (%s, %s) "
                "ON CONFLICT (id) DO NOTHING", (bucket, bucket))


# --------------------------------------------------------------------------- #
# Privileges
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("table, writes", [
    ("scrna_datasets", (True, True)),
    *((t, (True, False)) for t in load.CHILD_TABLES),
    *((t, (False, False)) for t in load.READ_ONLY_TABLES),
])
def test_the_pipeline_reads_writes_only_what_the_load_does_and_never_deletes(cur, table, writes):
    t = f"public.{table}"
    cur.execute("SELECT has_table_privilege('bloom_workflows', %s, 'SELECT'), "
                "has_any_column_privilege('bloom_workflows', %s, 'INSERT'), "
                "has_any_column_privilege('bloom_workflows', %s, 'UPDATE'), "
                "has_table_privilege('bloom_workflows', %s, 'DELETE'), "
                "has_table_privilege('bloom_workflows', %s, 'TRUNCATE')", (t, t, t, t, t))
    assert cur.fetchone() == (True, *writes, False, False)


@pytest.mark.parametrize("table", ["scrna_datasets", *load.CHILD_TABLES, *load.READ_ONLY_TABLES])
def test_the_pipeline_reads_every_row_through_its_own_rule(cur, table):
    # has_table_privilege ignores row rules; without this rule the reads return nothing.
    cur.execute("SELECT cmd, roles::text, qual FROM pg_policies WHERE schemaname = 'public' "
                "AND tablename = %s AND policyname = %s", (table, f"workflows_select_{table}"))
    assert cur.fetchone() == ("SELECT", "{bloom_workflows}", "true")


def test_the_pipeline_writes_only_the_dataset_columns_the_load_sets(cur):
    cur.execute("SELECT privilege_type, array_agg(column_name::text ORDER BY column_name) "
                "FROM information_schema.column_privileges WHERE grantee = 'bloom_workflows' "
                "AND table_schema = 'public' AND table_name = 'scrna_datasets' "
                "AND privilege_type IN ('INSERT', 'UPDATE') GROUP BY privilege_type")
    assert dict(cur.fetchall()) == {
        "INSERT": ["metadata", "name", "source_checksum", "species_id"],
        "UPDATE": ["expression_units", "ingested_at", "metadata", "n_cells", "n_genes"],
    }


@pytest.mark.parametrize("role, allowed", [
    ("bloom_workflows", True), ("bloom_user", False), ("bloom_writer", False),
    ("bloom_admin", False), ("bloom_agent", False), ("authenticated", False), ("anon", False),
])
def test_only_the_pipeline_may_call_the_new_functions(cur, role, allowed):
    for fn in (load.LINK, load.MAY_LOAD):
        cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, fn))
        assert cur.fetchone()[0] is allowed, f"{role} {fn}"


def test_the_pipeline_cant_bypass_rls_and_the_new_functions_pin_their_search_path(cur):
    cur.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'bloom_workflows'")
    assert cur.fetchone()[0] is False
    cur.execute(
        "SELECT proname, prosecdef, proconfig FROM pg_proc WHERE pronamespace = "
        "'public'::regnamespace AND proname IN ('link_rnaseq_run_dataset', "
        "'workflows_may_load_scrna_dataset', 'set_created_by_as_owner') ORDER BY proname"
    )
    rows = cur.fetchall()
    assert [r[0] for r in rows] == ["link_rnaseq_run_dataset", "set_created_by_as_owner",
                                    "workflows_may_load_scrna_dataset"]
    for name, definer, config in rows:
        assert definer and any(c.startswith("search_path=") for c in config or []), name


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def test_the_pipeline_loads_a_dataset_as_bloomctl_does(cur, pipeline, species):
    load.sign_in(cur, pipeline)
    dataset_id, created_by = load.create(cur, species)
    assert created_by == pipeline, "created_by isn't the pipeline account"
    load.fill(cur, dataset_id)
    assert load.finish(cur, dataset_id) == 1
    cur.execute("SELECT count(*) FROM public.scrna_cells WHERE dataset_id = %s", (dataset_id,))
    assert cur.fetchone()[0] == 1


def test_a_finished_dataset_cant_be_changed_or_added_to(cur, pipeline, species):
    dataset_id = load.loaded(cur, pipeline, species)
    load.sign_in(cur, pipeline)
    assert load.finish(cur, dataset_id) == 0, "the pipeline changed a finished dataset"
    load.refused(cur, "INSERT INTO public.scrna_genes (dataset_id, gene_number, gene_name) "
                 "VALUES (%s, 1, 'AT1G01020')", (dataset_id,))


def test_a_removed_dataset_cant_be_added_to(cur, pipeline, species):
    load.sign_in(cur, pipeline)
    dataset_id, _ = load.create(cur, species)
    cur.execute("RESET ROLE")
    cur.execute("UPDATE public.scrna_datasets SET deleted_at = now() WHERE id = %s", (dataset_id,))
    load.sign_in(cur, pipeline)
    load.refused(cur, "INSERT INTO public.scrna_genes (dataset_id, gene_number, gene_name) "
                 "VALUES (%s, 1, 'AT1G01020')", (dataset_id,))


def test_another_accounts_dataset_cant_be_changed_or_added_to(cur, pipeline, scientist, species):
    load.sign_in(cur, scientist, role="bloom_writer")
    dataset_id, _ = load.create(cur, species)
    cur.execute("RESET ROLE")
    load.sign_in(cur, pipeline)
    assert load.finish(cur, dataset_id) == 0, "the pipeline changed a scientist's dataset"
    load.refused(cur, "INSERT INTO public.scrna_clusters (dataset_id, cluster_id, ordinal) "
                 "VALUES (%s, '9', 9)", (dataset_id,))
    load.refused(cur, "INSERT INTO public.scrna_cells (dataset_id, cell_number, cluster_id) "
                 "VALUES (%s, 9, '9')", (dataset_id,))


def test_a_dataset_cant_be_created_finished_or_owned(cur, pipeline, scientist, species):
    load.sign_in(cur, pipeline)
    load.refused(cur, "INSERT INTO public.scrna_datasets (name, species_id, ingested_at) "
                 "VALUES ('Sneaky', %s, now())", (species,))
    load.refused(cur, "INSERT INTO public.scrna_datasets (name, species_id, created_by) "
                 "VALUES ('Sneaky', %s, %s)", (species, scientist))


def test_the_trigger_still_sets_created_by_for_a_writer(cur, scientist, species):
    load.sign_in(cur, scientist, role="bloom_writer")
    assert load.create(cur, species)[1] == scientist


@pytest.mark.parametrize("path", ["h5ad/" + "b" * 64 + ".h5ad.gz",
                                  "counts/Root atlas_1_/AT1G01010.json"])
def test_the_pipeline_stores_the_file_and_counts_as_storage_does(cur, path):
    _bucket(cur)
    cur.execute("SET LOCAL ROLE bloom_workflows")
    cur.execute(_STORAGE_UPSERT, (path, "scrna", "first"))
    cur.execute(_STORAGE_UPSERT, (path, "scrna", "second"))
    cur.execute("SELECT version FROM storage.objects WHERE bucket_id = 'scrna' AND name = %s",
                (path,))
    assert cur.fetchall() == [("second",)]


@pytest.mark.parametrize("bucket, path", [
    ("scrna", "other/x.json"), ("scrna", "h5ad"), ("scrna", "Counts/x.json"),
    ("scrna", "h5adx/y"), ("scrna-elsewhere", "h5ad/x"),
])
def test_the_pipeline_cant_write_elsewhere(cur, bucket, path):
    _bucket(cur, bucket)
    cur.execute("SET LOCAL ROLE bloom_workflows")
    load.refused(cur, _STORAGE_UPSERT, (path, bucket, "x"))


def test_a_run_can_report_the_load_step(cur, scientist, species):
    run_id = load.run(cur, scientist, species)
    cur.execute("UPDATE public.rnaseq_runs SET current_step = 'load-dataset' WHERE id = %s",
                (run_id,))
    assert cur.rowcount == 1
