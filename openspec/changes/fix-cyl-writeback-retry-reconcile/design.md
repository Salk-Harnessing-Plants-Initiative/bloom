## Context

Three components decide a cylinder scan's final `cyl_pipeline_run_scans.status`:

| Component | When it runs | What it does to the scan's row |
| --- | --- | --- |
| `insert_cyl_result_envelope` | Once per envelope, during each write-back attempt | `'queued'` → `'written'`, guarded by `AND status != 'failed'` |
| `bloomctl cyl batch-ingest-result` | At the end of **every** write-back attempt | Calls `fail_cyl_pipeline_run_scans_without_result`, which turns `'queued'` → `'failed'` |
| `status_poller.py` | Once the Workflow's rollup is no longer `'running'` | Calls the same RPC for every name with a leftover `'queued'` row, then writes the run's status and counts |

Argo's write-back template has `retryStrategy: limit: 2, retryPolicy: Always`, so a non-zero exit
re-runs the pod up to twice in the same Workflow, against the same rows. The guard's original
rationale (`20260912110000_add_cyl_writeback_run_scan_status.sql:37-43`) is that a late delivery
must not flip a closed row back, because "the parent run may have already gone terminal and
dropped out of status_poller.py's candidate-run query for good". bloom#1034 is the one case where
the closing and the later delivery belong to the same, still-running Workflow.

## Goals / Non-Goals

- **Goal:** a scan that a later attempt of the same write-back step ingests ends `'written'`.
- **Goal:** keep every closing guarantee. Each scan this Workflow dispatched still ends
  `'written'`, `'reused'` or `'failed'` once the Workflow is terminal.
- **Non-goal:** repairing rows already stuck this way (prod run 2).
- **Non-goal:** any change to the guard, the RPCs or the poller's behaviour.

## Decisions

### D1. Reconcile only when no retry can still write a result

`batch_ingest_result` skips its reconciliation call when any `ScanResult` from
`ingest_one_envelope` (an envelope file it actually tried to ingest) has `status == "failed"` and
`retriable == True`. In every other case it reconciles exactly as today.

This predicate is exact for the question "could a retry of this step still write a result?":

- A retry runs the same container against the same `/workspace/input` and `/workspace/predictions`
  volumes. Those are produced by earlier DAG steps, which a step-level retry doesn't re-run. So
  the retry sees the same envelope files and the same manifest.
- An envelope that ingested, or was a no-op, is already `'written'`. Reconciling never touches it.
- A non-retriable envelope failure, such as a `status_update_matched: false` mismatch, gets the
  same answer on retry by definition (`_batch.py:28-40`).
- A missing manifest-declared file or a missing run manifest can't appear on a retry. Both are
  marked `retriable: true` today only so that the step, and with it the Workflow, ends `Failed`
  (`ingest.py:1131-1141`). They are not marked that way because a retry could fix them.
- Only a retriable envelope failure, such as a transient RPC, network, upload or #1022-style
  constraint error, can turn into a write on the next attempt.

Rejected: **"reconcile only when exiting 0"**, the first framing. It is equivalent except that it
also defers on the no-manifest path and on missing declared files. Those always exit 1, so the
in-pod reconcile would never run for them. That would turn `NO_RUN_MANIFEST_MESSAGE` and its
"re-dispatch the run" hint into dead code, and the scans would wait for the poller's generic
message for no benefit.

Rejected: **pass `{{retries}}` into the pod and reconcile on the final attempt** (#1034 option 1
as written). It needs an upstream template change plus a new bloomctl input. It still mis-closes
scans when the final attempt is cut short by a pod kill, where the poller is the backstop anyway.
And it adds nothing over D1.

Rejected: **let the same Workflow overwrite its own `'failed'`** (#1034 option 2). It redefines
the roughly 300-line `insert_cyl_result_envelope` body, which `test_cyl_noop_redelivery_migration_files.py`
pins for equality. It weakens a guard four spec scenarios rely on. And it needs a run-terminal
check inside the RPC. D1 removes the cause without touching SQL.

### D2. The poller is the closer of last resort, and that is already true

The poller already reconciles leftover `'queued'` rows before every non-`'running'` status write
(`status_poller.py:385-420`). It re-derives counts afterwards and holds the run as a candidate
when the call fails. `rollup()` returns `'running'` while any phase is Pending or Running, and
Argo reports the Workflow `Running` until the write-back node, including its retries, and the
`exit-gate` leaf have finished. So the poller can't race an in-flight attempt, and a deferred row
is always closed once the Workflow ends. Nothing in the poller changes. Its spec text and README
are updated because they say a `'queued'` row at that point "can only mean write-back never ran",
which is no longer the only cause.

### D3. Exit code and output are unchanged

When reconciliation is deferred, the batch already contains a retriable failure, so it already
exits 1, and no `<reconciliation>` entry is added. A reconciliation-call failure on a
non-deferred batch is still a retriable synthetic entry, so the step is retried. The retry's
re-ingest of already-written envelopes is a no-op that the primary
`(argo_workflow_name, source_id)` update still matches, and its reconcile is idempotent.

### D4. Deployment needs an upstream pin bump

The cluster runs `bloomctl:sha-<sha>@sha256:…` pinned in upstream
`sleap-roots-write-back-template.yaml`, not anything bloom deploys. Until that pin moves to an
image built from this merge and the template is re-registered, prod and staging keep the old
behaviour. `tasks.md` §5 covers verifying the image, the cross-repo PR (opened only with the
user's OK), and evidence that it took effect. The change is not archived before then.

## Risks / Trade-offs

- **Deferred rows read `'queued'` a little longer.** After a final attempt that still failed
  retriably, they stay `'queued'` until the next poller cycle, and then read `'failed'` with the
  poller's generic message instead of bloomctl's. That is acceptable: the run itself also reads
  `'running'` until that same cycle.
- **The poller now carries more of the load.** If the poller is down, deferred rows stay
  `'queued'`. But the run's terminal status, counts, and every "write-back never ran" scan already
  depend on the poller in exactly the same way. This adds no new single point of failure.
- **The guard's late-delivery protection is unchanged.** A delivery after the poller has closed a
  row still can't resurrect it, which is the guard's original purpose.
- **Unreadable or malformed envelope files** are retriable today, although a retry rereads the
  same bytes, so they now defer reconciliation too. Their rows are closed by the poller after the
  retries. Reclassifying them as non-retriable is a separate exit-code question, out of scope.

## Migration Plan

No schema migration. Rollout follows D4. Rollback means reverting the bloomctl change and the pin
bump. Rows left `'queued'` by a deferred attempt are closed by the poller either way.

## Open Questions

None.
