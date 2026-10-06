"""Progress on the terminal for the upload's long steps: preparing, sending and writing cells.

Shown on stderr, beside the summary, only when stderr is a terminal: a log or CI run gets the
result lines and no redraws. Each bar is removed when its step ends, leaving the line that
says what happened.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator

# Each step's unit: bytes are shown in MB, cells as a count.
BYTES = "bytes"
CELLS = "cells"


@contextmanager
def track(description: str, unit: str = BYTES) -> Iterator[Callable[[int, int], None]]:
    """Yield ``update(done, total)`` for one step."""
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        MofNCompleteColumn,
        Progress,
        TaskProgressColumn,
        TextColumn,
        TimeRemainingColumn,
    )

    console = Console(stderr=True)
    if not console.is_terminal:
        yield lambda _done, _total: None
        return
    amount = DownloadColumn() if unit == BYTES else MofNCompleteColumn()
    with Progress(TextColumn("{task.description}"), BarColumn(), amount, TaskProgressColumn(),
                  TimeRemainingColumn(), console=console, transient=True) as progress:
        task = progress.add_task(description, total=None)

        def update(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total)

        yield update
