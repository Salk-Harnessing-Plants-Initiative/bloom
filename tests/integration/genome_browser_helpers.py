"""Shared helpers for the genome browser build tests.

Each test applies the genome migrations, the annotation migrations and the browser migration
inside its own transaction and rolls it back, so the database is left unchanged whatever it
already holds. Files are stood in for by storage.objects rows with a size.
"""

import json

import pytest

from tests.integration.annotation_enrichment_helpers import call, put_file
from tests.integration.genome_reference_helpers import WRITER
from tests.integration.genome_reference_helpers import (
    apply_migrations as apply_genome_migrations,
)
from tests.integration.test_rnaseq_runs import _find_one, _sql_body

psycopg = pytest.importorskip("psycopg")

# The annotation migrations run first: the browser migration re-creates the writer upload
# policy they define, and reuses their _storage_object_bytes.
MIGRATIONS = tuple(
    _find_one("migrations", f"*_{name}.sql")
    for name in ("add_genome_annotations", "add_genome_annotation_uploads",
                 "add_genome_browser_builds", "add_genome_browser_build_uploads")
)
# The browser migrations, and their rollbacks in the order they are run.
BROWSER_MIGRATIONS = MIGRATIONS[-2:]
ROLLBACKS = tuple(
    _find_one("rollbacks", f"*_{name}_rollback.sql")
    for name in ("add_genome_browser_build_uploads", "add_genome_browser_builds")
)
BUCKET = "genome-references"
SHA = "c" * 64
TOOLS = {"htslib": "1.21", "pysam": "0.23.0"}
# Each role's object basename and the size the tests store it at.
FILES = {
    "fasta": ("genome.fa.gz", 4096),
    "fasta_fai": ("genome.fa.gz.fai", 120),
    "fasta_gzi": ("genome.fa.gz.gzi", 64),
    "gtf": ("genes.gtf.gz", 2048),
    "gtf_tbi": ("genes.gtf.gz.tbi", 256),
}
# Two genes, as the build reads them from the GTF.
GENES = (
    {"gene_id": "AT1G01010", "gene_name": "NAC001", "contig": "Chr1",
     "gene_start": 3631, "gene_end": 5899, "strand": "+"},
    {"gene_id": "AT1G01020", "gene_name": None, "contig": "Chr1",
     "gene_start": 6788, "gene_end": 9130, "strand": "-"},
)


def apply_migrations(c) -> None:
    """The genome migrations, then the annotation and browser migrations."""
    c.execute("DROP TABLE IF EXISTS public.genome_browser_genes CASCADE")
    c.execute("DROP TABLE IF EXISTS public.genome_browser_builds CASCADE")
    c.execute("DROP TABLE IF EXISTS public.genome_annotation_builds CASCADE")
    apply_genome_migrations(c)
    for migration in MIGRATIONS:
        c.execute(_sql_body(migration))


def start_build(cur, version_id, user=WRITER, role="bloom_writer"):
    """Start a build: (build id, number, folder)."""
    return call(cur, "SELECT * FROM public.start_genome_browser_build(%s, %s)",
                (version_id, json.dumps(TOOLS)), user=user, role=role)


def put_files(cur, folder, only=None, user=WRITER, role="bloom_writer") -> None:
    """Store the build's five files, or only the roles named, as Storage records an upload."""
    for name, (basename, size) in FILES.items():
        if only is None or name in only:
            put_file(cur, BUCKET, f"{folder}/{basename}", size=size, user=user, role=role)


def files_json(**overrides) -> str:
    """The five roles with their checksums and sizes, as finish takes them.

    A keyword replaces one role's entry; `role=None` leaves that role out.
    """
    files = {name: {"sha256": SHA, "bytes": size} for name, (_, size) in FILES.items()}
    files.update(overrides)
    return json.dumps({name: entry for name, entry in files.items() if entry is not None})


def add_genes(cur, build_id, genes=GENES, user=WRITER, role="bloom_writer"):
    """Add genes to a build; returns how many were added."""
    return call(cur, "SELECT public.add_genome_browser_genes(%s, %s)", (build_id, json.dumps(genes)),
                user=user, role=role)[0]


def finish_build(cur, build_id, files=None, gene_count=len(GENES),
                 user=WRITER, role="bloom_writer"):
    return call(cur, "SELECT public.finish_genome_browser_build(%s, %s, %s)",
                (build_id, files_json() if files is None else files, gene_count),
                user=user, role=role)


def ready_build(cur, version_id):
    """A ready build with its five files stored and GENES added: (build id, folder)."""
    build_id, _, folder = start_build(cur, version_id)
    put_files(cur, folder)
    add_genes(cur, build_id)
    finish_build(cur, build_id)
    return build_id, folder
