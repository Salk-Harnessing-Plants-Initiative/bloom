"""
Cell Ranger trigger: list the samples and references in the bucket, validate a
run request against them, and create the run with `request_scrna_cellranger_run`.

A run is one sample (one folder of FASTQs under raw_reads/) and one reference.
"""

import re

from fastapi import HTTPException

import scrna_s3
from supabase_client import app_client

# Allowed sample and reference names ('__' separates run_key parts); the database checks the same rule.
NAME_RULE = re.compile(r"^(?!.*__)[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
NAME_HELP = (
    "letters, digits, '.', '_' or '-', starting with a letter or digit, "
    "with no '__' (at most 100)"
)

# Most folders returned by one inputs listing.
MAX_LISTED_FOLDERS = 200

RUNS_TABLE = "scrna_cellranger_runs"
REQUEST_FN = "request_scrna_cellranger_run"


def _valid_name(value) -> bool:
    return isinstance(value, str) and bool(NAME_RULE.match(value))


def list_inputs() -> dict:
    """Samples (with FASTQ count and size) and references available in the bucket."""
    s3 = scrna_s3.client()

    sample_names, samples_truncated = scrna_s3.list_sample_folders(
        s3, MAX_LISTED_FOLDERS
    )
    samples = []
    unusable = []
    for name in sample_names:
        if not _valid_name(name):
            unusable.append(name)
            continue
        count, size = scrna_s3.sample_fastqs(s3, name)
        samples.append({"name": name, "fastq_count": count, "total_bytes": size})

    reference_names, references_truncated = scrna_s3.list_reference_folders(
        s3, MAX_LISTED_FOLDERS
    )
    references = [
        name
        for name in reference_names
        if _valid_name(name) and scrna_s3.reference_exists(s3, name)
    ]

    return {
        "samples": samples,
        "references": references,
        "unusable_sample_folders": unusable,
        "samples_truncated": samples_truncated,
        "references_truncated": references_truncated,
    }


def _validate_request(body) -> tuple[str, str]:
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=422, detail="request body must be a JSON object"
        )
    sample = body.get("sample")
    if not _valid_name(sample):
        raise HTTPException(
            status_code=422, detail=f"sample must be a name of {NAME_HELP}"
        )
    reference = body.get("reference")
    if not _valid_name(reference):
        raise HTTPException(
            status_code=422, detail=f"reference must be a name of {NAME_HELP}"
        )
    return sample, reference


def trigger_run(body, user_id: str) -> dict:
    sample, reference = _validate_request(body)

    s3 = scrna_s3.client()
    if not scrna_s3.reference_exists(s3, reference):
        raise HTTPException(
            status_code=404,
            detail=f"no Cell Ranger reference at reference_genome/{reference}/ (expected reference.json)",
        )
    if scrna_s3.sample_fastqs(s3, sample)[0] == 0:
        raise HTTPException(status_code=404, detail=f"no FASTQs at raw_reads/{sample}/")

    client = app_client()
    run_id = (
        client.rpc(
            REQUEST_FN,
            {"p_sample": sample, "p_reference": reference, "p_requested_by": user_id},
        )
        .execute()
        .data
    )

    return {
        "run_id": run_id,
        "sample": sample,
        "reference": reference,
        "run_key": f"{sample}__{reference}__{user_id}",
    }


def get_run(run_id: int) -> dict:
    """The run's row."""
    client = app_client()
    rows = (
        client.table(RUNS_TABLE).select("*").eq("id", run_id).limit(1).execute().data
        or []
    )
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"Cell Ranger run {run_id} not found"
        )
    return rows[0]
