"""
Integration tests for bloom_uploader, the role a scientist flagged `is_uploader` signs in as:
- sign-in gives it to an account with only that flag; a higher flag keeps its role;
- it uploads its own genome versions (start, store both files, finish) and cannot finish,
  abandon or add files to anyone else's;
- it loads its own single-cell datasets as `bloomctl scrna hdf5 upload` does (create, add
  clusters, genotypes, cells, genes, counts and default DE, finish; resume; add counts to its
  own finished dataset that has none), storing h5ad/ and counts/ objects, and writes no one
  else's dataset or objects;
- it writes no other table or bucket, deletes nothing, and is not a member of authenticated;
- it reads what bloom_user reads.

LOCAL ONLY: `pg_conn` connects as `supabase_admin` (BYPASSRLS) and every test rolls back.
Privileges a role is denied are checked with has_*_privilege, not by calling as that role.
"""

import json
import uuid

import pytest

from tests.integration import _scrna_pipeline_load as load
from tests.integration.test_rnaseq_runs import _find_one, _sql_body

psycopg = pytest.importorskip("psycopg")
Jsonb = psycopg.types.json.Jsonb

ROLE = "bloom_uploader"
GENOME_BUCKET = "genome-references"
SHA_A, SHA_B = "a" * 64, "b" * 64

# Every public table the uploader may write, and how: (INSERT, UPDATE).
WRITES = {
    "scrna_datasets": (True, True),
    **{t: (True, False) for t in load.CHILD_TABLES},
    **{t: (True, False) for t in load.DE_TABLES},
}

# SECURITY DEFINER functions the uploader may call beyond what bloom_user may.
UPLOADER_FUNCTIONS = {
    "abandon_genome_version",
    "finish_genome_version",
    "start_genome_version",
    "uploader_may_load_scrna_dataset",
    "uploader_may_reopen_scrna_dataset",
    "uploader_may_upload_genome_file",
    "uploader_owns_object",
}

# bloom_user functions that write, which the uploader does not get.
USER_WRITE_FUNCTIONS = [
    "add_species", "insert_gravi_image", "insert_gravi_scan_session", "insert_gravi_scan_metadata",
]

# storage-api's upsert, run as the caller's role, owned by the signed-in account.
STORAGE_UPSERT = """
    INSERT INTO storage.objects
      (name, owner, owner_id, bucket_id, metadata, user_metadata, version)
    VALUES (%(name)s, %(owner)s::uuid, %(owner)s::text, %(bucket)s, '{}', '{}', %(version)s)
    ON CONFLICT (name, bucket_id) DO UPDATE
      SET metadata = EXCLUDED.metadata,
          user_metadata = EXCLUDED.user_metadata,
          version = EXCLUDED.version,
          owner = EXCLUDED.owner,
          owner_id = EXCLUDED.owner_id
"""

STORAGE_INSERT = """
    INSERT INTO storage.objects (bucket_id, name, owner, owner_id, metadata)
    VALUES (%(bucket)s, %(name)s, %(owner)s::uuid, %(owner)s::text, %(metadata)s)
"""


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        yield c
    pg_conn.rollback()


def _account(cur, **flags):
    uid = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO auth.users (id, email, raw_app_meta_data) VALUES (%s, %s, %s)",
        (uid, f"uploader-{uid[:8]}@salk.edu", Jsonb(flags)),
    )
    return uid


@pytest.fixture
def uploader(cur):
    return _account(cur, is_uploader=True)


@pytest.fixture
def writer(cur):
    return _account(cur, is_writer=True)


@pytest.fixture
def species(cur):
    return load.species(cur)


def _as(cur, uid, role=ROLE):
    load.sign_in(cur, uid, role)
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (uid,))


def _back(cur):
    cur.execute("RESET ROLE")
    cur.execute("SELECT set_config('request.jwt.claims', '', true)")
    cur.execute("SELECT set_config('request.jwt.claim.sub', '', true)")


def _bucket(cur, bucket):
    cur.execute("INSERT INTO storage.buckets (id, name) VALUES (%s, %s) "
                "ON CONFLICT (id) DO NOTHING", (bucket, bucket))


