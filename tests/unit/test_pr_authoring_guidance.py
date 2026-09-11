"""The two ways a PR gets its description carry the same Schema changes section.

Browser PRs start from .github/pull_request_template.md; Claude-drafted PRs follow
.claude/commands/pr-description.md and open with `gh pr create --body/--body-file`, which
never loads the GitHub template. These tests keep the two sections identical, prove the
worked example and a filled-in template pass the real body check, and pin the constraint
guidance the migration and review commands give.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
TEMPLATE = REPO_ROOT / ".github" / "pull_request_template.md"
COMMANDS = REPO_ROOT / ".claude" / "commands"
PR_DESCRIPTION = COMMANDS / "pr-description.md"
MIGRATION_COMMAND = COMMANDS / "database-migration.md"
REVIEW_COMMAND = COMMANDS / "review-pr.md"
NEW_FEATURE = COMMANDS / "new-feature.md"
EXAMPLE_MIGRATION = (
    Path(__file__).parent / "fixtures" / "migrations" / "20260909090000_scrna_cells_genotype_and_labels.sql"
)

HEADING = "## Schema changes"
OPT_OUT = "No schema changes."


def _load(name: str):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lint = _load("lint_migration_pr_body")
FACTS = _load("migration_sql").scan(EXAMPLE_MIGRATION.read_text(encoding="utf-8"))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(path: Path) -> list[str]:
    """Lines of the first Schema changes section, up to the next heading or fence."""
    lines = _read(path).splitlines()
    assert HEADING in lines, f"{path.name} has no `{HEADING}` section"
    start = lines.index(HEADING)
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith(("## ", "```"))),
        len(lines),
    )
    return lines[start:end]


def _only(path: Path, predicate, what: str) -> str:
    matches = [line for line in _section(path) if predicate(line)]
    assert len(matches) == 1, f"{path.name}: expected one {what} in `{HEADING}`, found {len(matches)}"
    return matches[0]


def _header_row(path: Path) -> str:
    return _only(path, lambda l: l.startswith("|") and "How it is added" in l, "constraints-table header")


# --- the two sections match ------------------------------------------------------


def test_template_and_command_share_the_constraints_table_header():
    assert _header_row(TEMPLATE) == _header_row(PR_DESCRIPTION)
    assert re.search(r"\|\s*Name\s*\|", _header_row(TEMPLATE))


def test_template_and_command_share_the_opt_out_line():
    assert _only(TEMPLATE, lambda l: l.strip() == OPT_OUT, "opt-out line")
    assert _only(PR_DESCRIPTION, lambda l: l.strip() == OPT_OUT, "opt-out line")


def test_template_says_to_delete_the_section_without_migrations():
    assert any("Delete this section if this PR changes no migrations" in l for l in _section(TEMPLATE))


def test_template_and_command_say_to_name_what_is_dropped():
    for path in (TEMPLATE, PR_DESCRIPTION):
        section = "\n".join(_section(path))
        assert "Name every table, view and index the migrations drop" in section, path.name
        assert "no diagram is needed" in section, path.name
        assert "table, view, constraint or index" in section, path.name


def test_command_names_the_snapshot_and_the_pre_check():
    text = _read(PR_DESCRIPTION)
    assert "make erd-snapshot CHANGED=origin/staging" in text
    assert "make pr-body-check" in text


# --- the guidance satisfies the checker ------------------------------------------


def _worked_example() -> str:
    text = _read(PR_DESCRIPTION)
    match = re.search(r"^### Migration PR\n.*?^````markdown\n(.*?)^````", text, re.M | re.S)
    assert match, "pr-description.md needs a `### Migration PR` example in a ````markdown block"
    return match.group(1)


def test_worked_example_passes_the_body_check():
    assert lint.check_body(_worked_example(), FACTS) == []


def test_filled_in_template_passes_the_body_check():
    template = _read(TEMPLATE)
    diagram = "```mermaid\nerDiagram\n" + "\n".join(
        f'"public.{t}" {{\n  bigint id\n}}' for t in sorted(FACTS.tables_touched)
    ) + "\n```\n"
    rows = "\n".join(
        f"| `{name}` | t | kind | bad rows | drop, then add |"
        for name in sorted(FACTS.constraints_added | FACTS.indexes_added)
    )
    header = _header_row(TEMPLATE)
    separator = next(l for l in template.splitlines() if l.startswith("| ---"))
    filled = template.replace(f"{header}\n{separator}", f"{diagram}\n{header}\n{separator}\n{rows}")
    assert lint.check_body(filled, FACTS) == []


def test_untouched_template_fails_for_a_table_creating_migration():
    assert lint.check_body(_read(TEMPLATE), FACTS)


# --- the commands give the constraint guidance ------------------------------------


def _migration_section(heading: str) -> str:
    match = re.search(rf"^{re.escape(heading)}\n(.*?)(?=^## |\Z)", _read(MIGRATION_COMMAND), re.M | re.S)
    assert match, f"database-migration.md has no `{heading}` section"
    return match.group(1)


def test_migration_command_documents_every_constraint_kind():
    section = _migration_section("## Adding constraints and indexes")
    first_cells = " ".join(
        line.split("|")[1] for line in section.splitlines() if line.startswith("|") and "---" not in line
    ).upper()
    for kind in ("CHECK", "FOREIGN KEY", "UNIQUE", "PRIMARY KEY", "EXCLUDE", "INDEX"):
        assert kind in first_cells, f"the pattern table has no row for {kind}"
    assert "pg_get_constraintdef" in section
    assert "NOT VALID" in section


def test_migration_command_describes_migration_prs():
    section = _migration_section("## Migration PRs")
    for phrase in ("make gen-types", "make erd", "make erd-snapshot", "make pr-body-check", "expand"):
        assert phrase in section, f"the Migration PRs section does not mention {phrase!r}"


def test_review_command_checks_the_schema_section():
    text = _read(REVIEW_COMMAND)
    assert "How it is added" in text and "erd.md" in text


def test_new_feature_points_at_the_schema_section():
    assert "Schema changes" in _read(NEW_FEATURE)
