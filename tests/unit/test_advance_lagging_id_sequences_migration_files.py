"""Text checks on the fix-prod-sequences-behind migration and rollback (bloom#1022).

These pin what the integration tests can't see: the file wrapper and lock timeout, that
the lock comes before any setval, that the migration names no table (it is generic over
pg_get_serial_sequence), that it changes no schema, and that the rollback is a no-op.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"
ROLLBACKS = REPO_ROOT / "supabase" / "rollbacks"
MIGRATION_GLOB = "*_advance_lagging_id_sequences.sql"
ROLLBACK_GLOB = "*_advance_lagging_id_sequences_rollback.sql"

# Issue #1022's 21 behind prod tables. The migration must not special-case any of them.
ISSUE_TABLES = (
    "species",
    "people",
    "assemblies",
    "cyl_scanners",
    "cyl_qc_sets",
    "cyl_qc_codes",
    "cyl_qc_set_codes",
    "cyl_datasets",
    "cyl_scientists",
    "phenotypers",
    "cyl_experiments",
    "plate_plant_traits_list",
    "plates_trait_source",
    "translation_projects",
    "translation_project_users",
    "gene_candidate_scientists",
    "ortho_gene_id_map",
    "scrna_datasets",
    "scrna_cells",
    "scrna_genes",
    "scrna_counts",
)


def _exactly_one(directory: Path, glob: str) -> Path:
    matches = sorted(directory.glob(glob))
    assert len(matches) == 1, f"expected exactly one {glob}, found {matches}"
    return matches[0]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _without_comments(text: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", line) for line in text.splitlines())


def _without_strings(code: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", code)


def test_migration_and_rollback_exist_once():
    m = _exactly_one(MIGRATIONS, MIGRATION_GLOB)
    r = _exactly_one(ROLLBACKS, ROLLBACK_GLOB)
    assert r.name == m.name.replace(".sql", "_rollback.sql")


def test_migration_wrapper_and_lock_timeout():
    lines = [line.strip() for line in _without_comments(_text(_exactly_one(MIGRATIONS, MIGRATION_GLOB))).splitlines()]
    code = [line for line in lines if line]
    assert code[0] == "BEGIN;"
    assert code[1] == "SET LOCAL lock_timeout = '5s';"
    assert code[-1] == "COMMIT;"


def test_migration_has_one_advance_block():
    lines = _text(_exactly_one(MIGRATIONS, MIGRATION_GLOB)).splitlines()
    assert sum(line.startswith("DO $advance$") for line in lines) == 1
    assert sum(line == "$advance$;" for line in lines) == 1


def test_lock_comes_before_any_setval():
    code = _without_comments(_text(_exactly_one(MIGRATIONS, MIGRATION_GLOB)))
    lock = code.find("LOCK TABLE")
    setval = code.find("setval(")
    assert lock != -1 and setval != -1 and lock < setval


def test_migration_changes_no_schema():
    code = _without_strings(_without_comments(_text(_exactly_one(MIGRATIONS, MIGRATION_GLOB))))
    assert not re.search(r"\b(CREATE|DROP)\b|\bALTER\s+(TABLE|SEQUENCE)\b", code, re.I)


def test_migration_names_no_table():
    text = _text(_exactly_one(MIGRATIONS, MIGRATION_GLOB))
    named = [t for t in ISSUE_TABLES if re.search(rf"\b{t}\b", text)]
    assert named == []


def test_rollback_changes_no_sequence():
    text = _text(_exactly_one(ROLLBACKS, ROLLBACK_GLOB))
    code = _without_comments(text)
    assert not re.search(r"\bsetval\b|\bnextval\b|\bALTER\s+SEQUENCE\b", code, re.I)
    assert "STAGING HOT-APPLY ONLY" in text
    assert "supabase migration repair --status reverted" in text
    assert "behind" in text  # the header says why it does nothing