# --------------------------------------------------------------------------- #
# Sign-in
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("flags, expected", [
    ({"is_uploader": True}, "bloom_uploader"),
    ({"is_uploader": True, "is_writer": True}, "bloom_writer"),
    ({"is_uploader": True, "is_admin": True}, "bloom_admin"),
    ({"is_uploader": True, "is_workflows": True}, "bloom_workflows"),
    ({"is_uploader": False}, "bloom_user"),
    ({}, "bloom_user"),
])
def test_sign_in_gives_the_uploader_role_below_writer(cur, flags, expected):
    uid = _account(cur, **flags)
    event = {"user_id": uid, "claims": {"sub": uid, "role": "authenticated"}}
    cur.execute("SELECT public.custom_access_token_hook(%s::jsonb)", (json.dumps(event),))
    assert cur.fetchone()[0]["claims"]["role"] == expected


def test_only_sign_in_may_call_the_hook(cur):
    cur.execute("SELECT has_function_privilege(r, 'public.custom_access_token_hook(jsonb)', "
                "'EXECUTE') FROM unnest(ARRAY['supabase_auth_admin', 'bloom_uploader', "
                "'authenticated', 'anon']) r")
    assert [r[0] for r in cur.fetchall()] == [True, False, False, False]


# --------------------------------------------------------------------------- #
# The role
# --------------------------------------------------------------------------- #


def test_the_role_is_a_nologin_role_postgrest_can_switch_to(cur):
    cur.execute("SELECT rolcanlogin, rolbypassrls, rolsuper, rolinherit FROM pg_roles "
                "WHERE rolname = %s", (ROLE,))
    assert cur.fetchone() == (False, False, False, True)
    cur.execute("SELECT pg_has_role('authenticator', %s, 'MEMBER')", (ROLE,))
    assert cur.fetchone()[0] is True


@pytest.mark.parametrize("other", [
    "authenticated", "anon", "service_role", "bloom_user", "bloom_writer", "bloom_admin",
    "bloom_workflows", "bloom_agent",
])
def test_the_uploader_is_a_member_of_no_other_role(cur, other):
    cur.execute("SELECT pg_has_role(%s, %s, 'MEMBER')", (ROLE, other))
    assert cur.fetchone()[0] is False


def test_the_uploader_has_storage_but_not_auth_schema_usage(cur):
    cur.execute("SELECT has_schema_privilege(%s, 'public', 'USAGE'), "
                "has_schema_privilege(%s, 'storage', 'USAGE'), "
                "has_schema_privilege(%s, 'auth', 'USAGE')", (ROLE, ROLE, ROLE))
    assert cur.fetchone() == (True, True, False)


# --------------------------------------------------------------------------- #
# What it may write
# --------------------------------------------------------------------------- #


def test_the_uploader_writes_only_the_load_tables(cur):
    cur.execute(
        "SELECT c.relname, has_any_column_privilege(%(r)s, c.oid, 'INSERT'), "
        "has_any_column_privilege(%(r)s, c.oid, 'UPDATE') "
        "FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace "
        "AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
        "AND (has_any_column_privilege(%(r)s, c.oid, 'INSERT') "
        "     OR has_any_column_privilege(%(r)s, c.oid, 'UPDATE'))",
        {"r": ROLE},
    )
    assert {name: (ins, upd) for name, ins, upd in cur.fetchall()} == WRITES


@pytest.mark.parametrize("table", [
    "cyl_scans", "cyl_plants", "cyl_experiments", "gravi_experiments", "gene_candidates",
    "species", "genome_references", "genome_reference_versions", "rnaseq_runs",
    "scrna_cluster_neighbors", "scrna_embeddings", "accessions", "experiment_progress_logs",
])
def test_the_uploader_cannot_write_outside_the_load(cur, table):
    t = f"public.{table}"
    cur.execute("SELECT has_any_column_privilege(%s, %s, 'INSERT'), "
                "has_any_column_privilege(%s, %s, 'UPDATE')", (ROLE, t, ROLE, t))
    assert cur.fetchone() == (False, False)


def test_the_uploader_deletes_and_truncates_nothing(cur):
    cur.execute(
        "SELECT n.nspname || '.' || c.relname FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname IN ('public', 'storage') AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
        "AND (has_table_privilege(%(r)s, c.oid, 'DELETE') "
        "     OR has_table_privilege(%(r)s, c.oid, 'TRUNCATE'))",
        {"r": ROLE},
    )
    assert cur.fetchall() == []


