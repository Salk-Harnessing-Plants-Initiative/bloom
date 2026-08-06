"""bloomctl command group (entry point ``bloomctl = bloomctl.cli:cli``)."""

from __future__ import annotations

import click

from . import __version__
from .auth import DEFAULT_SERVER
from .credentials import DEFAULT_PROFILE


@click.group()
@click.version_option(version=__version__, prog_name="bloomctl")
def cli() -> None:
    """Bloom command-line tool"""


def _load_creds(profile: str):
    """Load a profile's stored credentials, or fail with a login hint."""
    from .credentials import load_credentials

    try:
        return load_credentials(profile)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(f"{exc} — run `bloomctl login`.") from exc


def _authed_client(profile: str):
    """Load a profile's credentials and return a signed-in Supabase client."""
    from . import auth

    try:
        return auth.make_authed_client(_load_creds(profile))
    except auth.AuthError as exc:
        raise click.ClickException(str(exc)) from exc


def _authed_storage_session(profile: str):
    """A signed-in client that re-authenticates itself during long storage runs."""
    from . import auth
    from .cyl._storage import StorageSession

    creds = _load_creds(profile)
    try:
        client = auth.make_authed_client(creds)
    except auth.AuthError as exc:
        raise click.ClickException(str(exc)) from exc
    return StorageSession(client, creds)


@cli.command()
@click.option(
    "--server",
    default=DEFAULT_SERVER,
    show_default=True,
    help="Which Bloom server to log in to — prod by default; pass a staging/local URL to switch.",
)
@click.option(
    "--api-url",
    default=None,
    help="Supabase API URL (e.g. https://bloom.salk.edu/api). Pair with --anon-key to skip the /client-info fetch.",
)
@click.option(
    "--anon-key",
    default=None,
    help="Public anon key — pair with --api-url to supply credentials manually when /client-info can't be fetched (interim / local / offline).",
)
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to write (credentials.txt for prod).",
)
@click.option("--email", default=None, help="Login email (prompted if omitted).")
@click.option("--password", default=None, help="Login password (prompted if omitted).")
def login(
    server: str,
    api_url: str | None,
    anon_key: str | None,
    profile: str,
    email: str | None,
    password: str | None,
) -> None:
    """Log in to Bloom and save credentials to ~/.bloom/credentials.txt."""
    from . import auth
    from .credentials import Credentials, save_credentials

    if not email:
        email = click.prompt("Email")
    if not password:
        password = click.prompt("Password", hide_input=True)

    # api_url + anon_key come from --api-url/--anon-key, else from /client-info.
    if api_url and anon_key:
        resolved_api_url, resolved_anon_key = api_url, anon_key
    else:
        try:
            resolved_api_url, resolved_anon_key = auth.fetch_anon_credentials(server)
        except auth.AuthError as exc:
            raise click.ClickException(str(exc)) from exc

    try:
        auth.verify_credentials(resolved_api_url, resolved_anon_key, email, password)
    except auth.AuthError as exc:
        raise click.ClickException(str(exc)) from exc

    path = save_credentials(
        Credentials(
            api_url=resolved_api_url,
            anon_key=resolved_anon_key,
            email=email,
            password=password,
        ),
        profile=profile,
    )
    click.echo(f"Logged in as {email}. Credentials saved to {path}.")


# Data-type command groups, one file per command (see bloomctl/cyl).
from .cyl import cyl  # noqa: E402  (registered after `cli` is defined)

cli.add_command(cyl)
