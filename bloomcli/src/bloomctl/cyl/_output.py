"""Shared output helpers for `cyl` commands (table and machine-readable rendering)."""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Mapping, Sequence

import click

# Machine-readable output formats for `--output` on list commands. Both are stdlib —
# rendering must not add a runtime dependency.
MACHINE_FORMATS = ("csv", "json")


def render(records: Sequence[Mapping[str, Any]], fieldnames: Sequence[str], fmt: str) -> str:
    """Render records as a `fmt` document, fields in `fieldnames` order.

    Empty input yields a well-formed empty document — a header-only CSV, or `[]` for JSON —
    never a human message, so output stays pipeable. Uses the stdlib csv writer so values are
    quoted/escaped correctly (commas, newlines, quotes); a ``None`` renders as an empty cell.
    """
    if fmt == "json":
        return json.dumps([{k: r.get(k) for k in fieldnames} for r in records])
    if fmt == "csv":
        buf = io.StringIO()
        # extrasaction="ignore": callers may pass richer records than the declared field
        # set; the declared set is the contract.
        writer = csv.DictWriter(
            buf, fieldnames=list(fieldnames), extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        for record in records:
            writer.writerow({k: record.get(k) for k in fieldnames})
        return buf.getvalue().rstrip("\n")
    raise ValueError(f"unsupported output format: {fmt!r} (expected one of {MACHINE_FORMATS})")


def print_table(
    title: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    empty: str,
) -> None:
    """Render rows as a rich table, or print `empty` when there are no rows."""
    if not rows:
        click.echo(empty)
        return
    from rich.console import Console
    from rich.table import Table

    table = Table(title=title)
    for col in columns:
        table.add_column(col, overflow="fold")
    for row in rows:
        table.add_row(*row)
    Console().print(table)
