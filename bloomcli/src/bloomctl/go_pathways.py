"""GO and pathway results in Bloom: a genome version's annotation builds (genome-references) and a
dataset's enrichment runs (scrna). Each is recorded by start, upload, finish through Bloom's
functions, and abandoned if the upload or finish fails; a ready build is fetched back with its
checksum and size checked. The files are bloom-annotation/1 and bloom-enrichment/1 (gzipped
JSON, made by the GO and pathway workflow steps).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import click

ANNOTATION_FORMAT = "bloom-annotation/1"
ENRICHMENT_FORMAT = "bloom-enrichment/1"
ANNOTATION_BUCKET = "genome-references"
ENRICHMENT_BUCKET = "scrna"
SOURCES = ("eggnog", "tair-go", "mercator")
UPLOAD_OPTIONS = {"content-type": "application/gzip", "upsert": "false"}
_VERSION_LABEL = re.compile(r"^(?P<genome>.+)\.v(?P<version>[1-9][0-9]*)$")

# Exit codes: 0 done; 3 refused, or nothing to fetch (asking again won't help); 4 a build of that
# version and source is still uploading (worth waiting for); 1 anything else, such as a network
# error (worth retrying); 2 a usage error.
EXIT_REFUSED = 3
EXIT_IN_PROGRESS = 4


class Refused(click.ClickException):
    """Bloom said no, or there is nothing to fetch; asking again won't help."""

    exit_code = EXIT_REFUSED


class InProgress(click.ClickException):
    """A build is still uploading; it may be ready later."""

    exit_code = EXIT_IN_PROGRESS


def _refusals() -> tuple[type[Exception], ...]:
    """What Bloom raises when it answers with a refusal: a function's or an upload's."""
    from postgrest.exceptions import APIError
    from storage3.exceptions import StorageApiError

    return (APIError, StorageApiError)


def _rows(result: Any) -> list[dict]:
    data = result.data
    return data if isinstance(data, list) else [data]


def read_file(path: Path, expected: str) -> dict:
    """A gzipped JSON results file, refused unless it is of the expected format."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Refused(f"{path.name} is not a gzipped JSON file: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("format") != expected:
        found = doc.get("format") if isinstance(doc, dict) else None
        raise Refused(f"{path.name} is {found!r}, not {expected}")
    return doc


def version_id(client: Any, label: str) -> int:
    """The id of a ready genome version, written <genome>.v<N>."""
    match = _VERSION_LABEL.match(label)
    if not match:
        raise click.UsageError(f"{label!r} is not a genome version <genome>.v<N>")
    genomes = client.table("genome_references").select("id").eq(
        "name", match["genome"]).execute().data
    if not genomes:
        raise Refused(f"Bloom has no genome {match['genome']!r}")
    versions = client.table("genome_reference_versions").select("id, status").eq(
        "genome_id", genomes[0]["id"]).eq("version", int(match["version"])).execute().data
    if not versions:
        raise Refused(f"{match['genome']} has no v{match['version']}")
    if versions[0]["status"] != "ready":
        raise Refused(f"{label} is {versions[0]['status']}, not ready")
    return versions[0]["id"]


# --------------------------------------------------------------------------- #
# Annotation builds
# --------------------------------------------------------------------------- #


def _annotation_counts(doc: dict, name: str) -> tuple[int, int]:
    genes, terms = doc.get("genes"), doc.get("terms")
    if not isinstance(genes, dict) or not isinstance(terms, dict) or not genes or not terms:
        raise Refused(f"{name} has no genes or no terms")
    for gene, info in genes.items():
        unknown = [t for t in info.get("terms", []) if t not in terms]
        if unknown:
            raise Refused(f"{name}: gene {gene} uses unknown terms {', '.join(unknown[:5])}")
    return len(genes), len(terms)


def _abandon(client: Any, function: str, key: str, record_id: int, what: str) -> None:
    """Cancel a failed upload's record; if Bloom can't be told, say who can clear it."""
    try:
        client.rpc(function, {key: record_id}).execute()
    except Exception:  # noqa: BLE001 - the upload's own failure is the one to raise
        click.echo(f"{what} couldn't be cancelled and is left as a partial upload; ask a Bloom "
                   "admin to clear it before uploading again.", err=True)


def upload_annotation(client: Any, path: Path) -> dict:
    """Records an annotation file as its genome version's next build of its source."""
    doc = read_file(path, ANNOTATION_FORMAT)
    gene_count, term_count = _annotation_counts(doc, path.name)
    version = version_id(client, f"{doc['genome']}.v{doc['version']}")
    data = path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    refused = _refusals()
    try:
        (build,) = _rows(client.rpc("start_genome_annotation_build", {
            "p_version_id": version, "p_source": doc["source"],
            "p_tool_versions": doc.get("tools", {}),
        }).execute())
    except refused as exc:
        raise Refused(f"Bloom refused the build: {exc}") from exc
    stage = "store the file of"
    try:
        client.storage.from_(ANNOTATION_BUCKET).upload(build["file_path"], data,
                                                        dict(UPLOAD_OPTIONS))
        stage = "finish"
        client.rpc("finish_genome_annotation_build", {
            "p_build_id": build["build_id"], "p_file_sha256": sha256, "p_file_bytes": len(data),
            "p_gene_count": gene_count, "p_term_count": term_count,
        }).execute()
    except refused as exc:
        _abandon(client, "abandon_genome_annotation_build", "p_build_id", build["build_id"],
                 f"Annotation build {build['build']}")
        raise Refused(f"Bloom refused to {stage} annotation build {build['build']}: {exc}") from exc
    except BaseException:
        _abandon(client, "abandon_genome_annotation_build", "p_build_id", build["build_id"],
                 f"Annotation build {build['build']}")
        raise
    return build


