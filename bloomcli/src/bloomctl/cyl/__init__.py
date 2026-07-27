"""`bloomctl cyl` command group — cylinder-scan commands.

One file per entity (grouped by entity; verbs live inside each entity):
  - download.py             `cyl download`                    pull an experiment/scan: scans.csv + images
  - download_for_predict.py `cyl download-for-predict`        stage one scan in predict's layout
                            `cyl batch-download-for-predict`  stage a batch of scans (A4)
  - ingest.py               `cyl ingest-result`                write a per-scan ResultEnvelope back via RPC
                            `cyl batch-ingest-result`          write back a batch of envelopes (A4)
  - datasets.py             `cyl datasets`                     list/get/create cylinder trait datasets
  - experiments.py          `cyl experiments`                  list cylinder experiments
  - _batch.py               shared ScanResult/BatchResult reporting for the batch-* commands (no CLI of its own)

Add a command: new file here, register it below.
"""

from __future__ import annotations

import click

# Alias so the command objects don't shadow the same-named submodules.
from .datasets import datasets as datasets_cmd
from .download import download as download_cmd
from .download_for_predict import batch_download_for_predict as batch_download_for_predict_cmd
from .download_for_predict import download_for_predict as download_for_predict_cmd
from .experiments import experiments as experiments_cmd
from .ingest import batch_ingest_result as batch_ingest_result_cmd
from .ingest import ingest_result as ingest_result_cmd


@click.group(name="cyl")
def cyl() -> None:
    """Cylinder-scan commands."""


cyl.add_command(download_cmd)
cyl.add_command(download_for_predict_cmd)
cyl.add_command(batch_download_for_predict_cmd)
cyl.add_command(ingest_result_cmd)
cyl.add_command(batch_ingest_result_cmd)
cyl.add_command(datasets_cmd)
cyl.add_command(experiments_cmd)
