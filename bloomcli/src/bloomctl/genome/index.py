"""`bloomctl genome index`: build a genome version's files for the genome browser.

The version's stored FASTA and GTF are downloaded and checked against Bloom's record, turned into
the five indexed files the browser reads (see _index), and every gene's position is read from the
GTF. Only when all of that works is a build started; the files are then uploaded, the genes sent
in batches, and the build finished. A failure or a stop signal after the start abandons the build.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import click
import httpx

from .._signals import stop_signals_interrupt
from ..credentials import DEFAULT_PROFILE
from ..go_pathways import InProgress
from ..scrna import _session, _transfer
from ..scrna._text import visible
from . import _files, _index
from .download import NAMED, Unavailable, fetch, ready_version
from .upload import WRITER_ROLE, send

WORKFLOWS_ROLE = "bloom_workflows"
CONTENT_TYPES = {
    "fasta": "application/gzip",
    "fasta_fai": "text/plain",
    "fasta_gzi": "application/octet-stream",
    "gtf": "application/gzip",
    "gtf_tbi": "application/octet-stream",
}
# A batch of genes is sent again this many times after a lost answer or a server error;
# sending again is safe.
GENE_SEND_TRIES = 3
# PostgREST's codes for a database it could not reach, alongside HTTP 5xx.
TRANSIENT_CODES = {"PGRST001", "PGRST002"}


class Transient(click.ClickException):
    """A server or network error; running the command again may work."""


@click.command(name="index")
@click.argument("genome")
@click.option("--workdir", type=click.Path(file_okay=False, path_type=Path),
              help="Where to build the files (default: a temporary folder).")
@click.option("--replace-unfinished", is_flag=True,
              help="Abandon this version's unfinished build first, when you started it (an "
                   "earlier run that was stopped).")
@click.option("-p", "--profile", default=DEFAULT_PROFILE, show_default=True,
              help="Credentials profile to use.")
def index(genome: str, workdir: Path | None, replace_unfinished: bool, profile: str) -> None:
    """Build GENOME's files for the genome browser and record them in Bloom.

    GENOME is a ready genome version, NAME.vN. Prints one line, the build's number, id, folder
    and gene count, also when the version already has a ready build. Exits 3 when the version
    can't be built (not ready, a file that doesn't match Bloom's record, or a GTF or FASTA the
    browser can't use) or Bloom refuses the build; 4 while another build of it is unfinished;
    1 for a network or server error, worth retrying.
    """
    named = NAMED.fullmatch(genome)
    if (named is None or named["version"] is None
            or not _files.NAME_RULE.match(named["name"])):
        raise click.UsageError(f"GENOME must be a genome version, NAME.vN ({_files.NAME_HELP}).")
    _index.require_htslib()
    name = named["name"]
    conn = _session.connect(profile)
    if conn.role not in (WRITER_ROLE, WORKFLOWS_ROLE):
        raise click.ClickException(
            f"this login signs in as {conn.role or 'no role'}; building a genome for the browser "
            f"needs a writer ({WRITER_ROLE}) or pipeline ({WORKFLOWS_ROLE}) login. Nothing was "
            "started."
        )
    version = ready_version(conn.client, name, named["version"],
                            "only a ready version can be built. Nothing was started.")
    exact = f"{name}.v{version['version']}"
    ready = _existing(conn.client, version, exact, replace_unfinished)
    if ready is not None:
        click.echo(f"{exact} already has browser build b{ready['build']}; nothing to do.",
                   err=True)
        _say(exact, ready, ready.get("gene_count"))
        return
    if workdir is not None:
        workdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bloomctl-genome-index-", dir=workdir) as tmp:
        built, run = _prepare(conn, profile, exact, version, Path(tmp))
        started = _start(run["conn"].client, version["id"], exact)
        try:
            with stop_signals_interrupt():
                _upload(run, profile, started, built)
                _add_genes(run["conn"].client, started["build_id"], built.genes)
                _finish(run["conn"].client, started["build_id"], built)
        except BaseException:
            if _abandon(run["conn"].client, started, exact) != "ready":
                raise
    _say(exact, started, len(built.genes))


def _say(exact: str, build: dict[str, Any], genes: int | None) -> None:
    """The one line on stdout: the build, its folder and gene count."""
    build_id = build.get("build_id", build.get("id"))
    click.echo(f"{exact} browser build b{build['build']} (id {build_id}): "
               f"{visible(build['folder'])}, {genes} genes")


def _existing(client: Any, version: dict[str, Any], exact: str, replace: bool
              ) -> dict[str, Any] | None:
    """The version's ready build, if it has one. An unfinished build is abandoned when asked
    (and the caller started it); otherwise the command waits for it (exit 4)."""
    from .._postgrest import queried

    builds = queried(
        "the version's browser builds",
        lambda: client.table("genome_browser_builds")
        .select("id, build, status, folder, gene_count").eq("version_id", version["id"])
        .execute().data,
    ) or []
    ready = next((b for b in builds if b["status"] == "ready"), None)
    if ready is not None:
        return ready
    for build in (b for b in builds if b["status"] == "uploading"):
        if not replace:
            raise InProgress(
                f"{exact}'s browser build b{build['build']} is unfinished. If no other run is "
                "making it (an earlier run was stopped), run again with --replace-unfinished."
            )
        _rpc(client, f"abandon unfinished build b{build['build']}",
             "abandon_genome_browser_build", {"p_build_id": build["id"]})
        click.echo(f"Abandoned {exact}'s unfinished browser build b{build['build']}.", err=True)
    return None


def _prepare(conn, profile: str, exact: str, version: dict[str, Any], tmp: Path):
    """Download the version's files and build the browser set; nothing is started in Bloom."""
    sources, out = tmp / "sources", tmp / "browser"
    sources.mkdir()
    out.mkdir()
    with _transfer.open_client() as http:
        for kind, plain in (("fasta", "genome.fa"), ("gtf", "genes.gtf")):
            conn = fetch(http, conn, profile, exact, version, kind, plain, sources, False)
    try:
        built = _index.build(sources / "genome.fa.gz", sources / "genes.gtf.gz", out,
                             tmp / "work")
    except _index.IndexProblem as exc:
        raise Unavailable(f"{exact} can't be built for the browser: {visible(str(exc))}. "
                          "Nothing was started.") from exc
    for path in sources.iterdir():
        path.unlink()
    return built, {"conn": conn}


