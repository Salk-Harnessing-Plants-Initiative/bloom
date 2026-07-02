"""bloomctl command group (entry point ``bloomctl = bloomctl.cli:cli``)."""

from __future__ import annotations

from pathlib import Path

import click

from . import __version__
from .auth import DEFAULT_SERVER
from .credentials import DEFAULT_PROFILE


@click.group()
@click.version_option(version=__version__, prog_name="bloomctl")
def cli() -> None:
    """Bloom command-line tool"""


def _authed_client(profile: str):
    """Load a profile's credentials and return a signed-in Supabase client."""
    from . import auth
    from .credentials import load_credentials

    try:
        creds = load_credentials(profile)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(f"{exc} — run `bloomctl login`.") from exc
    try:
        return auth.make_authed_client(creds)
    except auth.AuthError as exc:
        raise click.ClickException(str(exc)) from exc


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


@cli.command()
@click.argument("out_dir", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--experiment-id",
    "--experiment_id",
    "experiment_id",
    type=int,
    default=None,
    help="Download a whole experiment by ID (mutually exclusive with --scan-id).",
)
@click.option(
    "--scan-id",
    "--scan_id",
    "scan_id",
    type=int,
    default=None,
    help="Download a single scan by ID (mutually exclusive with --experiment-id).",
)
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
@click.option(
    "--meta-only",
    "--meta_only",
    "meta_only",
    is_flag=True,
    help="Write scans.csv only; skip image download.",
)
@click.option(
    "--plant-qr-code",
    "--plant_qr_code",
    "plant_qr_code",
    default=None,
    help="Restrict to a single plant QR code.",
)
@click.option(
    "--plant-age-min",
    "--plant_age_min",
    "plant_age_min",
    type=int,
    default=0,
    show_default=True,
    help="Minimum plant age in days.",
)
@click.option(
    "--plant-age-max",
    "--plant_age_max",
    "plant_age_max",
    type=int,
    default=1000,
    show_default=True,
    help="Maximum plant age in days.",
)
@click.option(
    "--limit",
    type=int,
    default=100000,
    show_default=True,
    help="Maximum number of scans to fetch.",
)
def download(
    out_dir: Path,
    experiment_id: int | None,
    scan_id: int | None,
    profile: str,
    meta_only: bool,
    plant_qr_code: str | None,
    plant_age_min: int,
    plant_age_max: int,
    limit: int,
) -> None:
    """Download a cylinder experiment (--experiment-id) or a single scan (--scan-id):
    metadata (scans.csv) and per-frame images."""
    from . import download as dl

    # Exactly one of --experiment-id / --scan-id.
    if (experiment_id is None) == (scan_id is None):
        raise click.UsageError("Pass exactly one of --experiment-id or --scan-id.")

    client = _authed_client(profile)

    if scan_id is not None:
        scan = dl.fetch_scan(client, scan_id)
        if scan is None:
            raise click.ClickException(f"Scan {scan_id} not found.")
        scans = [scan]
    else:
        scans = dl.fetch_scans(
            client,
            experiment_id,
            plant_qr_code=plant_qr_code,
            plant_age_min=plant_age_min,
            plant_age_max=plant_age_max,
            limit=limit,
        )
    genotypes = dl.fetch_genotypes(client, [s.get("accession_id") for s in scans])
    rows = [dl.build_scan_row(s, genotypes.get(s.get("accession_id"))) for s in scans]

    out = Path(out_dir)
    csv_path = out / "scans.csv"
    dl.write_scans_csv(rows, csv_path)
    click.echo(f"Wrote {len(rows)} scans -> {csv_path}")

    if meta_only:
        return

    result = dl.download_images(client, scans, out)
    log_path = out / "download_log.txt"
    dl.write_download_log(result, log_path)
    click.echo(
        f"Downloaded {result.ok}/{result.total} image frames -> {out / 'images'}  (log: {log_path})"
    )
    if result.failed:
        # Partial download: surface it and exit non-zero so a pipeline knows the
        # output is incomplete (the log lists every failed frame).
        raise click.ClickException(
            f"{result.failed} of {result.total} frames failed to download — see {log_path}"
        )


@cli.group(name="list")
def list_() -> None:
    """List experiments or scans you can access."""


def _print_table(title: str, columns: list[str], rows: list[list[str]], empty: str) -> None:
    """Render a rich table (or an 'empty' message when there are no rows)."""
    if not rows:
        click.echo(empty)
        return
    from rich.console import Console
    from rich.table import Table

    table = Table(title=title)
    for col in columns:
        table.add_column(col, overflow="fold")
    for row in rows:
        table.add_row(*row)
    Console().print(table)


@list_.command(name="experiments")
@click.option(
    "-p", "--profile", default=DEFAULT_PROFILE, show_default=True,
    help="Credentials profile to use.",
)
def list_experiments(profile: str) -> None:
    """List cylinder experiments (id, name) you can access."""
    from . import download as dl

    client = _authed_client(profile)
    experiments = dl.fetch_experiments(client)
    _print_table(
        "Experiments",
        ["ID", "Name", "Created"],
        [[str(e.get("id", "")), e.get("name") or "", str(e.get("created_at") or "")[:10]] for e in experiments],
        empty="No experiments found.",
    )


@list_.command(name="scans")
@click.option(
    "--experiment-id", "--experiment_id", "experiment_id", type=int, required=True,
    help="Experiment to list scans for.",
)
@click.option(
    "-p", "--profile", default=DEFAULT_PROFILE, show_default=True,
    help="Credentials profile to use.",
)
def list_scans(experiment_id: int, profile: str) -> None:
    """List scans in an experiment (scan id, plant, wave, date)."""
    from . import download as dl

    client = _authed_client(profile)
    scans = dl.fetch_scans(client, experiment_id)
    _print_table(
        f"Scans in experiment {experiment_id}",
        ["Scan ID", "Plant QR", "Wave", "Age (d)", "Date"],
        [
            [
                str(s.get("scan_id", "")),
                s.get("qr_code") or "",
                str(s.get("wave_number") or ""),
                str(s.get("plant_age_days") or ""),
                str(s.get("date_scanned") or ""),
            ]
            for s in scans
        ],
        empty=f"No scans found for experiment {experiment_id}.",
    )
