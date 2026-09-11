"""Check that a migration PR's body documents its schema change.

The body needs a mermaid erDiagram naming every table and view the migration changes create,
alter or rename to; a constraints table (header with `Name` and `How it is added`) naming each
constraint and index they add; and, under its Schema changes heading, the name of every table,
view and index they drop. Indexes are not drawn, so a change that creates or alters no table
or view needs no diagram. `No schema changes.` on its own line passes only when that is true.

Usage:
  lint_migration_pr_body.py BASE [--head HEAD] [--staging-ref REF] [--repo DIR] [--body-file PATH]
  Without --body-file the body is read from the PR_BODY environment variable.

Exit codes:
  0  no migration change, or the body documents it
  1  the body does not document it
  2  a git ref cannot be resolved
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from migration_changes import RefError, changed_facts, migration_changes  # noqa: E402
from migration_sql import MigrationFacts  # noqa: E402

OPT_OUT = "No schema changes."
TITLE = "::error title=PR body schema section::"
SNAPSHOT = "make erd-snapshot CHANGED=origin/staging"

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_MERMAID = re.compile(r"^```mermaid[ \t]*\n(.*?)^```", re.M | re.S)
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$", re.M)
_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")


def _normalise(body: str | None) -> str:
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    return _COMMENT.sub("", text)


def _has_token(name: str, text: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text) is not None


def _schema_section(text: str) -> str:
    """The body under its `Schema changes` heading, up to the next heading at that level or above."""
    headings = list(_HEADING.finditer(text))
    for i, heading in enumerate(headings):
        if heading.group(2).strip().lower() == "schema changes":
            level = len(heading.group(1))
            end = next((h.start() for h in headings[i + 1 :] if len(h.group(1)) <= level), len(text))
            return text[heading.end() : end]
    return ""


def _cells(line: str) -> list[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]


def _constraints_table(text: str) -> list[str] | None:
    """Name-column cells of the first table whose header has `Name` and `How it is added`."""
    lines = text.split("\n")
    for i in range(len(lines) - 1):
        if "|" not in lines[i] or "|" not in lines[i + 1]:
            continue
        separator = _cells(lines[i + 1])
        if not all(_SEPARATOR_CELL.match(cell) for cell in separator):
            continue
        header = [cell.lower() for cell in _cells(lines[i])]
        if "name" not in header or "how it is added" not in header:
            continue
        column = header.index("name")
        names = []
        for row in lines[i + 2 :]:
            if "|" not in row:
                break
            cells = _cells(row)
            if column < len(cells):
                names.append(cells[column].replace("`", ""))
        return names
    return None


def _summary(facts: MigrationFacts) -> str:
    parts = []
    for verb, names in (
        ("create", facts.tables_created),
        ("alter", facts.tables_altered),
        ("drop", facts.tables_dropped),
        ("rename", {old for old, _ in facts.tables_renamed}),
        ("add", facts.constraints_added | facts.indexes_added),
        ("drop constraint or index", facts.constraints_dropped | facts.indexes_dropped),
        ("create view", facts.views_created),
        ("alter view", facts.views_altered),
        ("drop view", facts.views_dropped),
        ("rename view", {old for old, _ in facts.views_renamed}),
    ):
        if names:
            parts.append(f"{verb} {', '.join(sorted(names))}")
    return "; ".join(parts)


def check_body(body: str | None, facts: MigrationFacts) -> list[str]:
    """Problems with the body's schema section; empty when it documents the change."""
    text = _normalise(body)
    if not text.strip():
        return [
            f"The PR body is empty. Add a Schema changes section: the output of `{SNAPSHOT}` "
            f"and a constraints table, or the line `{OPT_OUT}`."
        ]

    problems = [
        f"An unnamed constraint is added to {table}. Name every constraint so it can be listed."
        for table in facts.unnamed_constraints
    ]
    dropped = sorted(facts.tables_dropped | facts.views_dropped | facts.indexes_dropped)

    if any(line.strip() == OPT_OUT for line in text.split("\n")):
        if facts.changes_schema:
            problem = f"`{OPT_OUT}` is not true: the migrations {_summary(facts)}."
            if dropped:
                problem += " Remove that line and name what they drop under Schema changes."
            problems.append(problem)
        return problems

    # Indexes are not drawn, so a change that creates or alters no table or view needs no diagram.
    if facts.tables_touched or not facts.changes_schema:
        diagrams = [m.group(1) for m in _MERMAID.finditer(text) if "erDiagram" in m.group(1)]
        if not diagrams:
            problems.append(
                f"No mermaid erDiagram block. Paste the output of `{SNAPSHOT}` under Schema changes, "
                f"or add `{OPT_OUT}` if the migrations change no table, view, constraint or index."
            )
        else:
            drawn = "\n".join(diagrams)
            missing = sorted(t for t in facts.tables_touched if not _has_token(t, drawn))
            if missing:
                problems.append(f"The erDiagram does not show: {', '.join(missing)}.")

    section = _schema_section(text)
    unnamed = [name for name in dropped if not _has_token(name, section)]
    if unnamed:
        problems.append(
            "Name every table, view and index the migrations drop under the Schema changes heading. "
            f"Not named there: {', '.join(unnamed)}."
        )

    required = sorted(facts.constraints_added | facts.indexes_added)
    if required:
        listed = _constraints_table(text)
        if listed is None:
            problems.append(
                "No constraints table. Add a table whose header has `Name` and `How it is added`, "
                f"with one row for each of: {', '.join(required)}."
            )
        else:
            cells = "\n".join(listed)
            missing = [name for name in required if not _has_token(name, cells)]
            if missing:
                problems.append(f"The constraints table does not list: {', '.join(missing)}.")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base", help="the PR's base commit")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--staging-ref", default="origin/staging")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--body-file", help="read the body from this file instead of PR_BODY")
    args = parser.parse_args(argv)
    repo = Path(args.repo)

    try:
        if not migration_changes(args.base, args.head, args.staging_ref, repo):
            print("PR body check passed (no migration change).")
            return 0
        facts = changed_facts(args.base, args.head, args.staging_ref, repo)
    except RefError as exc:
        print(f"::error title=lint_migration_pr_body: cannot resolve ref::{exc}")
        return 2

    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    else:
        body = os.environ.get("PR_BODY")
    problems = check_body(body, facts)
    if problems:
        for problem in problems:
            print(f"{TITLE}{problem}")
        return 1
    print("PR body check passed (schema section complete).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
