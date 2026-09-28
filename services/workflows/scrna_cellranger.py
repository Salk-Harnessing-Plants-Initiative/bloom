"""
Cell Ranger trigger: validate a run request and create the run with
`request_scrna_cellranger_run`.

A run is one sample (one folder of FASTQs under raw_reads/) and one reference. The
pipeline checks that the reference and the FASTQs exist and fails the run if not.
"""

import re

from fastapi import HTTPException

from supabase_client import app_client

# Allowed sample and reference names ('__' separates run_key parts); the database checks the same rule.
NAME_RULE = re.compile(r"^(?!.*__)[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
NAME_HELP = (
    "letters, digits, '.', '_' or '-', starting with a letter or digit, "
    "with no '__' (at most 100)"
)

RUNS_TABLE = "scrna_cellranger_runs"
REQUEST_FN = "request_scrna_cellranger_run"


def _valid_name(value) -> bool:
    return isinstance(value, str) and bool(NAME_RULE.match(value))


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
