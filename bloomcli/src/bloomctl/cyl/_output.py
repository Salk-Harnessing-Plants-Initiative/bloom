"""Shared output helpers for `cyl` commands (table rendering)."""

from __future__ import annotations

from typing import Sequence

import click


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
