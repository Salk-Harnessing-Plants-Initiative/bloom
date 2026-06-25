"""bloomcli command group (entry point ``bloomcli = bloomcli.cli:cli``)."""

from __future__ import annotations

import click

from . import __version__


@click.group()
@click.version_option(version=__version__, prog_name="bloomcli")
def cli() -> None:
    """Bloom command-line tool: download cylinder experiments and manage credentials."""


@cli.command()
def login() -> None:
    """Log in to Bloom and save credentials to ~/.bloom/credentials.txt."""
    # Options + implementation land in a later task of #347 (login phase).
    raise click.ClickException("`bloomcli login` is not implemented yet (issue #347).")


@cli.command()
def download() -> None:
    """Download a cylinder experiment's metadata (scans.csv) and per-frame images."""
    # Options + implementation land in a later task of #347 (download phase).
    raise click.ClickException("`bloomcli download` is not implemented yet (issue #347).")
