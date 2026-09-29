"""
Cell Ranger trigger: validate a run request and create the run with
`request_scrna_cellranger_run`.

A run is one sample (one folder of FASTQs under raw_reads/) and one reference. The
pipeline checks that the reference and the FASTQs exist and fails the run if not.
An optional `metadata` object (the dataset's species, name, conditions) is stored on the
run as given, for loading the results later.
"""

import json
import re

from fastapi import HTTPException

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


def _validate_request(body) -> tuple[str, str, dict | None]:
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=422, detail="request body must be a JSON object"
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
    return sample, reference, _validate_metadata(body.get("metadata"))


def trigger_run(body, user_id: str) -> dict:
    sample, reference, metadata = _validate_request(body)

    args = {"p_sample": sample, "p_reference": reference, "p_requested_by": user_id}
    if metadata is not None:
        args["p_metadata"] = metadata

    client = app_client()
    run_id = client.rpc(REQUEST_FN, args).execute().data

    return {
        "run_id": run_id,
        "sample": sample,
        "reference": reference,
        "run_key": f"{sample}__{reference}__{user_id}",
    }


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
