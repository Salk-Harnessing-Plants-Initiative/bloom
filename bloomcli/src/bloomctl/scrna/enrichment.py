"""`bloomctl scrna enrichment upload`: record a dataset's GO and pathway enrichment.

The file is bloom-enrichment/1 (made by the GO and pathway workflow); it names its dataset and
differential expression analysis. A dataset not yet linked to a genome version is first linked
to the annotation build's.
"""

from __future__ import annotations

from pathlib import Path

import click

from .. import go_pathways
from ..credentials import DEFAULT_PROFILE
from . import _session


@click.group(name="enrichment")
def enrichment() -> None:
    """A dataset's GO and pathway enrichment."""


@enrichment.command(name="upload")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--build-id", type=int, required=True, help="The annotation build it was tested against.")
@click.option("--run-id", type=int, help="The pipeline job that made it, when there is one.")
@click.option("-p", "--profile", default=DEFAULT_PROFILE, show_default=True,
              help="Credentials profile to use.")
def upload(file: Path, build_id: int, run_id: int | None, profile: str) -> None:
    """Record FILE as an enrichment run of its dataset's analysis against --build-id.

    Prints the run's id. Exits 3 when Bloom refuses it (the run is abandoned), 1 for a network
    error.
    """
    run = go_pathways.upload_enrichment(_session.connect(profile).client, file,
                                        build_id=build_id, rnaseq_run_id=run_id)
    click.echo(f"enrichment run {run['run_id']}: {run['file_path']}")
