## Context

Three components decide a cylinder scan's final `cyl_pipeline_run_scans.status`:

| Component                          | When it runs today                                       | What it does to the row                                                                                 |
| ---------------------------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `insert_cyl_result_envelope`       | Once per envelope, in each write-back attempt            | `'queued'` → `'written'`, guarded by `AND status != 'failed'`                                           |
| `bloomctl cyl batch-ingest-result` | At the end of **every** write-back attempt               | `fail_cyl_pipeline_run_scans_without_result`: `'queued'` → `'failed'`                                   |
| `status_poller.py` `sweep_once`    | Once the whole **run's** rollup is no longer `'running'` | The same RPC for each Workflow name with a leftover `'queued'` row, then a recount and the status write |

Argo retries the write-back step (`retryStrategy: limit: 2, retryPolicy: Always`) in the same
Workflow, against the same rows. The guard exists so that a delivery arriving after a row was
closed can't flip it back, "since the parent run may have already gone terminal"
(`20260912110000_add_cyl_writeback_run_scan_status.sql:37-44`). bloom#1034 is the case where the
close and the later delivery both come from one still-running Workflow.

The dispatcher splits a run into 25-scan Workflows (`services/workflows/pipeline.py`
`BATCH_SIZE`), and these can finish hours apart.

## Goals / Non-Goals

- **Goal:** a scan that a later attempt of the same write-back step ingests ends `'written'`.
- **Goal:** every dispatched scan still ends `'written'`, `'reused'` or `'failed'`, and it does
  so within one poller cycle of its own Workflow ending. That is no slower than today for a
  failed scan.
