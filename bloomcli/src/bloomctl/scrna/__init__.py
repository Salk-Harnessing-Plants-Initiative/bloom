"""`bloomctl scrna` command group — single-cell dataset commands.

A dataset's whole AnnData file (`.h5ad`) is kept in the `scrna` bucket's `h5ad/` folder,
gzipped as it is and named by the SHA-256 of the uncompressed file. The bucket holds other
kinds of object besides — per-gene counts, above all — so the file commands are grouped
under the form they act on:

  hdf5 upload    — a writer puts a file there, after checking its structure
  hdf5 download  — anyone signed in gets a dataset's file back, checked against its fingerprint
  hdf5 list      — what is stored, with each object's size and the dataset it belongs to
"""

from __future__ import annotations

import click

from .download import download as download_cmd
from .list_files import list_files as list_cmd
from .upload import upload as upload_cmd


@click.group(name="scrna")
def scrna() -> None:
    """Single-cell dataset commands."""


@click.group(name="hdf5")
def hdf5() -> None:
    """A dataset's whole AnnData file (.h5ad), as stored in the scrna bucket."""


hdf5.add_command(upload_cmd)
hdf5.add_command(download_cmd)
hdf5.add_command(list_cmd)
scrna.add_command(hdf5)