def test_the_uploader_sets_only_the_dataset_columns_the_load_sets(cur):
    cur.execute("SELECT privilege_type, array_agg(column_name::text ORDER BY column_name) "
                "FROM information_schema.column_privileges WHERE grantee = %s "
                "AND table_schema = 'public' AND table_name = 'scrna_datasets' "
                "AND privilege_type IN ('INSERT', 'UPDATE') GROUP BY privilege_type", (ROLE,))
    assert dict(cur.fetchall()) == {
        "INSERT": ["metadata", "name", "source_checksum", "species_id"],
        "UPDATE": ["expression_units", "ingested_at", "metadata", "n_cells", "n_genes"],
    }


def test_the_uploader_calls_only_the_upload_functions_beyond_bloom_users(cur):
    cur.execute(
        "SELECT p.proname FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace "
        "AND p.prosecdef AND has_function_privilege(%s, p.oid, 'EXECUTE') "
        "AND NOT has_function_privilege('bloom_user', p.oid, 'EXECUTE')", (ROLE,))
    assert {r[0] for r in cur.fetchall()} == UPLOADER_FUNCTIONS


@pytest.mark.parametrize("name", USER_WRITE_FUNCTIONS)
def test_the_uploader_cannot_call_bloom_users_write_functions(cur, name):
    cur.execute("SELECT bool_or(has_function_privilege(%s, p.oid, 'EXECUTE')) FROM pg_proc p "
                "WHERE p.pronamespace = 'public'::regnamespace AND p.proname = %s", (ROLE, name))
    assert cur.fetchone()[0] is False


def test_only_the_uploader_may_call_its_checks(cur):
    for fn in ("uploader_may_load_scrna_dataset(bigint)",
               "uploader_may_reopen_scrna_dataset(bigint)",
               "uploader_may_upload_genome_file(text)", "uploader_owns_object(text)"):
        for role, allowed in ((ROLE, True), ("bloom_user", False), ("authenticated", False),
                              ("anon", False), ("bloom_workflows", False)):
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                        (role, f"public.{fn}"))
            assert cur.fetchone()[0] is allowed, f"{role} {fn}"


def test_the_checks_run_as_their_owner_with_a_pinned_search_path(cur):
    cur.execute("SELECT proname, prosecdef, proconfig FROM pg_proc WHERE pronamespace = "
                "'public'::regnamespace AND proname LIKE 'uploader\\_%%'")
    rows = cur.fetchall()
    assert {r[0] for r in rows} == {f for f in UPLOADER_FUNCTIONS if f.startswith("uploader_")}
    for name, definer, config in rows:
        assert definer and any(c.startswith("search_path=") for c in config or []), name


# --------------------------------------------------------------------------- #
# What it may read
# --------------------------------------------------------------------------- #


def test_the_uploader_reads_every_table_bloom_user_reads(cur):
    cur.execute(
        "SELECT n.nspname || '.' || c.relname FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname IN ('public', 'storage') AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
        "AND has_table_privilege('bloom_user', c.oid, 'SELECT') "
        "AND NOT has_table_privilege(%s, c.oid, 'SELECT')", (ROLE,))
    assert cur.fetchall() == []


def test_the_uploader_has_a_read_rule_for_each_of_bloom_users(cur):
    cur.execute(
        "SELECT u.schemaname, u.tablename, u.qual FROM pg_policies u "
        "WHERE 'bloom_user' = ANY(u.roles) AND u.cmd = 'SELECT' "
        "AND u.schemaname IN ('public', 'storage') "
        "EXCEPT SELECT p.schemaname, p.tablename, p.qual FROM pg_policies p "
        "WHERE %s = ANY(p.roles) AND p.cmd = 'SELECT'", (ROLE,))
    assert cur.fetchall() == []


def test_the_uploader_calls_bloom_users_read_functions(cur):
    cur.execute(
        "SELECT p.proname FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace "
        "AND has_function_privilege('bloom_user', p.oid, 'EXECUTE') "
        "AND NOT has_function_privilege(%s, p.oid, 'EXECUTE') ORDER BY 1", (ROLE,))
    assert sorted(r[0] for r in cur.fetchall()) == sorted(USER_WRITE_FUNCTIONS)


