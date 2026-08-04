"""
Trigger an A4 sleap-roots pipeline run for a set of scans (Phase 1 of bloom
#11/#404): validate the request, enumerate scans, compute an informational dedup
preview, write `cyl_pipeline_runs`/`cyl_pipeline_run_scans`, chunk into batches,
and enqueue each batch via `enqueue_cyl_pipeline_batch`. Uses a dedicated
least-privilege app user (see supabase_client.py) — the app user's grants and RLS
policies bound what this can touch.

This phase does NOT submit anything to Argo/Kubernetes — every enumerated scan is
always enqueued regardless of the dedup preview's outcome. See
openspec/changes/add-cyl-pipeline-trigger/design.md for why: the dedup preview
can only compare `params` (not model versions/code shas, which Bloom cannot
cheaply know before submission), so hard-skipping enqueue on a params-only match
would silently and permanently suppress recomputation after a model-version bump
with unchanged params. The real GPU-avoidance decision is made cluster-side by
the predict loop's own skip-if-done check, which does know current models.
"""

import json

from fastapi import HTTPException
from sleap_roots_contracts import compute_param_hash
from sleap_roots_contracts.hashing import NonCanonicalizableError
from supabase_client import app_client

VALID_TARGET_LEVELS = {"scan", "wave", "experiment", "scan_ids"}

# Scans per Argo batch — a plain constant, deliberately not yet an env var (see
# design.md's Open Questions: revisit if Phase 2 needs it configurable
# per-environment; ~25-50 per the canonical A4 design doc §6).
BATCH_SIZE = 25

# Both plain constants, not yet env vars (see design.md's Open Questions) — added
# after a PR review found neither `scan_ids` length nor `params` size was bounded
# anywhere in the request path.
MAX_SCAN_IDS = 5000
MAX_PARAMS_BYTES = 10_000


def _is_positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validate_params_and_hash(params) -> str:
    """Validate params are an object, bounded in size, and hashable — then return
    the hash. Computed once, up front, before app_client() is ever called (cheap
    rejection, matching every other validated field), and threaded through to
    _dedup_preview instead of being recomputed there."""
    if not isinstance(params, dict):
        raise HTTPException(status_code=422, detail="params must be an object")
    try:
        size = len(json.dumps(params))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail="params must be JSON-serializable"
        ) from exc
    if size > MAX_PARAMS_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"params must serialize to at most {MAX_PARAMS_BYTES} bytes",
        )
    try:
        return compute_param_hash(params)
    except (NonCanonicalizableError, TypeError, ValueError, RecursionError) as exc:
        raise HTTPException(
            status_code=422,
            detail="params must contain only finite, JSON-serializable values",
        ) from exc


def _validate_request(body: dict):
    target_level = body.get("target_level")
    if target_level not in VALID_TARGET_LEVELS:
        raise HTTPException(
            status_code=422,
            detail=f"target_level must be one of {sorted(VALID_TARGET_LEVELS)}",
        )

    params = body.get("params")
    if params is None:
        params = {}
    param_hash = _validate_params_and_hash(params)

    scan_ids = body.get("scan_ids")
    target_id = body.get("target_id")

    if target_level == "scan_ids":
        if target_id is not None:
            raise HTTPException(
                status_code=422,
                detail="target_id must be null when target_level is scan_ids",
            )
        if not isinstance(scan_ids, list) or not scan_ids:
            raise HTTPException(
                status_code=422,
                detail="scan_ids must be a non-empty array when target_level is scan_ids",
            )
        if len(scan_ids) > MAX_SCAN_IDS:
            raise HTTPException(
                status_code=422,
                detail=f"scan_ids must contain at most {MAX_SCAN_IDS} entries",
            )
        for sid in scan_ids:
            if not _is_positive_int(sid):
                raise HTTPException(
                    status_code=422, detail="scan_ids entries must be positive integers"
                )
        # Order-preserving dedup — a client-supplied duplicate is deduped, not
        # rejected (see design.md's "Decision: scan_ids are deduplicated").
        deduped = list(dict.fromkeys(scan_ids))
        return target_level, None, deduped, params, param_hash

    if scan_ids is not None:
        raise HTTPException(
            status_code=422,
            detail="scan_ids must be absent unless target_level is scan_ids",
        )
    if not _is_positive_int(target_id):
        raise HTTPException(
            status_code=422, detail="target_id must be a positive integer"
        )
    return target_level, target_id, None, params, param_hash


