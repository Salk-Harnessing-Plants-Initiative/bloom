"""Every copy of the sequence-advance body matches the migration's (bloom#1022, design D6).

The body is the `DO $advance$ … $advance$;` block in
supabase/migrations/*_advance_lagging_id_sequences.sql. It is copied unchanged to
scripts/sql/advance_behind_sequences.sql (run by `make seed-gravi`) and to any later
*_readvance_id_sequences_<reason>.sql migration. A copy that drifts would advance
differently from the migration that fixed prod.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"
ADVANCE_SCRIPT = REPO_ROOT / "scripts" / "sql" / "advance_behind_sequences.sql"
GRAVI_SEED = REPO_ROOT / "scripts" / "seed_gravi_mock_data.sql"
MAKEFILE = REPO_ROOT / "Makefile"


def _block(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = [i for i, line in enumerate(lines) if line == "DO $advance$"]
    end = [i for i, line in enumerate(lines) if line == "$advance$;"]
    assert len(start) == 1 and len(end) == 1, (path.name, start, end)
    return lines[start[0] : end[0] + 1]


def _original() -> list[str]:
    [path] = sorted(MIGRATIONS.glob("*_advance_lagging_id_sequences.sql"))
    return _block(path)


def test_advance_script_matches_the_migration():
    assert _block(ADVANCE_SCRIPT) == _original()


def test_advance_script_has_its_own_transaction_and_lock_timeout():
    code = [
        line.strip()
        for line in ADVANCE_SCRIPT.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    assert code[:2] == ["BEGIN;", "SET LOCAL lock_timeout = '5s';"]
    assert code[-1] == "COMMIT;"


def test_every_readvance_migration_matches_the_migration():
    for path in sorted(MIGRATIONS.glob("*_readvance_id_sequences_*.sql")):
        assert _block(path) == _original(), path.name


def _target(name: str) -> list[str]:
    lines = MAKEFILE.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"{name}:")
    body = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        body.append(line)
    return body


def test_seed_gravi_runs_the_seed_then_the_advance_script():
    recipe = "\n".join(_target("seed-gravi"))
    seed = recipe.find("scripts/seed_gravi_mock_data.sql")
    advance = recipe.find("scripts/sql/advance_behind_sequences.sql")
    assert seed != -1 and advance != -1 and seed < advance
    assert "db-dev" in recipe and "ON_ERROR_STOP=1" in recipe
    # Without it compose can't resolve the stack's variables and refuses to run (found by
    # the manual run, tasks 3.10).
    assert "--env-file .env.dev" in recipe


def test_gravi_seed_header_points_to_the_make_target():
    header = GRAVI_SEED.read_text(encoding="utf-8").split("BEGIN;", 1)[0]
    assert "make seed-gravi" in header
