"""
Integration tests for the pipeline loading a Cell Ranger run's dataset into Bloom:
- bloom_workflows can create a dataset and add its clusters, genotypes, cells, genes and
  counts, and finish it, as `bloomctl scrna hdf5 upload` does;
- it writes only datasets it created and hasn't finished, only the columns the load sets, and
  deletes nothing;
- in the `scrna` bucket it writes only under h5ad/ and counts/;
- only bloom_workflows may call link_rnaseq_run_dataset (its behaviour is in
  test_rnaseq_run_dataset_link.py);
- a run can report the load-dataset step.

LOCAL ONLY: `pg_conn` connects as `supabase_admin` (BYPASSRLS) and every test rolls back.
Privileges a role is denied are checked with has_*_privilege, not by calling as that role.
"""

import json
import uuid

import pytest

from tests.integration.test_rnaseq_runs import _find_one

psycopg = pytest.importorskip("psycopg")
Jsonb = psycopg.types.json.Jsonb

MIGRATION = _find_one("migrations", "*_let_pipeline_load_scrna_datasets.sql")
ROLLBACK = _find_one("rollbacks", "*_let_pipeline_load_scrna_datasets_rollback.sql")
LINK = "public.link_rnaseq_run_dataset(bigint)"
MAY_LOAD = "public.workflows_may_load_scrna_dataset(bigint)"
CHILD_TABLES = ["scrna_clusters", "scrna_genotypes", "scrna_cells", "scrna_genes", "scrna_counts"]
READ_ONLY_TABLES = ["species", "scrna_cluster_stats", "scrna_cluster_neighbors", "scrna_de"]

# The statement storage-api v1.48.14 runs as the caller's role for an upload with upsert.
_STORAGE_UPSERT = """
    INSERT INTO storage.objects
      (name, owner, owner_id, bucket_id, metadata, user_metadata, version)
    VALUES (%s, NULL, NULL, 'scrna', '{}', '{}', %s)
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


def _user(cur, workflows=False):
    uid = str(uuid.uuid4())
    meta = {"is_workflows": True} if workflows else {}
    cur.execute(
        "INSERT INTO auth.users (id, email, raw_app_meta_data) VALUES (%s, %s, %s)",
        (uid, f"load-{uid[:8]}@salk.edu", Jsonb(meta)),
    )
    return uid


@pytest.fixture
def pipeline(cur):
    return _user(cur, workflows=True)


@pytest.fixture
def scientist(cur):
    return _user(cur)


def _species(cur):
    tag = uuid.uuid4().hex[:10]
    cur.execute(
        "INSERT INTO public.species (common_name, genus, species) VALUES (%s, %s, %s) "
        "RETURNING id",
        (f"Load {tag}", f"Zload{tag}", "probus"),
    )
    return cur.fetchone()[0]


@pytest.fixture
def species(cur):
    return _species(cur)


def _as(cur, uid, role="bloom_workflows"):
    cur.execute(
        "SELECT set_config('request.jwt.claims', %s, true)",
        (json.dumps({"sub": uid, "role": role}),),
    )
    cur.execute(f"SET LOCAL ROLE {role}")


def _refused(cur, sql, params=(), error=psycopg.errors.InsufficientPrivilege):
    cur.execute("SAVEPOINT refused")
    try:
        with pytest.raises(error):
            cur.execute(sql, params)
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT refused")


def _create(cur, species, name="Root atlas"):
    """The load's first write, as the pipeline."""
    cur.execute(
        "INSERT INTO public.scrna_datasets (name, species_id, source_checksum, metadata) "
        "VALUES (%s, %s, %s, %s) RETURNING id, created_by::text",
        (name, species, "a" * 64, Jsonb({"expected_cells": 1})),
    )
    return cur.fetchone()


def _fill(cur, dataset_id):
    """The rest of the load, as the pipeline."""
    cur.execute(
        "INSERT INTO public.scrna_clusters (dataset_id, cluster_id, ordinal) VALUES (%s, '0', 0)",
        (dataset_id,),
    )
    cur.execute(
        "INSERT INTO public.scrna_genotypes (dataset_id, name) VALUES (%s, 'Col-0') RETURNING id",
        (dataset_id,),
    )
    genotype = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO public.scrna_cells (dataset_id, cell_number, cluster_id, genotype_id, facets) "
        "VALUES (%s, 0, '0', %s, %s)",
        (dataset_id, genotype, Jsonb({"sample": "S1"})),
    )
    cur.execute(
        "INSERT INTO public.scrna_genes (dataset_id, gene_number, gene_name) "
        "VALUES (%s, 0, 'AT1G01010') RETURNING id",
        (dataset_id,),
    )
    gene = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO public.scrna_counts (dataset_id, gene_id, counts_object_path) "
        "VALUES (%s, %s, 'counts/Root atlas_1_/AT1G01010.json')",
        (dataset_id, gene),
    )


