"""Recording GO and pathway results in Bloom: an annotation build in genome-references and an
enrichment run in scrna, each started, uploaded and finished through Bloom's functions (and
abandoned if the upload fails); and a ready build's file fetched back, its checksum checked."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import annotation as annotation_file
from . import enrichment as enrichment_file
from .steps import EXIT_BAD_ANNOTATION, EXIT_BAD_ENRICHMENT_INPUT, StepError

ANNOTATION_BUCKET = "genome-references"
ENRICHMENT_BUCKET = "scrna"
GZIP_OPTIONS = {"content-type": "application/gzip", "upsert": "false"}
_VERSION_LABEL = re.compile(r"^(?P<genome>.+)\.v(?P<version>[1-9][0-9]*)$")


def _refused_errors(refused: type[Exception] | None) -> type[Exception]:
    """What Bloom raises when it answers with a refusal (postgrest's APIError)."""
    if refused is not None:
        return refused
    from postgrest.exceptions import APIError

    return APIError


def _rows(result: Any) -> list[dict]:
    data = result.data
    return data if isinstance(data, list) else [data]


def _checked(path: Path) -> tuple[bytes, str]:
    data = path.read_bytes()
    return data, hashlib.sha256(data).hexdigest()


def _doc(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# Annotation builds
# --------------------------------------------------------------------------- #


def version_id(client: Any, label: str) -> int:
    """The id of a ready genome version, written <genome>.v<N>."""
    match = _VERSION_LABEL.match(label)
    if not match:
        raise StepError(EXIT_BAD_ANNOTATION, f"{label!r} is not a genome version <genome>.v<N>")
    genomes = client.table("genome_references").select("id").eq(
        "name", match["genome"]).execute().data
    if not genomes:
        raise StepError(EXIT_BAD_ANNOTATION, f"Bloom has no genome {match['genome']!r}")
    versions = client.table("genome_reference_versions").select("id, status").eq(
        "genome_id", genomes[0]["id"]).eq("version", int(match["version"])).execute().data
    if not versions:
        raise StepError(EXIT_BAD_ANNOTATION, f"{match['genome']} has no v{match['version']}")
    if versions[0]["status"] != "ready":
        raise StepError(EXIT_BAD_ANNOTATION, f"{label} is {versions[0]['status']}, not ready")
    return versions[0]["id"]


def upload_annotation(client: Any, path: Path, *, refused: type[Exception] | None = None) -> dict:
    """Records an annotation file as its genome version's next build of its source."""
    refusal = _refused_errors(refused)
    built = annotation_file.read(path)
    doc = _doc(path)
    version = version_id(client, f"{doc['genome']}.v{doc['version']}")
    data, sha256 = _checked(path)
    try:
        (build,) = _rows(client.rpc("start_genome_annotation_build", {
            "p_version_id": version, "p_source": doc["source"],
            "p_tool_versions": doc["tools"],
        }).execute())
    except refusal as exc:
        raise StepError(EXIT_BAD_ANNOTATION, f"Bloom refused the build: {exc}") from exc
    try:
        client.storage.from_(ANNOTATION_BUCKET).upload(build["file_path"], data, GZIP_OPTIONS)
        client.rpc("finish_genome_annotation_build", {
            "p_build_id": build["build_id"], "p_file_sha256": sha256, "p_file_bytes": len(data),
            "p_gene_count": len(built.genes), "p_term_count": len(built.terms),
        }).execute()
    except BaseException:
        client.rpc("abandon_genome_annotation_build", {"p_build_id": build["build_id"]}).execute()
        raise
    return build


def fetch_annotation(client: Any, build_id: int, out: Path) -> dict:
    """A ready build's file into `out`, refused unless it matches the build's checksum and size."""
    rows = client.table("genome_annotation_builds").select(
        "id, version_id, source, build, status, file_path, file_sha256, file_bytes").eq(
        "id", build_id).execute().data
    if not rows:
        raise StepError(EXIT_BAD_ENRICHMENT_INPUT, f"Bloom has no annotation build {build_id}")
    build = rows[0]
    if build["status"] != "ready":
        raise StepError(EXIT_BAD_ENRICHMENT_INPUT,
                        f"annotation build {build_id} is {build['status']}, not ready")
    data = client.storage.from_(ANNOTATION_BUCKET).download(build["file_path"])
    if (hashlib.sha256(data).hexdigest(), len(data)) != (build["file_sha256"],
                                                           build["file_bytes"]):
        raise StepError(EXIT_BAD_ENRICHMENT_INPUT,
                        f"{build['file_path']} doesn't match annotation build {build_id}'s "
                        "checksum and size")
    out.write_bytes(data)
    return build


def label(client: Any, build: dict) -> str:
    """How an enrichment names its build: <genome>.v<N>/<source>-b<B>."""
    (version,) = client.table("genome_reference_versions").select("genome_id, version").eq(
        "id", build["version_id"]).execute().data
    (genome,) = client.table("genome_references").select("name").eq(
        "id", version["genome_id"]).execute().data
    return f"{genome['name']}.v{version['version']}/{build['source']}-b{build['build']}"


# --------------------------------------------------------------------------- #
# Enrichment runs
# --------------------------------------------------------------------------- #


def _link(client: Any, dataset_id: int, version: int, refusal: type[Exception]) -> None:
    """Links the dataset to the build's genome version, unless it already is."""
    rows = client.table("scrna_datasets").select("genome_version_id").eq(
        "id", dataset_id).execute().data
    if not rows:
        raise StepError(EXIT_BAD_ENRICHMENT_INPUT, f"Bloom has no dataset {dataset_id}")
    current = rows[0]["genome_version_id"]
    if current == version:
        return
    if current is not None:
        raise StepError(EXIT_BAD_ENRICHMENT_INPUT,
                        f"dataset {dataset_id} is linked to genome version {current}, "
                        f"not the annotation's {version}")
    try:
        client.rpc("link_scrna_dataset_genome",
                   {"p_dataset_id": dataset_id, "p_version_id": version}).execute()
    except refusal as exc:
        raise StepError(EXIT_BAD_ENRICHMENT_INPUT, f"Bloom refused the link: {exc}") from exc


def upload_enrichment(client: Any, path: Path, *, build_id: int, rnaseq_run_id: int | None = None,
                      refused: type[Exception] | None = None) -> dict:
    """Records an enrichment file as a run of its dataset's analysis against `build_id`."""
    refusal = _refused_errors(refused)
    doc = _doc(path)
    if doc.get("format") != enrichment_file.FORMAT:
        raise StepError(EXIT_BAD_ENRICHMENT_INPUT,
                        f"{path.name} is {doc.get('format')!r}, not {enrichment_file.FORMAT}")
    (build,) = client.table("genome_annotation_builds").select("version_id").eq(
        "id", build_id).execute().data or [None]
    if build is None:
        raise StepError(EXIT_BAD_ENRICHMENT_INPUT, f"Bloom has no annotation build {build_id}")
    _link(client, doc["dataset_id"], build["version_id"], refusal)
    data, sha256 = _checked(path)
    try:
        (run,) = _rows(client.rpc("start_scrna_enrichment_run", {
            "p_dataset_id": doc["dataset_id"], "p_de_run_id": doc["de_run_id"],
            "p_annotation_build_id": build_id, "p_params": doc["params"],
            "p_rnaseq_run_id": rnaseq_run_id,
        }).execute())
    except refusal as exc:
        raise StepError(EXIT_BAD_ENRICHMENT_INPUT, f"Bloom refused the run: {exc}") from exc
    try:
        client.storage.from_(ENRICHMENT_BUCKET).upload(run["file_path"], data, GZIP_OPTIONS)
        client.rpc("finish_scrna_enrichment_run", {
            "p_run_id": run["run_id"], "p_file_sha256": sha256, "p_file_bytes": len(data),
            "p_term_count": len(doc["results"]), "p_background_genes": doc["background_genes"],
            "p_annotated_genes": doc["annotated_genes"],
        }).execute()
    except BaseException:
        client.rpc("abandon_scrna_enrichment_run", {"p_run_id": run["run_id"]}).execute()
        raise
    return run
