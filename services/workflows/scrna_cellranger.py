"""
Cell Ranger trigger: validate a run request and create the run with
`request_scrna_cellranger_run`.

A run is one sample and one genome version from Bloom (`genome_version_id`), which the
run records and builds its Cell Ranger reference from; the database refuses a version that
isn't ready. Its FASTQs come from one place: an S3 folder
(`fastq_url`, checked here and recorded with each file's size and ETag, the sample named
from the files; `fastq_files` is the list the form showed, and the run is refused if the
folder no longer matches it) or SRA run IDs; a sample name alone is refused. The pipeline
checks that the FASTQs exist and fails the run if not.
A `metadata` object (the dataset's name and species, which every run needs, and its
conditions) is stored on the run as given, for loading the results later. Optional `sra_runs` (1 to 9 SRA run IDs, one
lane each) import the sample from SRA under the new name `sample`; the run downloads them
into its own folder on the cluster.
"""

import json
import re

from fastapi import HTTPException
from postgrest import APIError

import s3_folder
from supabase_client import app_client

# Allowed names; the database checks the same rules. The sample rule lives with the folder
# check, which names a folder's sample from its files.
SAMPLE_RULE = s3_folder.SAMPLE_RULE
SAMPLE_HELP = s3_folder.SAMPLE_HELP
GENOME_VERSION_HELP = "the id of a ready genome version (a whole number above 0)"
# The largest id the database's BIGINT holds; a larger one is refused here, not as a 500.
MAX_GENOME_VERSION_ID = 2**63 - 1
# A version's genome name and number, which the run's reference becomes as <genome>.v<N>.
GENOME_VERSIONS_TABLE = "genome_reference_versions"

# The runs table shared by every RNA-seq workflow type; this module serves Cell Ranger rows.
RUNS_TABLE = "rnaseq_runs"
WORKFLOW_TYPE = "scrna-cellranger"
REQUEST_FN = "request_scrna_cellranger_run"

# The database refuses a larger metadata object; checking here gives a readable 422.
METADATA_MAX_BYTES = 65536
NO_DATASET = (
    "metadata needs a dataset_name and a species_id: every run loads its result into Bloom "
    "as a dataset"
)
# The loader's rule (bloomctl's SAFE_DATASET_NAME), as a storage path needs; tests/unit pins it.
DATASET_NAME_RULE = re.compile(r"[A-Za-z0-9._ -]+")
DOTS_ONLY = re.compile(r"\.+")
DATASET_NAME_HELP = (
    "The dataset name can use only letters A-Z, digits, spaces, '.', '_' and '-', and not "
    "dots alone: it becomes part of a storage path"
)

# NCBI (SRR), ENA (ERR) and DDBJ (DRR) run IDs; one lane each, named L001-L009.
SRA_RUN_RULE = re.compile(r"^[SED]RR[0-9]{6,10}$")
MAX_SRA_RUNS = 9
SRA_RUNS_HELP = f"1 to {MAX_SRA_RUNS} distinct SRA run IDs like SRR12046049"

# What the request function's refusals mean for the caller: a bad value (22023), or a run
# that would repeat one (23505): its run key already has a live run, or the scientist already
# ran these SRA runs on this genome under another name. 55000 ("still being imported") comes
# only from the function before 20261013230000, until that migration is applied.
_REFUSAL_STATUS = {"22023": 422, "23505": 409, "55000": 409}


def _valid_name(value, rule: re.Pattern) -> bool:
    return isinstance(value, str) and bool(rule.fullmatch(value))


def _validate_metadata(metadata) -> dict:
    """The run's form details; a run needs a dataset name and a species to load its result."""
    if metadata is None:
        raise HTTPException(status_code=422, detail=NO_DATASET)
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=422, detail="metadata must be a JSON object")
    name, species_id = metadata.get("dataset_name"), metadata.get("species_id")
    if (
        not isinstance(name, str)
        or not name.strip()
        or type(species_id) is not int
        or species_id < 1
    ):
        raise HTTPException(status_code=422, detail=NO_DATASET)
    # Checked as the worker and loader see it: trimmed.
    stripped = name.strip()
    if not DATASET_NAME_RULE.fullmatch(stripped) or DOTS_ONLY.fullmatch(stripped):
        raise HTTPException(status_code=422, detail=DATASET_NAME_HELP)
    size = len(json.dumps(metadata, ensure_ascii=False).encode())
    if size > METADATA_MAX_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"metadata must be at most {METADATA_MAX_BYTES // 1024} KB",
        )
    return metadata


def _validate_sra_runs(runs) -> list[str] | None:
    if runs is None:
        return None
    if (
        not isinstance(runs, list)
        or not 1 <= len(runs) <= MAX_SRA_RUNS
        or not all(isinstance(r, str) and SRA_RUN_RULE.fullmatch(r) for r in runs)
        or len(set(runs)) != len(runs)
    ):
        raise HTTPException(status_code=422, detail=f"sra_runs must be {SRA_RUNS_HELP}")
    return runs