def find_build(client: Any, version: int, source: str) -> dict:
    """The version's ready build of the source; InProgress while one uploads; Refused if none."""
    rows = client.table("genome_annotation_builds").select(
        "id, version_id, source, build, status, file_path, file_sha256, file_bytes").eq(
        "version_id", version).eq("source", source).execute().data or []
    ready = [r for r in rows if r["status"] == "ready"]
    if ready:
        return max(ready, key=lambda r: r["build"])
    uploading = [r for r in rows if r["status"] == "uploading"]
    if uploading:
        raise InProgress(f"annotation build {uploading[0]['build']} is still uploading")
    raise Refused(f"genome version {version} has no {source} annotation")


def download_build(client: Any, build: dict, out: Path) -> None:
    """A build's file into `out`, refused unless it matches the build's checksum and size."""
    data = client.storage.from_(ANNOTATION_BUCKET).download(build["file_path"])
    if (hashlib.sha256(data).hexdigest(), len(data)) != (build["file_sha256"],
                                                           build["file_bytes"]):
        raise Refused(f"{build['file_path']} doesn't match annotation build {build['id']}'s "
                      "checksum and size")
    out.write_bytes(data)


# --------------------------------------------------------------------------- #
# Enrichment runs
# --------------------------------------------------------------------------- #


def _link(client: Any, dataset_id: int, version: int, refused: tuple) -> None:
    """Links the dataset to the build's genome version, unless it already is."""
    rows = client.table("scrna_datasets").select("genome_version_id").eq(
        "id", dataset_id).execute().data
    if not rows:
        raise Refused(f"Bloom has no dataset {dataset_id}")
    current = rows[0]["genome_version_id"]
    if current == version:
        return
    if current is not None:
        raise Refused(f"dataset {dataset_id} is linked to genome version {current}, "
                      f"not the annotation's {version}")
    try:
        client.rpc("link_scrna_dataset_genome",
                   {"p_dataset_id": dataset_id, "p_version_id": version}).execute()
    except refused as exc:
        raise Refused(f"Bloom refused the link: {exc}") from exc


def upload_enrichment(client: Any, path: Path, *, build_id: int,
                      rnaseq_run_id: int | None = None) -> dict:
    """Records an enrichment file as a run of its dataset's analysis against `build_id`."""
    doc = read_file(path, ENRICHMENT_FORMAT)
    for key in ("dataset_id", "de_run_id", "background_genes", "annotated_genes"):
        if not isinstance(doc.get(key), int):
            raise Refused(f"{path.name} has no {key}")
    if not isinstance(doc.get("results"), list) or not isinstance(doc.get("params"), dict):
        raise Refused(f"{path.name} has no results or settings")
    builds = client.table("genome_annotation_builds").select("version_id").eq(
        "id", build_id).execute().data
    if not builds:
        raise Refused(f"Bloom has no annotation build {build_id}")
    refused = _refusals()
    _link(client, doc["dataset_id"], builds[0]["version_id"], refused)
    data = path.read_bytes()
    try:
        (run,) = _rows(client.rpc("start_scrna_enrichment_run", {
            "p_dataset_id": doc["dataset_id"], "p_de_run_id": doc["de_run_id"],
            "p_annotation_build_id": build_id, "p_params": doc["params"],
            "p_rnaseq_run_id": rnaseq_run_id,
        }).execute())
    except refused as exc:
        raise Refused(f"Bloom refused the run: {exc}") from exc
    stage = "store the file of"
    try:
        client.storage.from_(ENRICHMENT_BUCKET).upload(run["file_path"], data,
                                                        dict(UPLOAD_OPTIONS))
        stage = "finish"
        client.rpc("finish_scrna_enrichment_run", {
            "p_run_id": run["run_id"], "p_file_sha256": hashlib.sha256(data).hexdigest(),
            "p_file_bytes": len(data), "p_term_count": len(doc["results"]),
            "p_background_genes": doc["background_genes"],
            "p_annotated_genes": doc["annotated_genes"],
        }).execute()
    except refused as exc:
        _abandon(client, "abandon_scrna_enrichment_run", "p_run_id", run["run_id"],
                 f"Enrichment run {run['run_id']}")
        raise Refused(f"Bloom refused to {stage} enrichment run {run['run_id']}: {exc}") from exc
    except BaseException:
        _abandon(client, "abandon_scrna_enrichment_run", "p_run_id", run["run_id"],
                 f"Enrichment run {run['run_id']}")
        raise
    return run