def _rpc(client: Any, what: str, function: str, args: dict[str, Any]) -> Any:
    """Call a database function: a refusal exits 3; a server or network error exits 1."""
    from postgrest import APIError

    from ..errors import explain

    try:
        return client.rpc(function, args).execute().data
    except APIError as exc:
        code = str(getattr(exc, "code", "") or "")
        # A three-digit code is the gateway's HTTP status; Postgres's own codes have five.
        if code in TRANSIENT_CODES or (len(code) == 3 and code.isdigit() and code >= "500"):
            raise Transient(f"Bloom could not {what} (server error {code}); run the same "
                            "command again.") from exc
        raise Unavailable(f"Bloom refused to {what}: {visible(explain(exc))}") from exc
    except httpx.HTTPError as exc:
        raise Transient(f"Bloom could not be reached to {what} ({type(exc).__name__}); run the "
                        "same command again.") from exc


def _start(client: Any, version_id: int, exact: str) -> dict[str, Any]:
    try:
        rows = _rpc(client, f"start a browser build of {exact}", "start_genome_browser_build",
                    {"p_version_id": version_id, "p_tool_versions": _index.tool_versions()})
    except Unavailable as exc:
        if "already has browser build" in exc.message:
            raise InProgress(f"{exact} already has a browser build in progress.") from exc
        raise
    except Transient as exc:
        raise Transient(f"{exc.message} If Bloom started the build anyway, add "
                        "--replace-unfinished.") from exc
    rows = rows if isinstance(rows, list) else [rows]
    if not rows:
        raise click.ClickException("Bloom did not return the new build. Nothing was sent.")
    return rows[0]


def _upload(run: dict[str, Any], profile: str, started: dict[str, Any],
            built: _index.Built) -> None:
    """Send the five files, keeping the latest session in `run` for whatever follows."""
    with _transfer.open_client() as http:
        for role, name in _index.ROLES.items():
            run["conn"] = send(http, run["conn"], profile, f"{started['folder']}/{name}",
                               built.files[role], name, CONTENT_TYPES[role])


def _add_genes(client: Any, build_id: int, genes: list[dict[str, Any]]) -> None:
    for batch in _index.batches(genes):
        for attempt in range(1, GENE_SEND_TRIES + 1):
            try:
                _rpc(client, "add genes", "add_genome_browser_genes",
                     {"p_build_id": build_id, "p_genes": batch})
                break
            except Transient:
                if attempt == GENE_SEND_TRIES:
                    raise


def _finish(client: Any, build_id: int, built: _index.Built) -> None:
    files = {role: {"sha256": f.sha256, "bytes": f.size} for role, f in built.files.items()}
    _rpc(client, "finish the browser build", "finish_genome_browser_build", {
        "p_build_id": build_id, "p_files": files, "p_gene_count": len(built.genes),
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
        state = _status_of(client, started["build_id"])
        if state == "ready":
            return "ready"
        click.echo(f"{label} could not be abandoned and is {state or 'in an unknown state'}; "
                   "run again with --replace-unfinished to clear it.", err=True)
        return state
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
