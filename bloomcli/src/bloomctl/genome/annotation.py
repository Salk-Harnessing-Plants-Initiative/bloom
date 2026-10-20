"""`bloomctl genome annotation upload|download`: a genome version's GO and pathway annotation.

upload records a bloom-annotation/1 file (made by the GO and pathway workflow) as its genome
version's next build of its source. download saves a version's ready build, checked against
Bloom's record; it exits 4 while a build is still uploading and 3 when there is none, so a
pipeline step can wait for one or build its own.
"""

from __future__ import annotations

from pathlib import Path

import click

from .. import go_pathways
from ..credentials import DEFAULT_PROFILE
from ..scrna import _session


@click.group(name="annotation")
def annotation() -> None:
    """A genome version's GO and pathway annotation."""


@annotation.command(name="upload")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-p", "--profile", default=DEFAULT_PROFILE, show_default=True,
              help="Credentials profile to use.")
def upload(file: Path, profile: str) -> None:
    """Record FILE as its genome version's next annotation build.

    FILE is a bloom-annotation/1 file; it names its genome version and source. Prints the
    build's number and id. Exits 3 when Bloom refuses it (the build is abandoned), 1 for a
    network error.
    """
    build = go_pathways.upload_annotation(_session.connect(profile).client, file)
    click.echo(f"annotation build {build['build']} (id {build['build_id']}): {build['file_path']}")


@annotation.command(name="download")
@click.argument("genome")
@click.option("--source", type=click.Choice(go_pathways.SOURCES), default="eggnog",
              show_default=True)
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), required=True,
              help="Where to save the annotation file.")
@click.option("--id-file", type=click.Path(dir_okay=False, path_type=Path),
              help="Also write the build's id to this file.")
@click.option("-p", "--profile", default=DEFAULT_PROFILE, show_default=True,
              help="Credentials profile to use.")
def download(genome: str, source: str, out: Path, id_file: Path | None, profile: str) -> None:
    """Save GENOME's ready annotation build of SOURCE, checked against Bloom's record.

    GENOME is a genome version, NAME.vN. Exits 4 while a build is still uploading, 3 when there
    is none or the file doesn't match its record, 1 for a network error.
    """
    client = _session.connect(profile).client
    build = go_pathways.find_build(client, go_pathways.version_id(client, genome), source)
    go_pathways.download_build(client, build, out)
    if id_file is not None:
        id_file.write_text(f"{build['id']}\n", encoding="utf-8")
    click.echo(f"{genome}/{source}-b{build['build']} (id {build['id']})")
