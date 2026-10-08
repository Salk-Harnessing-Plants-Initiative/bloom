"""`bloomctl genome` command group — reference genomes for the Cell Ranger workflow.

A genome has numbered versions; each is a gzipped FASTA and GTF in the genome-references
bucket, and a version's files never change once it is uploaded.

  upload  — a writer stores a FASTA and GTF as a genome's next version
"""

from __future__ import annotations

import click

from .upload import upload as upload_cmd


@click.group(name="genome")
def genome() -> None:
    """Reference genome commands."""


genome.add_command(upload_cmd)
