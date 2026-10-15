"""Shared helpers for the genome annotation and single-cell enrichment tests.

Each test applies the genome migrations and the four annotation and enrichment migrations
inside its own transaction and rolls it back, so the database is left unchanged whatever it
already holds.
"""

import json
import uuid

import pytest

from tests.integration.genome_reference_helpers import (
    WRITER,
    as_admin,
    as_role,
)
from tests.integration.genome_reference_helpers import (
    apply_migrations as apply_genome_migrations,
)
from tests.integration.test_rnaseq_runs import _find_one, _sql_body

psycopg = pytest.importorskip("psycopg")

MIGRATIONS = tuple(
    _find_one("migrations", f"*_{name}.sql")
    for name in (
        "add_genome_annotations",
        "add_genome_annotation_uploads",
        "add_scrna_enrichment",
        "add_scrna_enrichment_uploads",
    )
)
# In the order they are run.
ROLLBACKS = tuple(
    _find_one("rollbacks", f"*_{name}_rollback.sql")
    for name in (
        "add_scrna_enrichment_uploads",
        "add_scrna_enrichment",
        "add_genome_annotation_uploads",
        "add_genome_annotations",
    )
)
TABLES = (
    "scrna_enrichment_terms", "scrna_enrichment_runs", "genome_gene_descriptions",
    "genome_gene_terms", "functional_terms", "genome_annotation_builds",
)
GO_TERMS = [
    {"kind": "go", "term_id": "GO:0015979", "name": "photosynthesis", "namespace": "BP"},
    {"kind": "go", "term_id": "GO:0008150", "name": "biological_process", "namespace": "BP"},
]
KEGG_TERMS = [{"kind": "kegg_pathway", "term_id": "map00195", "name": "Photosynthesis"}]
PARAMS = {"top_n": 300, "fdr_max": 0.05, "lfc_min": 0.25, "min_term": 5, "max_term": 500}


def apply_migrations(c) -> None:
    """The genome migrations, then the annotation and enrichment migrations."""
    for table in TABLES:
        c.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")
    c.execute("ALTER TABLE public.scrna_datasets DROP COLUMN IF EXISTS genome_version_id")
    apply_genome_migrations(c)
    for migration in MIGRATIONS:
        c.execute(_sql_body(migration))


def call(cur, sql, params=(), user=WRITER, role="bloom_writer"):
    """Run one statement as a signed-in role and return its first column, or None."""
    as_role(cur, role, user)
    cur.execute(sql, params)
    row = cur.fetchone() if cur.description else None
    as_admin(cur)
    return row[0] if row else None


def add_dataset(cur, species_id, genes=("G1", "G2", "G3", "G4")):
    """A dataset with genes: (dataset id, {gene name: scrna_genes id})."""
    cur.execute(
        "INSERT INTO public.scrna_datasets (name, species_id) VALUES (%s, %s) RETURNING id",
        (f"ds-{uuid.uuid4().hex[:8]}", species_id),
    )
    dataset_id = cur.fetchone()[0]
    ids = {}
    for number, name in enumerate(genes):
        cur.execute(
            "INSERT INTO public.scrna_genes (dataset_id, gene_number, gene_name) "
            "VALUES (%s, %s, %s) RETURNING id", (dataset_id, number, name),
        )
        ids[name] = cur.fetchone()[0]
    return dataset_id, ids