def _finish(cur, dataset_id):
    cur.execute(
        "UPDATE public.scrna_datasets SET n_cells = 1, n_genes = 1, ingested_at = now() "
        "WHERE id = %s",
        (dataset_id,),
    )
    return cur.rowcount


def _loaded(cur, pipeline, species, name="Root atlas"):
    """A dataset the pipeline loaded and finished; back as supabase_admin."""
    _as(cur, pipeline)
    dataset_id, _ = _create(cur, species, name)
    _fill(cur, dataset_id)
    assert _finish(cur, dataset_id) == 1
    cur.execute("RESET ROLE")
    return dataset_id


def _run(cur, scientist, species, name="Root atlas", status="running", sample="root1", **extra):
    metadata = {"species_id": species, "dataset_name": name, "origin": "public", **extra}
    cur.execute(
        "INSERT INTO public.rnaseq_runs (workflow_type, params, run_key, requested_by, status, "
        "metadata) VALUES ('scrna-cellranger', %s, %s, %s, %s, %s) RETURNING id",
        (Jsonb({"sample": sample, "reference": "tair10"}), f"{sample}__tair10__{scientist}",
         scientist, status, Jsonb(metadata)),
    )
    return cur.fetchone()[0]


def _link(cur, pipeline, run_id):
    _as(cur, pipeline)
    cur.execute("SELECT public.link_rnaseq_run_dataset(%s)", (run_id,))
    value = cur.fetchone()[0]
    cur.execute("RESET ROLE")
    return value


# --------------------------------------------------------------------------- #
# Privileges
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("table, writes", [
    ("scrna_datasets", (True, True)),
    *((t, (True, False)) for t in CHILD_TABLES),
    *((t, (False, False)) for t in READ_ONLY_TABLES),
])
def test_the_pipeline_reads_writes_only_what_the_load_does_and_never_deletes(cur, table, writes):
    t = f"public.{table}"
    cur.execute("SELECT has_table_privilege('bloom_workflows', %s, 'SELECT'), "
                "has_any_column_privilege('bloom_workflows', %s, 'INSERT'), "
                "has_any_column_privilege('bloom_workflows', %s, 'UPDATE'), "
                "has_table_privilege('bloom_workflows', %s, 'DELETE'), "
                "has_table_privilege('bloom_workflows', %s, 'TRUNCATE')", (t, t, t, t, t))
    assert cur.fetchone() == (True, *writes, False, False)


@pytest.mark.parametrize("column, insert, update", [
    ("name", True, False), ("species_id", True, False), ("source_checksum", True, False),
    ("metadata", True, True), ("n_cells", False, True), ("n_genes", False, True),
    ("expression_units", False, True), ("ingested_at", False, True),
    ("created_by", False, False), ("deleted_at", False, False), ("url", False, False),
    ("kind", False, False),
])
def test_the_pipeline_writes_only_the_dataset_columns_the_load_sets(cur, column, insert, update):
    cur.execute("SELECT has_column_privilege('bloom_workflows', 'public.scrna_datasets', %s, "
                "'INSERT'), has_column_privilege('bloom_workflows', 'public.scrna_datasets', %s, "
                "'UPDATE')", (column, column))
    assert cur.fetchone() == (insert, update)


@pytest.mark.parametrize("role, allowed", [
    ("bloom_workflows", True), ("bloom_user", False), ("bloom_writer", False),
    ("bloom_admin", False), ("bloom_agent", False), ("authenticated", False), ("anon", False),
])
def test_only_the_pipeline_may_call_the_new_functions(cur, role, allowed):
    for fn in (LINK, MAY_LOAD):
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
    assert all(definer and config for _name, definer, config in rows)


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def test_the_pipeline_loads_a_dataset_as_bloomctl_does(cur, pipeline, species):
    _as(cur, pipeline)
    dataset_id, created_by = _create(cur, species)
    assert created_by == pipeline, "created_by isn't the pipeline account"
    _fill(cur, dataset_id)
    assert _finish(cur, dataset_id) == 1
    cur.execute("SELECT count(*) FROM public.scrna_cells WHERE dataset_id = %s", (dataset_id,))
    assert cur.fetchone()[0] == 1