def _enumerate(
    client, target_level: str, target_id: int, scan_ids: list[int] | None
) -> list[int]:
    if target_level == "scan":
        rows = (
            client.table("cyl_scans_extended")
            .select("scan_id")
            .eq("scan_id", target_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not rows:
            raise HTTPException(status_code=404, detail=f"scan {target_id} not found")
        return [target_id]

    if target_level == "wave":
        exists = (
            client.table("cyl_waves")
            .select("id")
            .eq("id", target_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not exists:
            raise HTTPException(status_code=404, detail=f"wave {target_id} not found")
        rows = (
            client.table("cyl_scans_extended")
            .select("scan_id")
            .eq("wave_id", target_id)
            .order("scan_id")
            .execute()
            .data
            or []
        )
        return [r["scan_id"] for r in rows]

    if target_level == "experiment":
        exists = (
            client.table("cyl_experiments")
            .select("id")
            .eq("id", target_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not exists:
            raise HTTPException(
                status_code=404, detail=f"experiment {target_id} not found"
            )
        rows = (
            client.table("cyl_scans_extended")
            .select("scan_id")
            .eq("experiment_id", target_id)
            .order("scan_id")
            .execute()
            .data
            or []
        )
        return [r["scan_id"] for r in rows]

    # scan_ids
    found = (
        client.table("cyl_scans_extended")
        .select("scan_id")
        .in_("scan_id", scan_ids)
        .execute()
        .data
        or []
    )
    found_ids = {r["scan_id"] for r in found}
    missing = [s for s in scan_ids if s not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"scan_ids not found: {missing}")
    return list(scan_ids)


def _dedup_preview(client, scan_ids: list[int], request_hash: str) -> set[int]:
    """Which of `scan_ids` have at least one cyl_trait_sources row whose stored
    param_hash matches the request's params — informational only, see module
    docstring. One batched query per table, not a per-scan loop. `request_hash` is
    computed once in `_validate_request` and threaded through here rather than
    recomputed."""
    trait_rows = (
        client.table("cyl_scan_traits")
        .select("scan_id, source_id")
        .in_("scan_id", scan_ids)
        .execute()
        .data
        or []
    )
    source_ids = sorted(
        {r["source_id"] for r in trait_rows if r.get("source_id") is not None}
    )
    if not source_ids:
        return set()

    source_rows = (
        client.table("cyl_trait_sources")
        .select("id, metadata")
        .in_("id", source_ids)
        .execute()
        .data
        or []
    )
    matching_source_ids = {
        row["id"]
        for row in source_rows
        if ((row.get("metadata") or {}).get("params") or {}).get("param_hash")
        == request_hash
    }
    return {
        r["scan_id"] for r in trait_rows if r.get("source_id") in matching_source_ids
    }


def _chunk(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _insert_run(
    client,
    target_level,
    target_id,
    params,
    requested_by,
    *,
    scan_count,
    reused_count,
    status,
) -> int:
    row = {
        "target_level": target_level,
        "target_id": target_id,
        "params": params,
        "requested_by": requested_by,
        "status": status,
        "scan_count": scan_count,
        "reused_count": reused_count,
    }
    res = client.table("cyl_pipeline_runs").insert(row).execute()
    return res.data[0]["id"]


def trigger_pipeline(body: dict, user_id: str) -> dict:
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=422, detail="request body must be a JSON object"
        )

    target_level, target_id, scan_ids_in, params, param_hash = _validate_request(body)
    client = app_client()
    scan_ids = _enumerate(client, target_level, target_id, scan_ids_in)
    scan_count = len(scan_ids)

    if scan_count == 0:
        run_id = _insert_run(
            client,
            target_level,
            target_id,
            params,
            user_id,
            scan_count=0,
            reused_count=0,
            status="complete",
        )
        return {"pipeline_run_id": run_id, "scan_count": 0, "reused_count": 0}

    reused_scan_ids = _dedup_preview(client, scan_ids, param_hash)
    reused_count = len(reused_scan_ids)

    run_id = _insert_run(
        client,
        target_level,
        target_id,
        params,
        user_id,
        scan_count=scan_count,
        reused_count=reused_count,
        status="queued",
    )

    batches = _chunk(scan_ids, BATCH_SIZE)
    scan_rows = [
        {
            "run_id": run_id,
            "scan_id": sid,
            "batch_index": batch_index,
            "status": "queued",
        }
        for batch_index, batch in enumerate(batches)
        for sid in batch
    ]
    client.table("cyl_pipeline_run_scans").insert(scan_rows).execute()

    for batch_index, batch in enumerate(batches):
        client.rpc(
            "enqueue_cyl_pipeline_batch",
            {"p_run_id": run_id, "p_batch_index": batch_index, "p_scan_ids": batch},
        ).execute()

    return {
        "pipeline_run_id": run_id,
        "scan_count": scan_count,
        "reused_count": reused_count,
    }
