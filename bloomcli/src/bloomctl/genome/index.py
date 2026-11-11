"""`bloomctl genome index`: build a genome version's files for the genome browser.

The version's stored FASTA and GTF are downloaded and checked against Bloom's record, turned into
the five indexed files the browser reads (see _index), and every gene's position is read from the
GTF. Only when all of that works is a build started; the files are then uploaded, the genes sent
in batches, and the build finished. A failure after the start abandons the build.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import click
import httpx

from ..credentials import DEFAULT_PROFILE
from ..scrna import _session, _transfer
from ..scrna._text import visible
from . import _files, _index
from .download import NAMED, Unavailable, _fetch, _version
from .upload import _send

CONTENT_TYPES = {
    "fasta": "application/gzip",
    "fasta_fai": "text/plain",
    "fasta_gzi": "application/octet-stream",
    "gtf": "application/gzip",
    "gtf_tbi": "application/octet-stream",
}
# A batch of genes is sent again this many times when its answer is lost; sending again is safe.
GENE_SEND_TRIES = 3
EXIT_IN_PROGRESS = 4


class InProgress(click.ClickException):
    """Another build of this version is still uploading; it may be ready later."""

    exit_code = EXIT_IN_PROGRESS


@click.command(name="index")
@click.argument("genome")
@click.option("--workdir", type=click.Path(file_okay=False, path_type=Path),
              help="Where to build the files (default: a temporary folder).")
@click.option("-p", "--profile", default=DEFAULT_PROFILE, show_default=True,
              help="Credentials profile to use.")
def index(genome: str, workdir: Path | None, profile: str) -> None:
    """Build GENOME's files for the genome browser and record them in Bloom.

    GENOME is a ready genome version, NAME.vN. Prints the build's number, id, folder and gene
    count. Leaves a version that already has a ready build alone. Exits 3 when the version
    can't be built (not ready, a file that doesn't match Bloom's record, or a GTF the browser
    can't use) or Bloom refuses the build, 4 while another build of it is uploading, and 1 for
    anything else, such as a network error.
    """
    named = NAMED.fullmatch(genome)
    if (named is None or named["version"] is None
            or not _files.NAME_RULE.match(named["name"])):
        raise click.UsageError(f"GENOME must be a genome version, NAME.vN ({_files.NAME_HELP}).")
    _index.require_htslib()
    name = named["name"]
    conn = _session.connect(profile)
    version = _version(conn.client, name, named["version"])
    exact = f"{name}.v{version['version']}"
    if _settled(conn.client, version, exact):
        return
    if workdir is not None:
        workdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bloomctl-genome-index-", dir=workdir) as tmp:
        tmp_path = Path(tmp)
        sources, out = tmp_path / "sources", tmp_path / "browser"
        sources.mkdir()
        out.mkdir()
        with _transfer.open_client() as http:
            for kind, plain in (("fasta", "genome.fa"), ("gtf", "genes.gtf")):
                conn = _fetch(http, conn, profile, exact, version, kind, plain, sources, False)
            try:
                built = _index.build(sources / "genome.fa.gz", sources / "genes.gtf.gz", out,
                                     tmp_path / "work")
            except _index.IndexProblem as exc:
                raise Unavailable(f"{exact} can't be built for the browser: "
                                  f"{visible(str(exc))}. Nothing was started.") from exc
            started = _start(conn.client, version["id"], exact)
            try:
                conn = _upload(http, conn, profile, started, built)
                _add_genes(conn.client, started["build_id"], built.genes)
                _finish(conn.client, started["build_id"], built)
            except BaseException:
                if _abandon(conn.client, started, exact) != "ready":
                    raise
    click.echo(f"{exact} browser build b{started['build']} (id {started['build_id']}): "
               f"{started['folder']}, {len(built.genes)} genes")


def _settled(client: Any, version: dict[str, Any], exact: str) -> bool:
    """True when the version already has a ready build; InProgress while one uploads."""
    from .._postgrest import queried

    builds = queried(
        "the version's browser builds",
        lambda: client.table("genome_browser_builds").select("id, build, status, folder")
        .eq("version_id", version["id"]).execute().data,
    ) or []
    for build in builds:
        if build["status"] == "ready":
            click.echo(f"{exact} already has browser build b{build['build']} "
                       f"(id {build['id']}); nothing to do.", err=True)
            return True
        if build["status"] == "uploading":
            raise InProgress(f"{exact}'s browser build b{build['build']} is still uploading.")
    return False


def _rpc(client: Any, what: str, function: str, args: dict[str, Any]) -> Any:
    from postgrest import APIError

    from ..errors import explain

    try:
        return client.rpc(function, args).execute().data
    except APIError as exc:
        raise Unavailable(f"Bloom refused to {what}: {explain(exc)}") from exc


def _start(client: Any, version_id: int, exact: str) -> dict[str, Any]:
    rows = _rpc(client, f"start a browser build of {exact}", "start_genome_browser_build",
                {"p_version_id": version_id, "p_tool_versions": _index.tool_versions()})
    rows = rows if isinstance(rows, list) else [rows]
    if not rows:
        raise click.ClickException("Bloom did not return the new build. Nothing was sent.")
    return rows[0]


def _upload(http, conn, profile: str, started: dict[str, Any], built: _index.Built):
    for role, name in _index.ROLES.items():
        conn = _send(http, conn, profile, f"{started['folder']}/{name}", built.files[role],
                     name, CONTENT_TYPES[role])
    return conn


def _add_genes(client: Any, build_id: int, genes: list[dict[str, Any]]) -> None:
    for batch in _index.batches(genes):
        for attempt in range(1, GENE_SEND_TRIES + 1):
            try:
                _rpc(client, "add genes", "add_genome_browser_genes",
                     {"p_build_id": build_id, "p_genes": batch})
                break
            except httpx.HTTPError:
                if attempt == GENE_SEND_TRIES:
                    raise


def _finish(client: Any, build_id: int, built: _index.Built) -> None:
    files = {role: {"sha256": f.sha256, "bytes": f.size} for role, f in built.files.items()}
    _rpc(client, "finish the browser build", "finish_genome_browser_build", {
        "p_build_id": build_id, "p_files": files, "p_gtf_was_sorted": built.gtf_was_sorted,
        "p_gene_count": len(built.genes),
    })


def _abandon(client: Any, started: dict[str, Any], exact: str) -> str | None:
    """Abandon a failed build and say so; returns its status, "ready" when a finish whose
    answer was lost went through."""
    label = f"{exact} browser build b{started['build']}"
    if _status_of(client, started["build_id"]) == "ready":
        return "ready"
    try:
        client.rpc("abandon_genome_browser_build", {"p_build_id": started["build_id"]}).execute()
    except Exception:  # noqa: BLE001 - the original failure is the one to report
        click.echo(f"{label} could not be abandoned and is left uploading; ask a Bloom admin to "
                   "clear it before building again.", err=True)
        return None
    click.echo(f"{label} was abandoned; run the same command again to make a new build.",
               err=True)
    return "abandoned"


def _status_of(client: Any, build_id: int) -> str | None:
    try:
        rows = client.table("genome_browser_builds").select("status").eq(
            "id", build_id).execute().data
    except Exception:  # noqa: BLE001 - only used to settle an earlier failure
        return None
    return rows[0]["status"] if rows else None
