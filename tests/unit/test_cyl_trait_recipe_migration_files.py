"""Text checks on the add-cyl-trait-recipe-key migration and rollback files.

These pin properties an integration test cannot see: the lock timeout, named and
guarded constraints, ownership, and that no migration scans cyl_scan_traits.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"
ROLLBACKS = REPO_ROOT / "supabase" / "rollbacks"


def _one(directory: Path, glob: str) -> Path:
    matches = sorted(directory.glob(glob))
    if not matches:
        pytest.skip(f"{glob} not written yet")
    return matches[-1]


def _code(path: Path) -> str:
    """The file with -- comments removed, so prose cannot satisfy or trip a check."""
    return "\n".join(
        re.sub(r"--.*$", "", line)
        for line in path.read_text(encoding="utf-8").splitlines()
    )


M1 = "*_add_cyl_trait_recipe_key.sql"
R1 = "*_add_cyl_trait_recipe_key_rollback.sql"


def test_m1_sets_lock_timeout_and_reloads_postgrest():
    code = _code(_one(MIGRATIONS, M1))
    assert re.search(r"SET\s+LOCAL\s+lock_timeout", code, re.I)
    assert re.search(r"NOTIFY\s+pgrst", code, re.I)


def test_m1_constraints_are_named_and_guarded():
    code = _code(_one(MIGRATIONS, M1))
    for check in (
        "cyl_trait_sources_recipe_key_format_check",
        "cyl_trait_sources_recipe_key_version_check",
    ):
        assert re.search(rf"DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+{check}", code, re.I), (
            check
        )
        assert re.search(rf"ADD\s+CONSTRAINT\s+{check}\s+CHECK", code, re.I), check
    for fk in (
        "cyl_trait_sources_scan_id_fkey",
        "cyl_trait_sources_cyl_pipeline_run_id_fkey",
    ):
        assert re.search(rf"conname\s*=\s*'{fk}'", code, re.I), fk
        assert re.search(rf"ADD\s+CONSTRAINT\s+{fk}\s+FOREIGN\s+KEY", code, re.I), fk
    for idx in (
        "cyl_trait_sources_recipe_key_idx",
        "cyl_trait_sources_scan_id_idx",
        "cyl_pipeline_run_scans_argo_workflow_name_idx",
    ):
        assert re.search(rf"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+{idx}", code, re.I), (
            idx
        )


def test_m1_adds_no_inline_constraint_on_a_column():
    code = _code(_one(MIGRATIONS, M1))
    for stmt in re.findall(r"ADD\s+COLUMN[^,;]*", code, re.I):
        assert not re.search(r"\b(REFERENCES|CHECK)\b", stmt, re.I), stmt


def test_m1_never_reads_cyl_scan_traits():
    assert not re.search(r"\bcyl_scan_traits\b", _code(_one(MIGRATIONS, M1)))


def test_m1_functions_are_owned_by_postgres():
    code = _code(_one(MIGRATIONS, M1))
    created = re.findall(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+public\.(\w+)", code, re.I
    )
    assert set(created) == {
        "cyl_trait_recipe_payload_v1",
        "cyl_trait_recipe_key_v1",
        "cyl_backfill_trait_source_recipe_identity",
    }
    for fn in created:
        assert re.search(
            rf"ALTER\s+FUNCTION\s+public\.{fn}\([^)]*\)\s+OWNER\s+TO\s+postgres",
            code,
            re.I,
        ), fn


def test_every_update_in_m1_has_a_where():
    code = _code(_one(MIGRATIONS, M1))
    for stmt in re.findall(r"\bUPDATE\s+public\.\w+.*?;", code, re.I | re.S):
        assert re.search(r"\bWHERE\b", stmt, re.I), stmt


def test_r1_guard_runs_before_any_drop():
    code = _code(_one(ROLLBACKS, R1))
    guard = re.search(r"RAISE\s+EXCEPTION", code, re.I)
    first_drop = re.search(r"\b(DROP|ALTER\s+TABLE)\b", code, re.I)
    assert guard and first_drop and guard.start() < first_drop.start()
