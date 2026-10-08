"""Shared helpers for the genome reference tests.

Each test applies both genome migrations inside its own transaction and rolls it back, so the
database is left unchanged whatever it already holds. Roles are exercised with SET LOCAL ROLE
and the signed-in user's id, the way PostgREST and Storage call in.
"""

import json
import uuid

import pytest

from tests.integration.test_rnaseq_runs import _find_one, _sql_body

psycopg = pytest.importorskip("psycopg")

TABLES_MIGRATION = _find_one("migrations", "*_add_genome_references.sql")
UPLOADS_MIGRATION = _find_one("migrations", "*_add_genome_reference_uploads.sql")
TABLES_ROLLBACK = _find_one("rollbacks", "*_add_genome_references_rollback.sql")
UPLOADS_ROLLBACK = _find_one("rollbacks", "*_add_genome_reference_uploads_rollback.sql")
BUCKET = "genome-references"
TABLES = ("genome_references", "genome_reference_versions")

WRITER = str(uuid.uuid4())
OTHER_WRITER = str(uuid.uuid4())
SHA_A = "a" * 64
SHA_B = "b" * 64


def apply_migrations(c) -> None:
    """Both migrations, applied over whatever this database already holds."""
    c.execute("DROP TRIGGER IF EXISTS rnaseq_runs_keep_genome_version ON public.rnaseq_runs")
    c.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM information_schema.columns WHERE "
        "table_schema = 'public' AND table_name = 'rnaseq_runs' AND "
        "column_name = 'genome_version_id') THEN "
        "UPDATE public.rnaseq_runs SET genome_version_id = NULL; END IF; END $$"
    )
    for table in reversed(TABLES):
        c.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")
    c.execute(_sql_body(TABLES_MIGRATION))
    c.execute(_sql_body(UPLOADS_MIGRATION))


def new_genome_name() -> str:
    """A genome name no other test or real upload uses."""
    return f"g{uuid.uuid4().hex[:12]}"


def add_species(cur) -> int:
    tag = uuid.uuid4().hex[:12]
    cur.execute(
        "INSERT INTO public.species (genus, species, common_name) "
        "VALUES (%s, %s, %s) RETURNING id",
        (f"Testgenus{tag}", f"genomeus{tag}", f"genome-test-{tag}"),
    )
    return cur.fetchone()[0]


def as_role(cur, role, user=None) -> None:
    """Act as `role`, signed in as `user` (no user id when None)."""
    cur.execute(f"SET LOCAL ROLE {role}")
    claims = {"role": role, **({"sub": user} if user else {})}
    cur.execute("SELECT set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))
    # Older auth.uid() definitions read only this one.
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (user or "",))


def as_admin(cur) -> None:
    cur.execute("RESET ROLE")
    cur.execute("SELECT set_config('request.jwt.claims', '', true)")
    cur.execute("SELECT set_config('request.jwt.claim.sub', '', true)")


def refused(cur, sql, params, error, match=None) -> None:
    cur.execute("SAVEPOINT refused")
    try:
        with pytest.raises(error, match=match):
            cur.execute(sql, params)
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT refused")


def start(cur, genome, species=None, user=WRITER, **extra):
    """Start a version as a writer: (version_id, version, fasta_path, gtf_path)."""
    as_role(cur, "bloom_writer", user)
    args = {"p_genome": genome, "p_species_id": species, **extra}
    cur.execute(
        "SELECT * FROM public.start_genome_version("
        + ", ".join(f"{k} => %({k})s" for k in args)
        + ")",
        args,
    )
    row = cur.fetchone()
    as_admin(cur)
    return row


def put_object(cur, name, size, bucket=BUCKET, role="bloom_writer", user=WRITER):
    as_role(cur, role, user)
    cur.execute(
        "INSERT INTO storage.objects (bucket_id, name, metadata) VALUES (%s, %s, %s) RETURNING id",
        (bucket, name, json.dumps({"size": size, "mimetype": "application/gzip"})),
    )
    object_id = cur.fetchone()[0]
    as_admin(cur)
    return object_id


def finish(cur, version_id, fasta_bytes=100, gtf_bytes=50, user=WRITER):
    as_role(cur, "bloom_writer", user)
    cur.execute(
        "SELECT public.finish_genome_version(%s, %s, %s, %s, %s)",
        (version_id, SHA_A, fasta_bytes, SHA_B, gtf_bytes),
    )
    version = cur.fetchone()[0]
    as_admin(cur)
    return version


def abandon(cur, version_id, user=WRITER):
    as_role(cur, "bloom_writer", user)
    cur.execute("SELECT public.abandon_genome_version(%s)", (version_id,))
    changed = cur.fetchone()[0]
    as_admin(cur)
    return changed


def uploaded(cur, genome, species_id, user=WRITER):
    """A started version with both files stored: (version_id, version, fasta_path, gtf_path)."""
    row = start(cur, genome, species=species_id, user=user)
    put_object(cur, row[2], 100, user=user)
    put_object(cur, row[3], 50, user=user)
    return row


def ready(cur, genome, species_id):
    """A finished, ready version: (version_id, version, fasta_path, gtf_path)."""
    row = uploaded(cur, genome, species_id)
    finish(cur, row[0])
    return row


def status(cur, version_id):
    cur.execute(
        "SELECT status FROM public.genome_reference_versions WHERE id = %s", (version_id,)
    )
    return cur.fetchone()[0]


def add_run(cur, genome_version_id=None):
    """A Cell Ranger run row, optionally naming a genome version; returns its id."""
    cur.execute(
        "INSERT INTO rnaseq_runs (workflow_type, params, run_key, requested_by, "
        "genome_version_id) VALUES ('scrna-cellranger', %s, %s, %s, %s) RETURNING id",
        (
            json.dumps({"sample": "S1", "reference": "tair10_araport11"}),
            f"S1__tair10_araport11__{WRITER}",
            WRITER,
            genome_version_id,
        ),
    )
    return cur.fetchone()[0]
