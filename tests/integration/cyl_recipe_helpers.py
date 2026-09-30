"""Shared helpers for the add-cyl-trait-recipe-key tests.

Migration and rollback files are found by glob, never by timestamp, so a restamp only
renames files. `apply_recipe_rollbacks` puts the database back to a pre-change state
by applying whichever of this change's rollbacks exist, newest first, so a test
written in one section keeps passing as later sections add migrations.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent

# This change's migrations, oldest first: (name, migration glob, rollback glob).
RECIPE_LAYERS = (
    (1, "*_add_cyl_trait_recipe_key.sql", "*_add_cyl_trait_recipe_key_rollback.sql"),
    (
        2,
        "*_stamp_cyl_trait_source_recipe_and_run.sql",
        "*_stamp_cyl_trait_source_recipe_and_run_rollback.sql",
    ),
    (
        3,
        "*_add_cyl_trait_recipe_reads.sql",
        "*_add_cyl_trait_recipe_reads_rollback.sql",
    ),
    (
        4,
        "*_add_cyl_dataset_recipe_mode.sql",
        "*_add_cyl_dataset_recipe_mode_rollback.sql",
    ),
)

RECIPE_AND_RUN_COLUMNS = (
    "recipe_key",
    "recipe_key_version",
    "scan_id",
    "argo_workflow_name",
    "cyl_pipeline_run_id",
)


def find_one(directory: str, glob: str) -> Path | None:
    matches = sorted((REPO_ROOT / "supabase" / directory).glob(glob))
    return matches[-1] if matches else None


def migration(n: int) -> Path | None:
    return find_one("migrations", RECIPE_LAYERS[n - 1][1])


def rollback(n: int) -> Path | None:
    return find_one("rollbacks", RECIPE_LAYERS[n - 1][2])


def sql_body(path: Path) -> str:
    """The file minus its BEGIN;/COMMIT; wrapper, for applying inside a test's
    uncommitted transaction (CRLF-safe)."""
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def apply_recipe_rollbacks(cur, down_to: int = 1) -> list[int]:
    """Apply this change's rollbacks that exist, newest first, down to and including
    layer `down_to`. Returns the layers rolled back."""
    applied = []
    for n, _, _ in reversed(RECIPE_LAYERS):
        if n < down_to:
            break
        path = rollback(n)
        if path is not None:
            cur.execute(sql_body(path))
            applied.append(n)
    return applied


def acl_set(cur, signature: str) -> set[tuple[str, str]]:
    """{(grantee, privilege)} from a function's ACL; PUBLIC for grantee 0. Compared
    as a set because grant order and grantor differ between db push and a test."""
    cur.execute(
        """
        SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE a.grantee::regrole::text END,
               a.privilege_type
          FROM pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
         WHERE p.oid = %s::regprocedure
        """,
        (signature,),
    )
    return set(cur.fetchall())


def seed_scan(cur, n_images: int = 2) -> tuple[int, list[int]]:
    cur.execute("INSERT INTO cyl_scans DEFAULT VALUES RETURNING id")
    scan_id = cur.fetchone()[0]
    img_ids = []
    for _ in range(n_images):
        cur.execute(
            "INSERT INTO cyl_images (scan_id) VALUES (%s) RETURNING id", (scan_id,)
        )
        img_ids.append(cur.fetchone()[0])
    return scan_id, img_ids


def seed_source(
    cur,
    metadata_text: str | None,
    *,
    name: str = "seeded",
    idempotency_key: str | None = None,
) -> int:
    """Insert a source directly (as the break-glass role would), leaving the recipe
    and run columns NULL. `metadata_text` is raw JSON text, passed as ::jsonb."""
    cur.execute(
        "INSERT INTO cyl_trait_sources (name, metadata, idempotency_key) "
        "VALUES (%s, %s::jsonb, %s) RETURNING id",
        (name, metadata_text, idempotency_key),
    )
    return cur.fetchone()[0]


def recipe_columns(cur, source_id: int) -> dict:
    cur.execute(
        f"SELECT {', '.join(RECIPE_AND_RUN_COLUMNS)} FROM cyl_trait_sources WHERE id = %s",
        (source_id,),
    )
    row = cur.fetchone()
    return dict(zip(RECIPE_AND_RUN_COLUMNS, row)) if row else {}
