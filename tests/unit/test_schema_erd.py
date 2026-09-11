"""Tests for scripts/schema_erd.py: wrapping tbls output into erd.md, and the `make erd` guard.

The guard refuses to write the diagram from a dev database whose recorded migrations differ
from the checkout, because that diagram would not match what CI generates.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "schema_erd.py"


def _load():
    spec = importlib.util.spec_from_file_location("schema_erd", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["schema_erd"] = module
    spec.loader.exec_module(module)
    return module


erd = _load()

TBLS_OUTPUT = (
    "erDiagram\n"
    "\n"
    '"public.scrna_de" }o--|| "public.scrna_datasets" : "FOREIGN KEY (dataset_id) REFERENCES scrna_datasets(id)"   \n'
    "\n"
    '"public.scrna_de" {\n'
    "  bigint id  \n"
    "  bigint dataset_id FK\n"
    "}\n"
    "\n\n"
)


# --- wrap ----------------------------------------------------------------------


def test_wrap_puts_one_mermaid_block_under_a_generated_header():
    lines = erd.wrap(TBLS_OUTPUT).split("\n")
    assert lines[0] == erd.HEADER
    assert lines.count("```mermaid") == 1
    assert lines.count("```") == 1
    assert lines.index("```mermaid") < lines.index("erDiagram") < lines.index("```")


def test_wrap_has_no_trailing_whitespace_and_one_final_newline():
    text = erd.wrap(TBLS_OUTPUT)
    assert text.endswith("```\n") and not text.endswith("\n\n")
    assert all(line == line.rstrip() for line in text.split("\n"))


def test_wrap_keeps_the_diagram_text():
    text = erd.wrap(TBLS_OUTPUT)
    assert '"public.scrna_de" }o--|| "public.scrna_datasets"' in text
    assert "  bigint dataset_id FK" in text


def test_header_is_an_html_comment_so_github_does_not_render_it():
    assert erd.HEADER.startswith("<!--") and erd.HEADER.endswith("-->")


# --- mismatches ----------------------------------------------------------------

FILES = [
    "20260101000000_one.sql",
    "20260102000000_two.sql",
    "20250617163449_insert_image_2.0_rpc.sql",
]
RECORDED = [
    ("20260101000000", "one"),
    ("20260102000000", "two"),
    ("20250617163449", "insert_image_2.0_rpc"),
]


def test_matching_database_has_no_mismatches():
    assert erd.mismatches(RECORDED, FILES) == []


def test_same_version_with_a_different_name_is_named():
    recorded = [("20260101000000", "one_draft"), *RECORDED[1:]]
    problems = erd.mismatches(recorded, FILES)
    assert len(problems) == 1
    assert "20260101000000" in problems[0] and "one_draft" in problems[0] and "one" in problems[0]


def test_extra_recorded_version_is_named():
    problems = erd.mismatches([*RECORDED, ("20260103000000", "folded_away")], FILES)
    assert len(problems) == 1 and "20260103000000" in problems[0]


def test_missing_version_is_named():
    problems = erd.mismatches(RECORDED[:2], FILES)
    assert len(problems) == 1 and "20250617163449" in problems[0]


def test_migration_file_name_parsing():
    assert erd.parse_migration_file("20250617163449_insert_image_2.0_rpc.sql") == (
        "20250617163449",
        "insert_image_2.0_rpc",
    )


# --- guard CLI -------------------------------------------------------------------


def _guard(tmp_path: Path, rows: str) -> subprocess.CompletedProcess:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in FILES:
        (migrations / name).write_text("SELECT 1;\n")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "guard", "--migrations-dir", str(migrations)],
        input=rows,
        capture_output=True,
        text=True,
    )


def test_guard_passes_a_matching_database(tmp_path):
    rows = "".join(f"{v}|{n}\n" for v, n in RECORDED)
    assert _guard(tmp_path, rows).returncode == 0


def test_wrap_refuses_output_without_an_er_diagram():
    for bad in ("", "\n\n", "Error: could not connect to the database\n"):
        try:
            erd.wrap(bad)
        except ValueError:
            continue
        raise AssertionError(f"wrap accepted {bad!r}")


def test_wrap_cli_fails_on_empty_input_and_prints_nothing():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "wrap"], input="", capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "erDiagram" in result.stderr
    assert result.stdout == ""


def test_qualify_adds_public_only_where_missing():
    assert erd.qualify(["scrna_de", "public.cyl_scans", "storage.objects"]) == (
        "public.scrna_de,public.cyl_scans,storage.objects"
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _branch_with_migration(tmp_path: Path, sql: str) -> Path:
    _git(tmp_path, "init", "-q", "--initial-branch=staging")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "supabase" / "migrations").mkdir(parents=True)
    (tmp_path / "supabase/migrations/20260101000000_base.sql").write_text("CREATE TABLE base (id int);\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    (tmp_path / "supabase/migrations/20260102000000_change.sql").write_text(sql)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "change")
    return tmp_path


def test_changed_tables_are_what_the_branch_migrations_touch(tmp_path):
    repo = _branch_with_migration(
        tmp_path,
        "CREATE TABLE IF NOT EXISTS public.scrna_de_genes (id int);\n"
        "ALTER TABLE public.scrna_de ADD COLUMN IF NOT EXISTS run_id bigint;\n"
        "ALTER TABLE public.other ENABLE ROW LEVEL SECURITY;\n",
    )
    assert erd.changed_tables("staging", "feature", staging_ref="staging", repo=repo) == [
        "scrna_de",
        "scrna_de_genes",
    ]


def test_tables_cli_prints_the_tbls_table_list(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "tables", "--tables", "scrna_de,public.cyl_scans"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "public.scrna_de,public.cyl_scans"


def test_tables_cli_refuses_when_the_branch_changes_no_table(tmp_path):
    repo = _branch_with_migration(tmp_path, "CREATE OR REPLACE FUNCTION f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql;\n")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "tables", "--changed", "staging", "--head", "feature",
         "--staging-ref", "staging", "--repo", str(repo)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "no table" in result.stderr.lower()


def test_guard_refuses_when_no_rows_were_read(tmp_path):
    """An empty read means psql failed or the database is unmigrated, not that every file is missing."""
    result = _guard(tmp_path, "")
    assert result.returncode == 1
    assert "no migration rows were read" in result.stderr.lower()
    assert "20260101000000" not in result.stderr


def test_guard_refuses_a_mismatched_database_naming_each_version(tmp_path):
    rows = "20260101000000|one_draft\n20260102000000|two\n"
    result = _guard(tmp_path, rows)
    assert result.returncode == 1
    assert "20260101000000" in result.stderr and "20250617163449" in result.stderr
    assert "artifact" in result.stderr
