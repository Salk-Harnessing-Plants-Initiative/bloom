"""
Queue helpers for the pipeline dispatch worker.

Everything goes through the public SECURITY DEFINER wrapper functions
(claim_cyl_pipeline_batch / complete_cyl_pipeline_batch /
fail_cyl_pipeline_batch), called as the bloom_workflows app user via the
Supabase client — so the worker needs no direct DB connection or pgmq grants.
Matches video_queue.py's thin-wrapper style (bloom PR #469, unmerged —
referenced as a structural template, not a code dependency).
"""

import os

VISIBILITY_TIMEOUT = int(os.environ.get("WORKFLOWS_DISPATCH_VT_SECONDS", "60"))
MAX_READS = int(os.environ.get("WORKFLOWS_DISPATCH_MAX_READS", "5"))


def claim_batch(client, vt: int = VISIBILITY_TIMEOUT, max_reads: int = MAX_READS):
    """Claim the next queued batch (or None if the queue is empty, or the
    message was a poison message dead-lettered by the claim function itself)."""
    rows = (
        client.rpc("claim_cyl_pipeline_batch", {"p_vt": vt, "p_max_reads": max_reads})
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def complete_batch(
    client,
    run_id,
    batch_index: int,
    msg_id,
    scan_ids: list[int],
    argo_workflow_name: str,
) -> None:
    client.rpc(
        "complete_cyl_pipeline_batch",
        {
            "p_run_id": run_id,
            "p_batch_index": batch_index,
            "p_msg_id": msg_id,
            "p_scan_ids": scan_ids,
            "p_argo_workflow_name": argo_workflow_name,
        },
    ).execute()


def fail_batch(
    client, run_id, batch_index: int, msg_id, scan_ids: list[int], error: str
) -> None:
    client.rpc(
        "fail_cyl_pipeline_batch",
        {
            "p_run_id": run_id,
            "p_batch_index": batch_index,
            "p_msg_id": msg_id,
            "p_scan_ids": scan_ids,
            "p_error": error,
        },
    ).execute()
