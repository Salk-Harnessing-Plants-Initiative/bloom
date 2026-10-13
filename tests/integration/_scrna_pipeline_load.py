"""Helpers shared by the pipeline dataset-load tests: accounts, a species, the load's writes as
bloomctl makes them, a Cell Ranger run, and linking the run to its dataset."""

import json
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
Jsonb = psycopg.types.json.Jsonb

LINK = "public.link_rnaseq_run_dataset(bigint, bigint)"
MAY_LOAD = "public.workflows_may_load_scrna_dataset(bigint)"
CHILD_TABLES = ["scrna_clusters", "scrna_genotypes", "scrna_cells", "scrna_genes", "scrna_counts"]
# The default differential expression and cluster statistics the load adds (20261012230000).
DE_TABLES = ["scrna_de_runs", "scrna_de", "scrna_de_genes", "scrna_cluster_stats"]
READ_ONLY_TABLES = ["species", "scrna_cluster_neighbors"]


def user(cur, workflows=False):
    """A signed-up account; a pipeline account when `workflows`."""
    uid = str(uuid.uuid4())
    meta = {"is_workflows": True} if workflows else {}
    cur.execute(
        "INSERT INTO auth.users (id, email, raw_app_meta_data) VALUES (%s, %s, %s)",
        (uid, f"load-{uid[:8]}@salk.edu", Jsonb(meta)),
    )
    return uid


def species(cur):
    tag = uuid.uuid4().hex[:10]
    cur.execute(
        "INSERT INTO public.species (common_name, genus, species) VALUES (%s, %s, %s) "
        "RETURNING id",
        (f"Load {tag}", f"Zload{tag}", "probus"),
    )
    return cur.fetchone()[0]


def sign_in(cur, uid, role="bloom_workflows"):
    cur.execute(
        "SELECT set_config('request.jwt.claims', %s, true)",
        (json.dumps({"sub": uid, "role": role}),),
    )
    cur.execute(f"SET LOCAL ROLE {role}")


def refused(cur, sql, params=(), error=psycopg.errors.InsufficientPrivilege, match=None):
    cur.execute("SAVEPOINT refused")
    try:
        with pytest.raises(error, match=match):
            cur.execute(sql, params)
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT refused")


def create(cur, species_id, name="Root atlas", run_id=None):
    """The load's first write, as the signed-in account, recording the run it loads for:
    (id, created_by)."""
    metadata = {"expected_cells": 1}
    if run_id is not None:
        metadata["rnaseq_run_id"] = run_id
    cur.execute(
        "INSERT INTO public.scrna_datasets (name, species_id, source_checksum, metadata) "
        "VALUES (%s, %s, %s, %s) RETURNING id, created_by::text",
        (name, species_id, "a" * 64, Jsonb(metadata)),
    )
    return cur.fetchone()


def fill(cur, dataset_id):
    """The rest of the load, as the signed-in account."""
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
    # Any path: the row only records where the gene's counts file is.
    cur.execute(
        "INSERT INTO public.scrna_counts (dataset_id, gene_id, counts_object_path) "
        "VALUES (%s, %s, 'counts/any_1_/AT1G01010.json')",
        (dataset_id, gene),
    )


def add_de(cur, dataset_id):
    """The load's DE writes, as the signed-in account: one run, cluster 0 against the rest, one
    gene, and cluster 0's statistics. Needs fill()'s cluster and gene."""
    cur.execute(
        "INSERT INTO public.scrna_de_runs (dataset_id, source, status, method, params_hash, "
        "completed_at) VALUES (%s, 'batch', 'complete', 'scanpy-wilcoxon-one-vs-rest', %s, now()) "
        "RETURNING id",
        (dataset_id, "b" * 64),
    )
    run_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO public.scrna_de (dataset_id, run_id, cluster_id, contrast, group1, group2, "
        "group_kind, method, params_hash, tested, n_genes_tested) "
        "VALUES (%s, %s, '0', '0_vs_rest', '0', 'rest', 'cluster', "
        "'scanpy-wilcoxon-one-vs-rest', %s, true, 1) RETURNING id",
        (dataset_id, run_id, "b" * 64),
    )
    de_id = cur.fetchone()[0]
    cur.execute("SELECT id FROM public.scrna_genes WHERE dataset_id = %s", (dataset_id,))
    gene = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO public.scrna_de_genes (de_id, dataset_id, gene_id, log2fc, pvalue, fdr, "
        "pct_1, pct_2) VALUES (%s, %s, %s, 2.5, 0.001, 0.01, 0.9, 0.1)",
        (de_id, dataset_id, gene),
    )
    cur.execute(
        "INSERT INTO public.scrna_cluster_stats (dataset_id, cluster_id, cell_count, pct, markers) "
        "VALUES (%s, '0', 1, 100, %s)",
        (dataset_id, Jsonb({"top": [{"gene": "AT1G01010", "log2fc": 2.5, "q": 0.01}],
                           "n_significant": 1})),
    )


def finish(cur, dataset_id):
    """The load's last write; the number of rows it changed."""
    cur.execute(
        "UPDATE public.scrna_datasets SET n_cells = 1, n_genes = 1, ingested_at = now() "
        "WHERE id = %s",
        (dataset_id,),
    )
    return cur.rowcount


def loaded(cur, pipeline, species_id, name="Root atlas", run_id=None):
    """A dataset the pipeline loaded and finished, for `run_id`; back as supabase_admin."""
    sign_in(cur, pipeline)
    dataset_id, _ = create(cur, species_id, name, run_id)
    fill(cur, dataset_id)
    assert finish(cur, dataset_id) == 1
    cur.execute("RESET ROLE")
    return dataset_id


def run(cur, scientist, species_id, name="Root atlas", status="running", sample="root1",
        **details):
    """A Cell Ranger run with the form's details in its metadata."""
    metadata = {"species_id": species_id, "dataset_name": name, "origin": "public", **details}
    cur.execute(
        "INSERT INTO public.rnaseq_runs (workflow_type, params, run_key, requested_by, status, "
        "metadata) VALUES ('scrna-cellranger', %s, %s, %s, %s, %s) RETURNING id",
        (Jsonb({"sample": sample, "reference": "tair10"}), f"{sample}__tair10__{scientist}",
         scientist, status, Jsonb(metadata)),
    )
    return cur.fetchone()[0]


def link(cur, account, run_id, dataset_id):
    """link_rnaseq_run_dataset as a pipeline account; back as supabase_admin."""
    sign_in(cur, account)
    cur.execute("SELECT public.link_rnaseq_run_dataset(%s, %s)", (run_id, dataset_id))
    value = cur.fetchone()[0]
    cur.execute("RESET ROLE")
    return value
