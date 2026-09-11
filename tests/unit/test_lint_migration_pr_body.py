"""Tests for scripts/lint_migration_pr_body.py: a migration PR's body documents its schema change.

The body needs a mermaid erDiagram naming every table the migrations touch, and a
constraints table listing every constraint and index they add. `No schema changes.` passes
only when it is true. Most cases test check_body() on scanned SQL; the CLI cases run the
script against scratch git repos.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
SCRIPT = SCRIPTS / "lint_migration_pr_body.py"
FIXTURES = Path(__file__).parent / "fixtures" / "migrations"
HEADER = "| Name | Table | Type | What it refuses | How it is added |"
SEPARATOR = "| --- | --- | --- | --- | --- |"


def _load(name: str):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lint = _load("lint_migration_pr_body")
scan = _load("migration_sql").scan

SQL = (
    "CREATE TABLE IF NOT EXISTS public.scrna_de_genes (id int, CONSTRAINT g_one UNIQUE (id));\n"
    "ALTER TABLE public.scrna_de ADD CONSTRAINT scrna_de_run_in_same_dataset CHECK (true);\n"
    "CREATE INDEX IF NOT EXISTS g_idx ON public.scrna_de_genes (id);\n"
)
FACTS = scan(SQL)
NAMES = ["g_one", "scrna_de_run_in_same_dataset", "g_idx"]


def _diagram(*tables: str) -> str:
    entities = "\n".join(f'"public.{t}" {{\n  bigint id\n}}' for t in tables)
    return f"```mermaid\nerDiagram\n\n{entities}\n```"


def _table(names, header: str = HEADER, separator: str = SEPARATOR) -> str:
    rows = "\n".join(f"| `{n}` | t | CHECK | bad rows | drop, then add |" for n in names)
    return f"{header}\n{separator}\n{rows}"


def _body(tables=("scrna_de_genes", "scrna_de"), names=NAMES, **table_kwargs) -> str:
    return (
        "## Summary\n\nAdds gene rows.\n\n## Schema changes\n\n"
        f"{_diagram(*tables)}\n\n{_table(names, **table_kwargs)}\n"
    )


def _problems(body, facts=FACTS) -> list[str]:
    return lint.check_body(body, facts)


# --- the complete body ----------------------------------------------------------


def test_complete_body_passes():
    assert _problems(_body()) == []


def test_crlf_body_passes():
    assert _problems(_body().replace("\n", "\r\n")) == []


def test_real_common_results_migration_needs_every_name():
    facts = scan((FIXTURES / "20260912090000_scrna_de_common_results.sql").read_text())
    names = sorted(facts.constraints_added | facts.indexes_added)
    body = _body(tables=sorted(facts.tables_touched), names=names)
    assert _problems(body, facts) == []
    missing_one = _body(tables=sorted(facts.tables_touched), names=names[1:])
    assert any(names[0] in p for p in _problems(missing_one, facts))


# --- the diagram ------------------------------------------------------------------


def test_missing_diagram_fails_naming_the_snapshot_command():
    body = "## Schema changes\n\n" + _table(NAMES)
    problems = _problems(body)
    assert any("erDiagram" in p and "make erd-snapshot" in p for p in problems)


def test_prefix_sharing_table_does_not_satisfy_the_diagram():
    problems = _problems(_body(tables=("scrna_de_genes", "scrna_de_runs")))
    assert any("scrna_de" in p and "scrna_de_runs" not in p for p in problems)


def test_diagram_without_public_prefix_still_counts():
    body = _body().replace('"public.scrna_de"', '"scrna_de"')
    assert _problems(body) == []


# --- the constraints table ------------------------------------------------------


def test_name_only_in_prose_fails():
    body = _body(names=NAMES[:2]) + "\nAlso adds g_idx.\n"
    problems = _problems(body)
    assert any("g_idx" in p for p in problems)


def test_name_only_in_the_diagram_fails():
    body = _body(names=NAMES[:2]).replace("bigint id", "bigint id\n  text g_idx")
    assert any("g_idx" in p for p in _problems(body))


@pytest.mark.parametrize(
    "header, separator",
    [
        ("Name | Table | Type | What it refuses | How it is added", "--- | --- | --- | --- | ---"),
        ("|  Name  | Table | Type | What it refuses |  How it is added  |", "|:---|:---:|---:|---|:---|"),
    ],
)
def test_header_variants_are_recognised(header, separator):
    assert _problems(_body(header=header, separator=separator)) == []


def test_a_table_without_how_it_is_added_is_not_the_constraints_table():
    other = "| Name | Purpose |\n| --- | --- |\n" + "\n".join(f"| {n} | x |" for n in NAMES)
    body = "## Schema changes\n\n" + _diagram("scrna_de_genes", "scrna_de") + "\n\n" + other + "\n"
    problems = _problems(body)
    assert any("How it is added" in p for p in problems)


def test_constraints_table_is_optional_when_nothing_is_added():
    facts = scan("ALTER TABLE public.scrna_de ADD COLUMN IF NOT EXISTS run_id bigint;")
    assert _problems("## Schema changes\n\n" + _diagram("scrna_de") + "\n", facts) == []


def test_unnamed_constraint_fails():
    facts = scan("ALTER TABLE public.t ADD CHECK (x > 0);")
    problems = _problems(_body(tables=("t",), names=[]), facts)
    assert any("unnamed" in p.lower() and "t" in p for p in problems)


# --- the opt-out ------------------------------------------------------------------

FUNCTION_ONLY = FIXTURES / "20260910120000_fix_refresh_cyl_experiment_trait_counts_safeupdate.sql"


def test_opt_out_passes_for_a_function_only_migration():
    facts = scan(FUNCTION_ONLY.read_text())
    assert _problems("## Schema changes\n\nNo schema changes.\n", facts) == []


@pytest.mark.parametrize(
    "sql, named",
    [
        ("CREATE TABLE public.x (id int);", "x"),
        ("DROP TABLE IF EXISTS public.x;", "x"),
        ("ALTER TABLE public.t ADD CONSTRAINT c CHECK (true);", "c"),
        ("CREATE INDEX IF NOT EXISTS i ON public.t (a);", "i"),
        ("CREATE OR REPLACE VIEW public.cyl_plants_extended AS SELECT 1 AS id;", "cyl_plants_extended"),
        ("DROP VIEW IF EXISTS public.old_view;", "old_view"),
        ("ALTER VIEW public.old_view RENAME TO new_view;", "old_view"),
        ("ALTER VIEW public.plant_view RENAME COLUMN a TO b;", "plant_view"),
    ],
)
def test_opt_out_fails_when_the_schema_changes(sql, named):
    problems = _problems("No schema changes.\n", scan(sql))
    assert problems and any(named in p for p in problems)


def test_diagram_must_show_a_created_view():
    facts = scan("CREATE OR REPLACE VIEW public.cyl_plants_extended AS SELECT 1 AS id;")
    missing = _problems("## Schema changes\n\n" + _diagram("cyl_plants") + "\n", facts)
    assert any("cyl_plants_extended" in p for p in missing)
    assert _problems("## Schema changes\n\n" + _diagram("cyl_plants_extended") + "\n", facts) == []


def test_diagram_must_show_a_renamed_view_under_its_new_name():
    facts = scan("ALTER VIEW public.old_view RENAME TO new_view;")
    assert any("new_view" in p for p in _problems("## Schema changes\n\n" + _diagram("old_view") + "\n", facts))
    assert _problems("## Schema changes\n\n" + _diagram("new_view") + "\n", facts) == []


def test_missing_diagram_message_counts_views():
    problems = _problems("## Schema changes\n\n" + _table(NAMES))
    assert any("table, view, constraint or index" in p for p in problems)


# --- migrations that only drop things ---------------------------------------------

DROP_ONLY = (
    "DROP VIEW IF EXISTS public.old_view;\n"
    "DROP TABLE IF EXISTS public.old_table;\n"
    "DROP INDEX IF EXISTS public.old_idx;\n"
)


def test_a_drop_only_migration_needs_no_diagram_only_the_names():
    body = "## Schema changes\n\nDrops the view `old_view`, the table `old_table` and the index `old_idx`.\n"
    assert _problems(body, scan(DROP_ONLY)) == []


def test_a_drop_only_migration_must_name_everything_it_drops():
    problems = _problems("## Schema changes\n\nDrops the view `old_view`.\n", scan(DROP_ONLY))
    assert len(problems) == 1
    assert "old_table" in problems[0] and "old_idx" in problems[0] and "old_view" not in problems[0]


def test_opt_out_for_a_drop_only_migration_says_what_to_write_instead():
    problems = _problems("No schema changes.\n", scan("DROP VIEW IF EXISTS public.old_view;"))
    assert any("old_view" in p and "under Schema changes" in p for p in problems)


def test_a_drop_alongside_other_changes_must_still_be_named():
    facts = scan("DROP TABLE IF EXISTS public.old_data;\nALTER TABLE public.t ADD COLUMN IF NOT EXISTS c int;\n")
    problems = _problems("## Schema changes\n\n" + _diagram("t") + "\n", facts)
    assert len(problems) == 1 and "old_data" in problems[0]
    assert _problems("## Schema changes\n\nDrops `old_data`.\n\n" + _diagram("t") + "\n", facts) == []


def test_dropped_names_count_only_under_schema_changes():
    body = "## Summary\n\nRetires old_view, old_table and old_idx.\n\n## Schema changes\n\nSee the summary.\n"
    problems = _problems(body, scan(DROP_ONLY))
    assert len(problems) == 1
    assert all(name in problems[0] for name in ("old_view", "old_table", "old_idx"))


def test_dropped_names_inside_a_comment_do_not_count():
    assert _problems("## Schema changes\n\n<!-- old_view old_table old_idx -->\n", scan(DROP_ONLY))


def test_an_index_only_migration_needs_its_constraints_row_but_no_diagram():
    facts = scan("CREATE INDEX IF NOT EXISTS i ON public.t (a);")
    assert _problems("## Schema changes\n\n" + _table(["i"]) + "\n", facts) == []
    assert any("No constraints table" in p for p in _problems("## Schema changes\n\nAdds an index.\n", facts))


def test_a_replaced_index_is_named_by_its_constraints_row():
    facts = scan("DROP INDEX IF EXISTS public.i;\nCREATE INDEX i ON public.t (a);\n")
    assert _problems("## Schema changes\n\n" + _table(["i"]) + "\n", facts) == []


def test_messages_never_claim_a_migration_only_drops_when_it_also_adds():
    facts = scan("DROP INDEX IF EXISTS public.old_i;\nCREATE INDEX new_i ON public.t (a);\n")
    problems = _problems("## Schema changes\n\nSwaps an index.\n", facts)
    assert any("old_i" in p for p in problems) and any("new_i" in p for p in problems)
    assert not any("only drop" in p for p in problems)


def test_opt_out_inside_an_html_comment_is_ignored():
    facts = scan(FUNCTION_ONLY.read_text())
    problems = _problems("## Schema changes\n\n<!-- No schema changes. -->\n", facts)
    assert problems


def test_untouched_template_fails_for_a_table_creating_migration():
    template = (
        "## Schema changes\n\n"
        "<!-- Paste the output of `make erd-snapshot CHANGED=origin/staging` here. -->\n\n"
        f"{HEADER}\n{SEPARATOR}\n\n"
        "<!-- Or, if no table, constraint or index changes: No schema changes. -->\n"
    )
    assert _problems(template)


@pytest.mark.parametrize("body", ["", "   \n", None])
def test_empty_body_fails_with_guidance(body):
    problems = _problems(body)
    assert len(problems) == 1 and "Schema changes" in problems[0]


# --- CLI ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "--initial-branch=staging")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "supabase" / "migrations").mkdir(parents=True)
    (tmp_path / "supabase/migrations/20260101000000_base.sql").write_text("CREATE TABLE base (id int);\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    return tmp_path


def _commit_migration(repo: Path) -> None:
    (repo / "supabase/migrations/20260102000000_change.sql").write_text(SQL)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")


def _cli(repo: Path, *extra: str, body: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "PR_BODY"}
    if body is not None:
        env["PR_BODY"] = body
    return subprocess.run(
        [sys.executable, str(SCRIPT), "staging", "--head", "feature", "--staging-ref", "staging",
         "--repo", str(repo), *extra],
        capture_output=True, text=True, env=env, cwd=repo,
    )


def test_cli_passes_a_pr_without_a_migration_change(repo):
    (repo / "README.md").write_text("docs\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "docs")
    assert _cli(repo).returncode == 0


def test_cli_reads_the_body_from_the_environment_as_data(repo):
    _commit_migration(repo)
    result = _cli(repo, body="$(touch pwned) `touch pwned2` No diagram here")
    assert result.returncode == 1
    assert "::error title=PR body schema section::" in result.stdout
    assert not (repo / "pwned").exists() and not (repo / "pwned2").exists()


def test_cli_passes_a_complete_body_from_a_file(repo, tmp_path_factory):
    _commit_migration(repo)
    body_file = tmp_path_factory.mktemp("body") / "body.md"
    body_file.write_text(_body())
    assert _cli(repo, "--body-file", str(body_file)).returncode == 0


def test_cli_unresolvable_base_exits_2(repo):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "no-such-ref", "--repo", str(repo), "--staging-ref", "staging"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2


def test_snapshot_tables_and_required_tables_agree(repo):
    _commit_migration(repo)
    schema_erd = _load("schema_erd")
    mc = _load("migration_changes")
    facts = mc.changed_facts("staging", "feature", staging_ref="staging", repo=repo)
    assert schema_erd.changed_tables("staging", "feature", staging_ref="staging", repo=repo) == sorted(
        facts.tables_touched
    )
