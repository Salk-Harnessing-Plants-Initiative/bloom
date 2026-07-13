"""`bloomctl cyl` command group — cylinder-scan commands.

One file per entity (grouped by entity; verbs live inside each entity):
  - download.py  `cyl download`       pull an experiment/scan: scans.csv + images
  - ingest.py    `cyl ingest-result`  write a per-scan ResultEnvelope back via RPC (new; no legacy equivalent)
  - datasets.py  `cyl datasets`       list/create cylinder trait datasets

Add a command: new file here, register it below.
"""

from __future__ import annotations

import click

# Alias so the command objects don't shadow the same-named submodules.
from .datasets import datasets as datasets_cmd
from .download import download as download_cmd
from .ingest import ingest_result as ingest_result_cmd


@click.group(name="cyl")
def cyl() -> None:
    """Cylinder-scan commands."""


cyl.add_command(download_cmd)
cyl.add_command(ingest_result_cmd)
cyl.add_command(datasets_cmd)