def add_de(cur, dataset_id, status="complete", clusters=("0", "1")):
    """A differential expression analysis and its one-vs-rest comparisons: (run id, [de ids])."""
    cur.execute(
        "INSERT INTO public.scrna_de_runs (dataset_id, source, method, params_hash, status, "
        "completed_at, error_message) VALUES (%s, 'batch', 'scanpy-wilcoxon-one-vs-rest', 'h', %s, "
        "CASE WHEN %s IN ('complete', 'failed') THEN now() END, "
        "CASE WHEN %s = 'failed' THEN 'test failure' END) RETURNING id",
        (dataset_id, status, status, status),
    )
    run_id = cur.fetchone()[0]
    de_ids = []
    for ordinal, cluster in enumerate(clusters):
        # A comparison names a cluster of the dataset's catalogue.
        cur.execute(
            "INSERT INTO public.scrna_clusters (dataset_id, cluster_id, ordinal) VALUES (%s, %s, %s) "
            "ON CONFLICT (dataset_id, cluster_id) DO NOTHING", (dataset_id, cluster, ordinal),
        )
        cur.execute(
            "INSERT INTO public.scrna_de (dataset_id, cluster_id, contrast, group1, group2, "
            "n_group1, n_group2, n_genes_tested, run_id, group_kind, method, params_hash, tested) "
            "VALUES (%s, %s, %s, %s, 'rest', 10, 90, 4, %s, 'cluster', "
            "'scanpy-wilcoxon-one-vs-rest', 'h', true) RETURNING id",
            (dataset_id, cluster, f"{cluster}_vs_rest", cluster, run_id),
        )
        de_ids.append(cur.fetchone()[0])
    return run_id, de_ids


def start_build(cur, version_id, source="eggnog", user=WRITER, role="bloom_writer"):
    as_role(cur, role, user)
    cur.execute(
        "SELECT * FROM public.start_genome_annotation_build(%s, %s, %s)",
        (version_id, source, json.dumps({"eggnog_db": "5.0.2"})),
    )
    row = cur.fetchone()
    as_admin(cur)
    return row


def add_terms(cur, terms, user=WRITER):
    return call(cur, "SELECT public.add_functional_terms(%s)", (json.dumps(terms),), user=user)


def gene_terms(rows, kind="go"):
    return [{"gene_id": g, "kind": kind, "term_id": t, "direct": True} for g, t in rows]


def add_gene_terms(cur, build_id, rows, user=WRITER):
    return call(cur, "SELECT public.add_genome_gene_terms(%s, %s)",
                (build_id, json.dumps(rows)), user=user)


def finish_build(cur, build_id, genes, rows, user=WRITER):
    return call(cur, "SELECT public.finish_genome_annotation_build(%s, %s, %s)",
                (build_id, genes, rows), user=user)


def ready_build(cur, version_id, source="eggnog"):
    """A ready build: G1-G3 photosynthesis, G1 also KEGG photosynthesis (eggnog only)."""
    build_id, _ = start_build(cur, version_id, source)
    add_terms(cur, GO_TERMS + KEGG_TERMS)
    rows = gene_terms([("G1", "GO:0015979"), ("G2", "GO:0015979"), ("G3", "GO:0015979")])
    if source == "eggnog":
        rows += gene_terms([("G1", "map00195")], kind="kegg_pathway")
    add_gene_terms(cur, build_id, rows)
    finish_build(cur, build_id, 3, len(rows))
    return build_id


def link(cur, dataset_id, version_id, user=WRITER, role="bloom_writer"):
    call(cur, "SELECT public.link_scrna_dataset_genome(%s, %s)", (dataset_id, version_id),
         user=user, role=role)


def term_row(de_id, gene_ids, **change):
    row = {"de_id": de_id, "direction": "up", "kind": "go", "term_id": "GO:0015979",
           "hit_count": len(gene_ids), "list_size": 3, "term_size": 3, "background_size": 4,
           "pvalue": 0.001, "fdr": 0.01, "gene_ids": gene_ids}
    return {**row, **change}


def start_run(cur, dataset_id, de_run_id, build_id, params=None, user=WRITER):
    return call(cur, "SELECT public.start_scrna_enrichment_run(%s, %s, %s, %s)",
                (dataset_id, de_run_id, build_id, json.dumps(params or PARAMS)), user=user)


def add_run_terms(cur, run_id, rows, user=WRITER):
    return call(cur, "SELECT public.add_scrna_enrichment_terms(%s, %s)",
                (run_id, json.dumps(rows)), user=user)


def finish_run(cur, run_id, count, user=WRITER):
    call(cur, "SELECT public.finish_scrna_enrichment_run(%s, %s)", (run_id, count), user=user)