def _validate_genome_version(body: dict) -> int:
    if "reference" in body:
        raise HTTPException(
            status_code=422,
            detail="choose a genome version (genome_version_id); reference names aren't taken",
        )
    version_id = body.get("genome_version_id")
    if type(version_id) is not int or not 1 <= version_id <= MAX_GENOME_VERSION_ID:
        raise HTTPException(
            status_code=422, detail=f"genome_version_id must be {GENOME_VERSION_HELP}"
        )
    return version_id


def _genome_reference(client, version_id: int) -> str:
    """The version as <genome>.v<N>, the reference name the database gives the run."""
    rows = (
        client.table(GENOME_VERSIONS_TABLE)
        .select("version, genome_references(name)")
        .eq("id", version_id)
        .execute()
        .data
        or []
    )
    genome = (rows[0].get("genome_references") or {}).get("name") if rows else None
    if not genome:
        raise HTTPException(status_code=422, detail=f"no genome version {version_id}")
    return f"{genome}.v{rows[0]['version']}"


def _validate_request(body) -> tuple[str, int, dict | None, list[str] | None]:
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=422, detail="request body must be a JSON object"
        )
    sample = body.get("sample")
    if not _valid_name(sample, SAMPLE_RULE):
        raise HTTPException(
            status_code=422, detail=f"sample must be a name of {SAMPLE_HELP}"
        )
    return (
        sample,
        _validate_genome_version(body),
        _validate_metadata(body.get("metadata")),
        _validate_sra_runs(body.get("sra_runs")),
    )


def _file_set(files) -> set[tuple] | None:
    """The files as comparable (name, size, etag) entries, or None if not a file list."""
    if not isinstance(files, list) or not all(isinstance(f, dict) for f in files):
        return None
    return {(f.get("name"), f.get("size"), f.get("etag")) for f in files}


def _check_folder_for_start(body: dict) -> dict:
    """The folder as it is now, refused if it isn't what the form showed."""
    shown = _file_set(body.get("fastq_files"))
    if shown is None:
        raise HTTPException(
            status_code=422,
            detail="fastq_files must be the files the folder check returned",
        )
    folder = s3_folder.check_folder(body["fastq_url"])
    if _file_set(folder["files"]) != shown:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{folder['fastq_url']} changed since it was checked; check it again, then "
                "start the run"
            ),
        )
    return folder


def check_folder(body) -> dict:
    """The folder check on its own, for the form: queues nothing."""
    return s3_folder.check_folder(body.get("fastq_url"))


def trigger_run(body, user_id: str) -> dict:
    folder = None
    if isinstance(body, dict) and body.get("fastq_url") is not None:
        if body.get("sra_runs") is not None:
            raise HTTPException(
                status_code=422, detail="give SRA run IDs or an S3 folder, not both"
            )
        if body.get("sample") is not None:
            raise HTTPException(
                status_code=422,
                detail="give an S3 folder or a sample, not both; a folder's sample comes from its files",
            )
        # The cheap checks first, so a typo doesn't wait on S3.
        _validate_genome_version(body)
        _validate_metadata(body.get("metadata"))
        # Listed again here: the folder may have changed since the form checked it.
        folder = _check_folder_for_start(body)
        body = {**body, "sample": folder["sample"]}
    sample, version_id, metadata, sra_runs = _validate_request(body)
    if folder is None and sra_runs is None:
        raise HTTPException(
            status_code=422,
            detail="give an S3 folder or SRA run IDs to read the sample from",
        )

    client = app_client()
    reference = _genome_reference(client, version_id)
    # p_reference has no default, so it's sent as null for the function to be found.
    args = {
        "p_sample": sample,
        "p_reference": None,
        "p_requested_by": user_id,
        "p_genome_version_id": version_id,
        "p_metadata": metadata,
    }
    if sra_runs is not None:
        args["p_sra_runs"] = sra_runs
    if folder is not None:
        args["p_fastq_url"] = folder["fastq_url"]
        args["p_fastq_files"] = folder["files"]

    try:
        run_id = client.rpc(REQUEST_FN, args).execute().data
    except APIError as exc:
        status = _REFUSAL_STATUS.get(exc.code)
        if status is None:
            raise
        raise HTTPException(status_code=status, detail=exc.message) from exc

    started = {
        "run_id": run_id,
        "sample": sample,
        "genome_version_id": version_id,
        "reference": reference,
        "run_key": f"{sample}__{reference}__{user_id}",
    }
    if sra_runs is not None:
        started["sra_runs"] = sra_runs
    if folder is not None:
        started |= {
            key: folder[key]
            for key in ("fastq_url", "lanes", "files", "file_count", "total_bytes")
        }
    return started


def get_run(run_id: int) -> dict:
    """The run's row, if it is a Cell Ranger run."""
    client = app_client()
    rows = (
        client.table(RUNS_TABLE)
        .select("*")
        .eq("id", run_id)
        .eq("workflow_type", WORKFLOW_TYPE)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"Cell Ranger run {run_id} not found"
        )
    return rows[0]
