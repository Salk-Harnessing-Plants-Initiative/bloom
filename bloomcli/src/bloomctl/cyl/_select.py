"""Interactive menu selection shared by the cyl commands.

A single numbered-menu prompt so every command selects an entity the same way. The menu and
prompt are written to stderr, so a command's machine output (`--output json/csv`) on stdout stays
clean even while the menu is shown.
"""

from __future__ import annotations

from typing import Any

import click


def select_from_menu(
    items: list[tuple[Any, str]],
    *,
    title: str,
    prompt_label: str,
    all_label: str | None = None,
) -> Any:
    """Show a numbered menu and return the chosen item's value (`items[i][0]`).

    Each item is a ``(value, label)`` pair. Prints ``Select {title}:`` and the numbered labels to
    stderr. When ``all_label`` is given, entry ``0`` is offered and selecting it returns ``None``
    ("all" / no filter); otherwise the menu runs ``1..N`` and always returns a concrete value.
    ``click.prompt`` validates the number against the range and re-prompts on a bad entry; with no
    input to read (non-interactive) it aborts rather than returning a guessed choice.
    """
    click.echo(f"Select {title}:", err=True)
    low = 1
    if all_label is not None:
        click.echo(f"  0) {all_label}", err=True)
        low = 0
    for i, (_value, label) in enumerate(items, start=1):
        click.echo(f"  {i}) {label}", err=True)
    choice = click.prompt(prompt_label, type=click.IntRange(low, len(items)), err=True)
    if all_label is not None and choice == 0:
        return None
    return items[choice - 1][0]
