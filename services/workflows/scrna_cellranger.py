"""
Cell Ranger trigger: validate a run request and create the run with
`request_scrna_cellranger_run`.

A run is one sample and one reference. Its FASTQs come from one place: an S3 folder
(`fastq_url`, checked here and recorded with each file's size and ETag, the sample named
from the files), SRA run IDs, or a registered sample's folder under raw_reads/. The
pipeline checks that the reference and the FASTQs exist and fails the run if not.
An optional `metadata` object (the dataset's species, name, conditions) is stored on the
run as given, for loading the results later. Optional `sra_runs` (1 to 9 SRA run IDs, one
lane each) import the sample from SRA under the new name `sample`; the run downloads them
first and registers the sample once the download succeeds.
"""

import json
import re

from fastapi import HTTPException
from postgrest import APIError

import s3_folder
from supabase_client import app_client

# Allowed names ('__' separates run_key parts); the database checks the same rules.
# A sample is also Cell Ranger's run id, which allows no '.' and at most 64 characters.
SAMPLE_RULE = re.compile(r"^(?!.*__)[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SAMPLE_HELP = (
    "letters, digits, '_' or '-', starting with a letter or digit, "
    "with no '__' (at most 64)"
)
REFERENCE_RULE = re.compile(r"^(?!.*__)[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
REFERENCE_HELP = (
    "letters, digits, '.', '_' or '-', starting with a letter or digit, "
    "with no '__' (at most 100)"
)

# The runs table shared by every RNA-seq workflow type; this module serves Cell Ranger rows.
RUNS_TABLE = "rnaseq_runs"
WORKFLOW_TYPE = "scrna-cellranger"
REQUEST_FN = "request_scrna_cellranger_run"

# The database refuses a larger metadata object; checking here gives a readable 422.
METADATA_MAX_BYTES = 65536

# NCBI (SRR), ENA (ERR) and DDBJ (DRR) run IDs; one lane each, named L001-L009.
SRA_RUN_RULE = re.compile(r"^[SED]RR[0-9]{6,10}$")
MAX_SRA_RUNS = 9
SRA_RUNS_HELP = f"1 to {MAX_SRA_RUNS} distinct SRA run IDs like SRR12046049"

# What the request function's refusals mean for the caller: a bad value, or a name that
# is taken or still being imported.
_REFUSAL_STATUS = {"22023": 422, "23505": 409, "55000": 409}


def _valid_name(value, rule: re.Pattern) -> bool:
    return isinstance(value, str) and bool(rule.fullmatch(value))


def _validate_metadata(metadata) -> dict | None:
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=422, detail="metadata must be a JSON object")
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


def _validate_request(body) -> tuple[str, str, dict | None, list[str] | None]:
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=422, detail="request body must be a JSON object"
        )
    if "fastq_url" in body and "sample" in body:
        raise HTTPException(
            status_code=422,
            detail="give an S3 folder or a sample, not both; a folder's sample comes from its files",
        )
    sample = body.get("sample")
    if not _valid_name(sample, SAMPLE_RULE):
        raise HTTPException(
            status_code=422, detail=f"sample must be a name of {SAMPLE_HELP}"
        )
    reference = body.get("reference")
    if not _valid_name(reference, REFERENCE_RULE):
        raise HTTPException(
            status_code=422, detail=f"reference must be a name of {REFERENCE_HELP}"
        )
    return (
        sample,
        reference,
        _validate_metadata(body.get("metadata")),
        _validate_sra_runs(body.get("sra_runs")),
    )


def check_folder(body) -> dict:
    """The folder check on its own, for the form: queues nothing."""
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=422, detail="request body must be a JSON object"
        )
    return s3_folder.check_folder(body.get("fastq_url"))


def trigger_run(body, user_id: str) -> dict:
    folder = None
    if isinstance(body, dict) and "fastq_url" in body:
        if body.get("sra_runs") is not None:
            raise HTTPException(
                status_code=422, detail="give SRA run IDs or an S3 folder, not both"
            )
        if "sample" in body:
            raise HTTPException(
                status_code=422,
                detail="give an S3 folder or a sample, not both; a folder's sample comes from its files",
            )
        # Checked again here: the folder may have changed since the form checked it.
        folder = s3_folder.check_folder(body["fastq_url"])
        body = {**body, "sample": folder["sample"]}
        del body["fastq_url"]
    sample, reference, metadata, sra_runs = _validate_request(body)

    args = {"p_sample": sample, "p_reference": reference, "p_requested_by": user_id}
    if metadata is not None:
        args["p_metadata"] = metadata
    if sra_runs is not None:
        args["p_sra_runs"] = sra_runs
    if folder is not None:
        args["p_fastq_url"] = folder["fastq_url"]
        args["p_fastq_files"] = folder["files"]

    client = app_client()
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
        "reference": reference,
        "run_key": f"{sample}__{reference}__{user_id}",
    }
    if sra_runs is not None:
        started["sra_runs"] = sra_runs
    if folder is not None:
        started["fastq_url"] = folder["fastq_url"]
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