def test_a_finished_dataset_cant_be_changed_or_added_to(cur, pipeline, species):
    dataset_id = _loaded(cur, pipeline, species)
    _as(cur, pipeline)
    assert _finish(cur, dataset_id) == 0, "the pipeline changed a finished dataset"
    _refused(cur, "INSERT INTO public.scrna_genes (dataset_id, gene_number, gene_name) "
             "VALUES (%s, 1, 'AT1G01020')", (dataset_id,))


def test_another_accounts_dataset_cant_be_changed_or_added_to(cur, pipeline, scientist, species):
    _as(cur, scientist, role="bloom_writer")
    dataset_id, _ = _create(cur, species)
    cur.execute("RESET ROLE")
    _as(cur, pipeline)
    assert _finish(cur, dataset_id) == 0, "the pipeline changed a scientist's dataset"
    _refused(cur, "INSERT INTO public.scrna_clusters (dataset_id, cluster_id, ordinal) "
             "VALUES (%s, '9', 9)", (dataset_id,))
    _refused(cur, "INSERT INTO public.scrna_cells (dataset_id, cell_number, cluster_id) "
             "VALUES (%s, 9, '9')", (dataset_id,))


def test_a_dataset_cant_be_created_finished_or_owned(cur, pipeline, scientist, species):
    _as(cur, pipeline)
    _refused(cur, "INSERT INTO public.scrna_datasets (name, species_id, ingested_at) "
             "VALUES ('Sneaky', %s, now())", (species,))
    _refused(cur, "INSERT INTO public.scrna_datasets (name, species_id, created_by) "
             "VALUES ('Sneaky', %s, %s)", (species, scientist))


def test_the_trigger_still_sets_created_by_for_a_writer(cur, scientist, species):
    _as(cur, scientist, role="bloom_writer")
    assert _create(cur, species)[1] == scientist


def test_a_signed_in_user_cant_create_a_dataset(cur):
    cur.execute("SELECT has_table_privilege('bloom_user', 'public.scrna_datasets', 'INSERT')")
    assert cur.fetchone()[0] is False
    cur.execute("SELECT count(*) FROM pg_policies WHERE schemaname = 'public' "
                "AND tablename = 'scrna_datasets' AND 'bloom_user' = ANY(roles) "
                "AND cmd IN ('INSERT', 'ALL')")
    assert cur.fetchone()[0] == 0


@pytest.mark.parametrize("path", ["h5ad/" + "b" * 64 + ".h5ad.gz",
                                  "counts/Root atlas_1_/AT1G01010.json"])
def test_the_pipeline_stores_the_file_and_counts_as_storage_does(cur, path):
    cur.execute("INSERT INTO storage.buckets (id, name) VALUES ('scrna', 'scrna') "
                "ON CONFLICT (id) DO NOTHING")
    cur.execute("SET LOCAL ROLE bloom_workflows")
    cur.execute(_STORAGE_UPSERT, (path, "first"))
    cur.execute(_STORAGE_UPSERT, (path, "second"))
    cur.execute("SELECT version FROM storage.objects WHERE bucket_id = 'scrna' AND name = %s",
                (path,))
    assert cur.fetchall() == [("second",)]


@pytest.mark.parametrize("path", ["other/x.json", "h5ad", "Counts/x.json", "h5adx/y"])
def test_the_pipeline_cant_write_elsewhere_in_the_bucket(cur, path):
    cur.execute("INSERT INTO storage.buckets (id, name) VALUES ('scrna', 'scrna') "
                "ON CONFLICT (id) DO NOTHING")
    cur.execute("SET LOCAL ROLE bloom_workflows")
    _refused(cur, _STORAGE_UPSERT, (path, "x"))


def test_a_run_can_report_the_load_step(cur, scientist, species):
    run_id = _run(cur, scientist, species)
    cur.execute("UPDATE public.rnaseq_runs SET current_step = 'load-dataset' WHERE id = %s",
                (run_id,))
    assert cur.rowcount == 1
