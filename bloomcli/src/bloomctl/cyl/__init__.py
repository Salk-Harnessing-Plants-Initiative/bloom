"""`bloomctl cyl` command group — cylinder-scan commands, one file per command."""

from __future__ import annotations

import click

# Alias the command objects so they don't shadow the submodules of the same
# name on the package (`bloomctl.cyl.download` must stay the module).
from .download import download as download_cmd
from .ingest import ingest_result as ingest_result_cmd


@click.group(name="cyl")
def cyl() -> None:
    """Cylinder-scan commands."""


cyl.add_command(download_cmd)
cyl.add_command(ingest_result_cmd)
