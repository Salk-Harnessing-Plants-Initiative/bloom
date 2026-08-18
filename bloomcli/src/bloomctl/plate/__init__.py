"""`bloomctl plate` command group — plate (GraviScan) commands.

One file per entity (grouped by entity; verbs live inside each entity):
  - download.py             `plate download`                  pull an experiment/scan: plates.csv + images

Add a command: new file here, register it below.
"""

from __future__ import annotations

import click

# Alias so the command objects don't shadow the same-named submodules.
from .download import download as download_cmd


@click.group(name="plate")
def plate() -> None:
    """Plate (GraviScan) commands."""


plate.add_command(download_cmd)