- **Non-goals:**
  - repairing rows already stuck (prod run 2; separate issue);
  - changing the guard or either RPC;
  - an `argo retry` of a Workflow that has already failed. The poller has closed its rows by
    then, and the guard keeps them closed, as it does today;
  - reclassifying deterministic envelope failures, which are `retriable: true` today;
  - cross-run writes into the shared input directories (srp#37).

## Decisions

### D1. bloomctl reconciles only when no attempted envelope failed retriably

`batch_ingest_result` skips its reconciliation call when any `ScanResult` produced by
`ingest_one_envelope` (an envelope file it actually tried to ingest) is failed with
`retriable: true`. Otherwise it reconciles as today. The flag is computed over those results
only, so it is `False` when nothing was ingested (the no-manifest and all-declared-missing
paths).

**The predicate is conservative, not exact.** Only those envelopes can change on a retry,
because the files and manifest a retry reads come from earlier DAG steps that a step-level
retry doesn't re-run. Missing declared files and a missing run manifest are `retriable: true`,
but a retry can't change them. Today every `ingest_one_envelope` failure except the
`status_update_matched` mismatch is `retriable: true` (`_batch.py` `ScanResult`), and that
includes deterministic ones: contract validation, a missing `idempotency_key`,
`BlobConstructionError`, and an RPC rejection. Those also defer. Deferring is always safe,
because the poller closes the rows (D2). Its only cost is the poller's generic message in place
of bloomctl's.

**Deferral has to be visible in the Argo pod log.** bloomctl installs no logging handler, so a
`logger.info` never reaches the pod log (`_batch.py` already notes the log sink is unreadable
there). The command therefore prints one stderr line through `click.echo(..., err=True)`:
`reconciliation deferred to the status poller: N envelope(s) failed retriably`. stdout's
summary/`--json` output and the exit code don't change (D3).

Rejected alternatives:

- **"Reconcile only when exiting 0".** This would also defer the no-manifest and missing-file
  paths, which always exit 1. `NO_RUN_MANIFEST_MESSAGE` and its re-dispatch hint would become
  dead code, for no benefit.
- **Reconcile on the final attempt using `{{retries}}`.** That needs an upstream template change
  and a new input. It still leaves the poller as the backstop for a killed final attempt, and
  adds nothing over D1.
- **Let a Workflow overwrite its own `'failed'` rows (#1034 option 2).** That means redefining
  the roughly 300-line `insert_cyl_result_envelope`, which
  `test_cyl_noop_redelivery_migration_files.py` pins, and weakening a guard that four scenarios
  rely on.

### D2. The poller closes each Workflow's leftover rows once that Workflow is terminal

Today `sweep_once` reconciles only when the run's rollup is non-`'running'`, so it waits for every
Workflow in the run. With D1, rows deferred by a Workflow whose write-back exhausted its retries
would wait for the slowest sibling, which can take hours. Before D1, those rows were closed at the
end of the final attempt.

**Change.**

- `_fetch_effective_phases` additionally returns each Workflow name's phase, with `None` for a 404.
- When the rollup is `'running'`, `sweep_once` reconciles every Workflow name that both has a
  leftover `'queued'` row and has a **confirmed** terminal phase (`Succeeded`, `Failed` or
  `Error`).
- When the rollup is non-`'running'`, it reconciles every queued name, including 404'd ones,
  exactly as today.
- After any reconcile it recounts with `_count_done_and_failed` before the status write.
- **Failure handling is unchanged.** A failed reconcile or recount skips that run's status write
  this cycle, marks the cycle unclean, and retries next cycle. `PGRST202` is quiet, as today.

**Why it is safe.**

- A Workflow's phase becomes `Succeeded`, `Failed` or `Error` only after every node has
  finished. That includes the write-back node with all its retries and the downstream
  `exit-gate`. So no attempt can still write.
- While the run is `'running'`, 404'd Workflows are left to the run-level backstop. A 404 isn't
  a confirmed phase, and the existing addendum-8 reasoning applies only once the rollup has
  concluded.
- The status write already happens every cycle, so the run's `done_count`/`failed_count`
  reflect the newly closed rows at once.
- The only thing that can restart a terminal Workflow is a manual `argo retry`. Today, that
  Workflow's rows are closed by the run-level backstop once the run settles anyway, so this
  doesn't change the outcome for a retried Workflow (see Non-goals).

### D3. bloomctl's exit code and output are unchanged

A deferring batch already contains a retriable failure, so it exits 1, and no `<reconciliation>`
entry is added. A reconciliation failure on a non-deferring batch is still a retriable synthetic
entry. Its retry re-ingests already-written envelopes as no-ops: the primary
`(argo_workflow_name, source_id)` update still matches the `'written'` row, so
`status_update_matched` stays `true`. It then reconciles again, idempotently.

### D4. Rollout

- **Poller (bloom-side).** It deploys with the workflows service on the staging push, and
  promotes with the next staging→main cut.
- **bloomctl (cluster-side).** The cluster runs the image pinned in the upstream
  sleap-roots-pipeline templates. `docker-build-bloomcli.yml` publishes `sha-<squash short>` on
  the staging push. Upstream bumps all bloomctl-pinned templates to one build together; the
  last time was `fix-cyl-redelivery-blob-collision` §9.5. This change follows that convention.
- **The bump switches prod and staging at once.** Both submit to the shared `runai-busch-lab`
  namespace and resolve the same `templateRef`. So the bump ships every bloomctl commit on staging
  up to that merge. §6 re-checks `origin/main..origin/staging -- bloomcli` at bump time.
- **Rollback is a re-pin** to `sha-88cbcbf@sha256:0259ec0a…` plus re-registration.
- **Ordering.** The poller half alone is harmless: it only closes rows earlier for Workflows that
  are already terminal. So the two halves can land in either order.

## Risks / Trade-offs

- **Failed scans show a different message.** A scan that write-back failed on its final attempt
  reads `'queued'` until the first poller cycle after its Workflow ends, then gets the poller's
  generic message. That's acceptable: it is the same moment the run's counts update.
- **More rests on the poller.** If the poller is down, deferred rows stay `'queued'`. But the
  run's status, its counts, and every "write-back never ran" scan already depend on the poller
  in the same way.
- **Deterministic failures defer too** (D1). Their rows are closed by the poller with its
  message.
- **Shared input directories (srp#37).** Another run can write a `{scan_key}.result.json` into
  the shared hostPath directory between attempts. One sequence then still leaves a stale
  `'failed'` row:
  1. Attempt 1 finds declared `scan_9` missing, has no retriable envelope failure, and
     reconciles.
  2. A concurrent run writes `scan_9.result.json`.
  3. Attempt 2 ingests it.

  That is pre-existing cross-run contamination, and it needs overlapping runs of the same scan.
  It is out of scope here.

- **A run already stuck behind a withheld `'complete'` now also keeps its deferred rows
  `'queued'`.** This needs all of the following:
  - the deferring Workflow is TTL-garbage-collected (a 404) before the poller ever sees its
    `Failed` phase, which takes poller downtime longer than the TTL;
  - every sibling Workflow `Succeeded`.

  The rollup is then `'complete'` with an unknown, which is withheld every cycle, so nothing
  reconciles. The run staying `'running'` happens today too. What's new is only that its
  deferred rows also stay `'queued'` instead of being closed by bloomctl. Closing a 404'd
  Workflow's rows while the run is `'running'` is excluded on purpose (D2), so this is left to
  whatever fixes the withheld-`'complete'` stall.

- **A reconcile call that keeps failing now affects more.** If it fails for a terminal
  Workflow in a `'running'` run, it skips that run's progress write every cycle until it
  succeeds. That is the same isolation as the terminal-rollup case, which until now could only
  happen once per run.
- **The guard's late-delivery protection is unchanged.**

## Migration Plan

No schema migration. The rollout is described in D4. Rolling back means reverting the poller
commit, re-pinning bloomctl, or both. Each half can be rolled back on its own.

## Open Questions

None.
