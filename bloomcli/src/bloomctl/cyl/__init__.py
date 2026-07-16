"""`bloomctl cyl` command group — cylinder-scan commands, one file per command."""

from __future__ import annotations

import click

# Alias so the command objects don't shadow the same-named submodules.
from .download import download as download_cmd
from .ingest import ingest_result as ingest_result_cmd


@click.group(name="cyl")
def cyl() -> None:
    """Cylinder-scan commands."""


cyl.add_command(download_cmd)
cyl.add_command(ingest_result_cmd)