def test_the_uploader_reads_the_load_tables_and_its_own_unfinished_dataset(
        cur, uploader, species):
    _as(cur, uploader)
    dataset_id, _ = load.create(cur, species)
    cur.execute("SELECT id FROM public.scrna_datasets WHERE id = %s", (dataset_id,))
    assert cur.fetchall() == [(dataset_id,)]
    cur.execute("SELECT count(*) FROM public.species WHERE id = %s", (species,))
    assert cur.fetchone()[0] == 1


# --------------------------------------------------------------------------- #
# Genome versions
# --------------------------------------------------------------------------- #


def _start(cur, uid, role, genome, species_id):
    _as(cur, uid, role)
    cur.execute("SELECT * FROM public.start_genome_version(p_genome => %s, p_species_id => %s)",
                (genome, species_id))
    row = cur.fetchone()
    _back(cur)
    return row


def _put(cur, uid, role, name, size, bucket=GENOME_BUCKET):
    _as(cur, uid, role)
    cur.execute(STORAGE_INSERT, {"bucket": bucket, "name": name, "owner": uid,
                                 "metadata": json.dumps({"size": size})})
    _back(cur)


def _status(cur, version_id):
    cur.execute("SELECT status FROM public.genome_reference_versions WHERE id = %s",
                (version_id,))
    return cur.fetchone()[0]


def _genome():
    return f"g{uuid.uuid4().hex[:12]}"


def test_the_uploader_uploads_its_own_genome_version(cur, uploader, species):
    version_id, version, fasta, gtf = _start(cur, uploader, ROLE, _genome(), species)
    _put(cur, uploader, ROLE, fasta, 100)
    _put(cur, uploader, ROLE, gtf, 50)
    _as(cur, uploader)
    cur.execute("SELECT public.finish_genome_version(%s, %s, 100, %s, 50)",
                (version_id, SHA_A, SHA_B))
    assert cur.fetchone()[0] == version
    _back(cur)
    cur.execute("SELECT status, created_by::text FROM public.genome_reference_versions "
                "WHERE id = %s", (version_id,))
    assert cur.fetchone() == ("ready", uploader)


def test_the_uploader_abandons_its_own_unfinished_version(cur, uploader, species):
    version_id, *_ = _start(cur, uploader, ROLE, _genome(), species)
    _as(cur, uploader)
    cur.execute("SELECT public.abandon_genome_version(%s)", (version_id,))
    assert cur.fetchone()[0] is True
    _back(cur)
    assert _status(cur, version_id) == "abandoned"


@pytest.mark.parametrize("call", [
    "SELECT public.finish_genome_version(%s, '" + SHA_A + "', 100, '" + SHA_B + "', 50)",
    "SELECT public.abandon_genome_version(%s)",
])
def test_the_uploader_cannot_finish_or_abandon_another_accounts_version(
        cur, uploader, writer, species, call):
    version_id, _, fasta, gtf = _start(cur, writer, "bloom_writer", _genome(), species)
    _put(cur, writer, "bloom_writer", fasta, 100)
    _put(cur, writer, "bloom_writer", gtf, 50)
    _as(cur, uploader)
    load.refused(cur, call, (version_id,), match="started by someone else")
    _back(cur)
    assert _status(cur, version_id) == "uploading"


def test_the_uploader_cannot_add_files_to_another_accounts_version(
        cur, uploader, writer, species):
    _, _, fasta, _ = _start(cur, writer, "bloom_writer", _genome(), species)
    _as(cur, uploader)
    load.refused(cur, STORAGE_INSERT, {"bucket": GENOME_BUCKET, "name": fasta,
                                       "owner": uploader, "metadata": "{}"})


@pytest.mark.parametrize("path", ["{genome}/v1/extra.gz", "other.gz"])
def test_the_uploader_cannot_add_other_paths_to_the_genome_bucket(
        cur, uploader, species, path):
    genome = _genome()
    _start(cur, uploader, ROLE, genome, species)
    _as(cur, uploader)
    load.refused(cur, STORAGE_INSERT, {"bucket": GENOME_BUCKET, "name": path.format(genome=genome),
                                       "owner": uploader, "metadata": "{}"})


