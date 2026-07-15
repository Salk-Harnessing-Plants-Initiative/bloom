"""`bloomctl cyl` command group — cylinder-scan commands.

One file per entity (grouped by entity; verbs live inside each entity):
  - download.py  `cyl download`       pull an experiment/scan: scans.csv + images
  - ingest.py    `cyl ingest-result`  write a per-scan ResultEnvelope back via RPC (new; no legacy equivalent)
  - datasets.py     `cyl datasets`      list/get/create cylinder trait datasets
  - experiments.py  `cyl experiments`   list cylinder experiments
  - accessions.py   `cyl accessions`    list accessions per experiment; sample counts

Add a command: new file here, register it below.
"""

from __future__ import annotations

import click

# Alias so the command objects don't shadow the same-named submodules.
from .accessions import accessions as accessions_cmd
from .datasets import datasets as datasets_cmd
from .download import download as download_cmd
from .download_for_predict import download_for_predict as download_for_predict_cmd
from .experiments import experiments as experiments_cmd
from .ingest import ingest_result as ingest_result_cmd


@click.group(name="cyl")
def cyl() -> None:
    """Cylinder-scan commands."""


cyl.add_command(download_cmd)
cyl.add_command(download_for_predict_cmd)
cyl.add_command(ingest_result_cmd)
cyl.add_command(datasets_cmd)
cyl.add_command(experiments_cmd)
cyl.add_command(accessions_cmd)
