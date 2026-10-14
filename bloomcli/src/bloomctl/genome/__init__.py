"""`bloomctl genome` command group — reference genomes for the Cell Ranger workflow.

A genome has numbered versions; each is a gzipped FASTA and GTF in the genome-references
bucket, and a version's files never change once it is uploaded.

  upload    — a writer stores a FASTA and GTF as a genome's next version
  list      — every genome, its species and its versions, with their status
  download  — a version's FASTA and GTF, checked against Bloom's record
"""

from __future__ import annotations

import click

from .download import download as download_cmd
from .list import list_genomes as list_cmd
from .upload import upload as upload_cmd


@click.group(name="genome")
def genome() -> None:
    """Reference genome commands."""


genome.add_command(upload_cmd)
genome.add_command(list_cmd)
genome.add_command(download_cmd)