def test_the_uploader_cannot_replace_its_finished_versions_files(cur, uploader, species):
    version_id, _, fasta, gtf = _start(cur, uploader, ROLE, _genome(), species)
    _put(cur, uploader, ROLE, fasta, 100)
    _put(cur, uploader, ROLE, gtf, 50)
    _as(cur, uploader)
    cur.execute("SELECT public.finish_genome_version(%s, %s, 100, %s, 50)",
                (version_id, SHA_A, SHA_B))
    cur.execute("UPDATE storage.objects SET metadata = '{\"size\": 1}' "
                "WHERE bucket_id = %s AND name = %s", (GENOME_BUCKET, fasta))
    assert cur.rowcount == 0
    load.refused(cur, STORAGE_INSERT, {"bucket": GENOME_BUCKET, "name": fasta,
                                       "owner": uploader, "metadata": "{}"},
                 error=(psycopg.errors.InsufficientPrivilege, psycopg.errors.UniqueViolation))


# --------------------------------------------------------------------------- #
# Single-cell datasets
# --------------------------------------------------------------------------- #


def test_the_uploader_loads_its_own_dataset_as_bloomctl_does(cur, uploader, species):
    _as(cur, uploader)
    dataset_id, created_by = load.create(cur, species)
    assert created_by == uploader
    load.fill(cur, dataset_id)
    load.add_de(cur, dataset_id)
    assert load.finish(cur, dataset_id) == 1
    _back(cur)
    for table in ["scrna_datasets", *load.CHILD_TABLES, *load.DE_TABLES]:
        column = "id" if table == "scrna_datasets" else "dataset_id"
        cur.execute(f"SELECT count(*) FROM public.{table} WHERE {column} = %s", (dataset_id,))
        assert cur.fetchone()[0] >= 1, table
    cur.execute("SELECT ingested_at IS NOT NULL FROM public.scrna_datasets WHERE id = %s",
                (dataset_id,))
    assert cur.fetchone()[0] is True


def test_the_uploader_resumes_its_own_stopped_load(cur, uploader, species):
    _as(cur, uploader)
    dataset_id, _ = load.create(cur, species)
    cur.execute("INSERT INTO public.scrna_clusters (dataset_id, cluster_id, ordinal) "
                "VALUES (%s, '0', 0)", (dataset_id,))
    _back(cur)
    # A later run signs in again and carries on from what is stored.
    _as(cur, uploader)
    cur.execute("SELECT count(*) FROM public.scrna_clusters WHERE dataset_id = %s", (dataset_id,))
    assert cur.fetchone()[0] == 1
    cur.execute("INSERT INTO public.scrna_genes (dataset_id, gene_number, gene_name) "
                "VALUES (%s, 0, 'AT1G01010')", (dataset_id,))
    assert load.finish(cur, dataset_id) == 1


def _finished_without_counts(cur, uploader, species):
    """The uploader's dataset, finished before counts were part of the load."""
    _as(cur, uploader)
    dataset_id, _ = load.create(cur, species)
    cur.execute("INSERT INTO public.scrna_clusters (dataset_id, cluster_id, ordinal) "
                "VALUES (%s, '0', 0)", (dataset_id,))
    assert load.finish(cur, dataset_id) == 1
    return dataset_id


def test_the_uploader_adds_counts_to_its_own_finished_dataset_that_has_none(
        cur, uploader, species):
    dataset_id = _finished_without_counts(cur, uploader, species)
    cur.execute("UPDATE public.scrna_datasets SET ingested_at = NULL, "
                "metadata = metadata || '{\"counts_pending\": true}' WHERE id = %s",
                (dataset_id,))
    assert cur.rowcount == 1
    cur.execute("INSERT INTO public.scrna_genes (dataset_id, gene_number, gene_name) "
                "VALUES (%s, 0, 'AT1G01010') RETURNING id", (dataset_id,))
    gene = cur.fetchone()[0]
    cur.execute("INSERT INTO public.scrna_counts (dataset_id, gene_id, counts_object_path) "
                "VALUES (%s, %s, 'counts/x_1_/AT1G01010.json')", (dataset_id, gene))
    cur.execute("UPDATE public.scrna_datasets SET ingested_at = now(), "
                "metadata = metadata - 'counts_pending' WHERE id = %s", (dataset_id,))
    assert cur.rowcount == 1


