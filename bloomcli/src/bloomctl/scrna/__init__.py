"""`bloomctl scrna` command group — single-cell dataset commands.

A dataset's whole AnnData file (`.h5ad`) is kept in the `scrna` bucket's `h5ad/` folder,
gzipped as it is and named by the SHA-256 of the uncompressed file:

  upload    — a writer puts a file there, after checking its structure
  download  — anyone signed in gets a dataset's file back, checked against its fingerprint
"""

from __future__ import annotations

import click

from .download import download as download_cmd
from .upload import upload as upload_cmd


@click.group(name="scrna")
def scrna() -> None:
    """Single-cell dataset commands."""


scrna.add_command(upload_cmd)
scrna.add_command(download_cmd)