def test_a_finished_dataset_with_counts_cannot_be_reopened_or_added_to(cur, uploader, species):
    _as(cur, uploader)
    dataset_id, _ = load.create(cur, species)
    load.fill(cur, dataset_id)
    assert load.finish(cur, dataset_id) == 1
    cur.execute("UPDATE public.scrna_datasets SET ingested_at = NULL WHERE id = %s",
                (dataset_id,))
    assert cur.rowcount == 0, "the uploader reopened a finished dataset with counts"
    load.refused(cur, "INSERT INTO public.scrna_genes (dataset_id, gene_number, gene_name) "
                 "VALUES (%s, 1, 'AT1G01020')", (dataset_id,))


def test_a_finished_dataset_without_counts_is_not_added_to_until_reopened(
        cur, uploader, species):
    dataset_id = _finished_without_counts(cur, uploader, species)
    load.refused(cur, "INSERT INTO public.scrna_genes (dataset_id, gene_number, gene_name) "
                 "VALUES (%s, 1, 'AT1G01020')", (dataset_id,))


@pytest.mark.parametrize("owner_role", ["bloom_writer", "bloom_uploader", "bloom_workflows"])
def test_the_uploader_cannot_change_or_add_to_another_accounts_dataset(
        cur, uploader, species, owner_role):
    flag = {"bloom_writer": "is_writer", "bloom_uploader": "is_uploader",
            "bloom_workflows": "is_workflows"}[owner_role]
    other = _account(cur, **{flag: True})
    _as(cur, other, owner_role)
    dataset_id, _ = load.create(cur, species)
    cur.execute("INSERT INTO public.scrna_clusters (dataset_id, cluster_id, ordinal) "
                "VALUES (%s, '0', 0)", (dataset_id,))
    _back(cur)
    _as(cur, uploader)
    assert load.finish(cur, dataset_id) == 0, "the uploader finished someone else's dataset"
    for sql in (
        "INSERT INTO public.scrna_clusters (dataset_id, cluster_id, ordinal) VALUES (%s, '9', 9)",
        "INSERT INTO public.scrna_genotypes (dataset_id, name) VALUES (%s, 'Col-0')",
        "INSERT INTO public.scrna_cells (dataset_id, cell_number, cluster_id) VALUES (%s, 9, '0')",
        ("INSERT INTO public.scrna_genes (dataset_id, gene_number, gene_name) "
         "VALUES (%s, 9, 'AT1G01020')"),
        ("INSERT INTO public.scrna_de_runs (dataset_id, source, status, method, params_hash) "
         "VALUES (%s, 'batch', 'complete', 'scanpy-wilcoxon-one-vs-rest', '" + SHA_B + "')"),
        ("INSERT INTO public.scrna_cluster_stats (dataset_id, cluster_id, cell_count, pct) "
         "VALUES (%s, '0', 1, 100)"),
    ):
        load.refused(cur, sql, (dataset_id,))
    _back(cur)
    cur.execute("SELECT ingested_at, created_by::text FROM public.scrna_datasets WHERE id = %s",
                (dataset_id,))
    assert cur.fetchone() == (None, other)


def test_the_uploader_cannot_reopen_another_accounts_dataset_without_counts(
        cur, uploader, species):
    other = _account(cur, is_uploader=True)
    dataset_id = _finished_without_counts(cur, other, species)
    _back(cur)
    _as(cur, uploader)
    cur.execute("UPDATE public.scrna_datasets SET ingested_at = NULL WHERE id = %s",
                (dataset_id,))
    assert cur.rowcount == 0


def test_a_removed_dataset_cannot_be_added_to(cur, uploader, species):
    _as(cur, uploader)
    dataset_id, _ = load.create(cur, species)
    _back(cur)
    cur.execute("UPDATE public.scrna_datasets SET deleted_at = now() WHERE id = %s", (dataset_id,))
    _as(cur, uploader)
    load.refused(cur, "INSERT INTO public.scrna_genes (dataset_id, gene_number, gene_name) "
                 "VALUES (%s, 1, 'AT1G01020')", (dataset_id,))


def test_a_dataset_cannot_be_created_finished_or_owned_by_another(cur, uploader, writer, species):
    _as(cur, uploader)
    load.refused(cur, "INSERT INTO public.scrna_datasets (name, species_id, ingested_at) "
                 "VALUES ('Sneaky', %s, now())", (species,))
    load.refused(cur, "INSERT INTO public.scrna_datasets (name, species_id, created_by) "
                 "VALUES ('Sneaky', %s, %s)", (species, writer))


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", ["h5ad/" + SHA_B + ".h5ad.gz",
                                  "counts/Root atlas_1_/AT1G01010.json"])
def test_the_uploader_stores_the_file_and_counts_as_storage_does(cur, uploader, path):
    _bucket(cur, "scrna")
    _as(cur, uploader)
    for version in ("first", "second"):
        cur.execute(STORAGE_UPSERT, {"name": path, "owner": uploader, "bucket": "scrna",
                                     "version": version})
    cur.execute("SELECT version FROM storage.objects WHERE bucket_id = 'scrna' AND name = %s",
                (path,))
    assert cur.fetchall() == [("second",)]


def test_the_uploader_cannot_replace_another_accounts_counts(cur, uploader, writer):
    _bucket(cur, "scrna")
    path = "counts/Theirs_1_/AT1G01010.json"
    _as(cur, writer, "bloom_writer")
    cur.execute(STORAGE_UPSERT, {"name": path, "owner": writer, "bucket": "scrna",
                                 "version": "theirs"})
    _back(cur)
    _as(cur, uploader)
    load.refused(cur, STORAGE_UPSERT, {"name": path, "owner": uploader, "bucket": "scrna",
                                       "version": "mine"})
    cur.execute("UPDATE storage.objects SET version = 'mine' "
                "WHERE bucket_id = 'scrna' AND name = %s", (path,))
    assert cur.rowcount == 0


@pytest.mark.parametrize("bucket, path", [
    ("scrna", "other/x.json"), ("scrna", "reports/1/web_summary.html"), ("scrna", "h5ad"),
    ("scrna", "Counts/x.json"), ("images", "h5ad/x"), ("videos", "x.mp4"),
    ("tus-files", "x"), ("cyl-images", "x.png"), ("species-illustrations", "x.png"),
])
def test_the_uploader_cannot_write_other_buckets_or_paths(cur, uploader, bucket, path):
    _bucket(cur, bucket)
    _as(cur, uploader)
    load.refused(cur, STORAGE_UPSERT, {"name": path, "owner": uploader, "bucket": bucket,
                                       "version": "x"})


def test_the_uploader_reads_the_scrna_bucket(cur, uploader, writer):
    _bucket(cur, "scrna")
    path = "h5ad/" + SHA_A + ".h5ad.gz"
    _as(cur, writer, "bloom_writer")
    cur.execute(STORAGE_UPSERT, {"name": path, "owner": writer, "bucket": "scrna",
                                 "version": "v"})
    _back(cur)
    _as(cur, uploader)
    cur.execute("SELECT count(*) FROM storage.objects WHERE bucket_id = 'scrna' AND name = %s",
                (path,))
    assert cur.fetchone()[0] == 1


# --------------------------------------------------------------------------- #
# The migration and its rollback
# --------------------------------------------------------------------------- #


def test_the_migration_applies_again_and_the_rollback_removes_the_role(cur):
    migration = _sql_body(_find_one("migrations", "*_add_bloom_uploader_role.sql"))
    rollback = _sql_body(_find_one("rollbacks", "*_add_bloom_uploader_role_rollback.sql"))
    cur.execute(migration)
    cur.execute(rollback)
    cur.execute("SELECT count(*) FROM pg_roles WHERE rolname = %s", (ROLE,))
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'public'::regnamespace "
                "AND proname LIKE 'uploader\\_%%'")
    assert cur.fetchone()[0] == 0
    uid = _account(cur, is_uploader=True)
    event = {"user_id": uid, "claims": {"sub": uid, "role": "authenticated"}}
    cur.execute("SELECT public.custom_access_token_hook(%s::jsonb)", (json.dumps(event),))
    assert cur.fetchone()[0]["claims"]["role"] == "bloom_user"
    cur.execute(migration)
    cur.execute("SELECT count(*) FROM pg_roles WHERE rolname = %s", (ROLE,))
    assert cur.fetchone()[0] == 1
