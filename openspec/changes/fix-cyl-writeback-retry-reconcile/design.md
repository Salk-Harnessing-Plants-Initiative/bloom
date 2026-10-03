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
- **Goal:** every dispatched scan still ends `'written'`, `'reused'` or `'failed'`. It does so
  within one poller cycle of its own Workflow ending, once the run has been fully dispatched and
  its poller is live (D4). That is no slower than today for a failed scan.
- **Non-goals:**
  - repairing rows already stuck (prod run 2; bloom#1035);
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

### D2. The poller closes each _settled_ Workflow's leftover rows in every non-terminal cycle

Before this change, `sweep_once` reconciled only when the run's rollup was non-`'running'`, so
it waited for every Workflow in the run. With D1, rows deferred by a Workflow whose write-back
used up its retries would wait for the slowest sibling, which can take hours. Before D1, those
rows were closed at the end of the final attempt.

**Change.**

- `_fetch_effective_phases` returns an `EffectivePhases` `NamedTuple`. Its sixth field,
  `settled_workflow_names`, lists the queued Workflow names that can write nothing more:
  - the phase is `Succeeded`, `Failed` or `Error`; or
  - it returns a 404, and every one of its `'queued'` rows was last updated longer ago than
    `WORKFLOWS_K8S_TTL_SECONDS` plus a 5-minute skew allowance. For a `'queued'` row, last updated
    means when `complete_cyl_pipeline_batch` stamped the Workflow name. A 404 that old can only
    be `ttlStrategy` garbage collection, which fires no sooner than the TTL after the Workflow
    finished. A younger 404 is left alone.
- In every cycle that writes no terminal status, `sweep_once` reconciles the settled names and
  then recounts. That covers a rollup of `'running'`, no conclusion at all (`None`), and a
  withheld `'complete'`. Without this, the review of PR #1038 found deferred rows stuck in two
  cases:
  - a run whose Workflows all 404 because the poller couldn't read them for longer than the
    TTL, as with a bad CA cert (#1019);
  - a run withheld from `'complete'`.
- When the rollup is terminal, it reconciles every queued name, including 404'd ones, exactly
  as before.
- **Failure handling.**
  - A terminal rollup still skips its status write when the reconcile or recount fails, so the
    run stays a candidate.
  - A `'running'` run still writes its status with the snapshot counts and marks the cycle
    unclean. It stays a candidate either way, so skipping the write would only freeze its
    progress counts.
  - `PGRST202` is quiet in both cases.
- Each reconcile logs how many rows it closed, so operators can see that the poller closed them
  rather than bloomctl.

**Why it is safe.**

- A Workflow's phase becomes `Succeeded`, `Failed` or `Error` only after every node has
  finished. That includes the write-back node with all its retries and the downstream
  `exit-gate`. So no ordinary attempt can still write.
- **One residual race.** Argo can mark an evicted pod's node `Failed` before its container
  exits, and an RPC the pod already sent still commits. If a poller cycle lands in that window,
  which is about one RPC long, it can close a row the RPC then writes, and the guard keeps it
  `'failed'`. Before this change, the same window existed only at run-terminal time.
- A 404'd Workflow's rows are closed only once garbage collection is the sole explanation. A
  misconfigured namespace can't close fresh rows early, because every row also has to be older
  than the TTL.
- The only thing that can restart a terminal Workflow is a manual `argo retry` (see Non-goals).

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
- **Ordering: the poller first.** The poller half alone is harmless: it only closes rows
  earlier for Workflows that are already settled. The bloomctl half is not harmless on its own.
  - Without the new poller, a deferred row in a multi-Workflow run waits for the slowest
    sibling, and an unconcluded or withheld run keeps it `'queued'`.
  - The template bump reaches prod at once, while the poller reaches prod only at the next
    staging→main promotion.
  - So tasks §6.3 waits until prod's workflows service runs this commit.

## Risks / Trade-offs

- **The real error isn't kept on the row.** A scan that write-back failed on its final attempt
  reads `'queued'` until the first poller cycle after its Workflow is settled. It then gets the
  poller's message: "…before write-back recorded a result for this scan; its result file may
  exist". The text was reworded from "produced a result" so it no longer claims nothing was
  produced.
  - The specific error (a DB rejection, a validation failure) only reached the write-back pod
    log, which Argo deletes with the Workflow after the TTL. Before this change the row got
    bloomctl's equally generic `NO_RESULT_MESSAGE`, so nothing is lost relative to that.
  - Recording per-scan errors on the row would need a new RPC parameter. That is out of scope.
- **More rests on the poller.** If the poller is down, deferred rows stay `'queued'` until it
  returns. Once it does, rows of Workflows garbage-collected in the meantime are closed too
  (D2). The run's status, its counts and every "write-back never ran" scan already depend on the
  poller in the same way.
- **Deterministic failures defer too** (D1). Their rows are closed by the poller with its
  message.
- **Closing rows doesn't unstick the run.** For a run whose Workflows all 404, or one withheld
  from `'complete'`, D2 closes the rows but the run's status stays as it was, as today. That
  stall is outside this change.
- **Undispatched batches hold everything back.** While some batches of a run are still waiting
  to be dispatched, the run isn't a polling candidate yet (`_settle_cyl_pipeline_run`), so its
  finished Workflows' rows wait too. If that lasts longer than the TTL, D2's 404 rule closes
  them once the run becomes a candidate.
- **One sibling's K8s error blocks the run.** It skips that whole run for the cycle, including
  its settled siblings' close-out. This isolation predates the change.
- **Shared input directories (srp#37).** Another run can write a `{scan_key}.result.json` into
  the shared hostPath directory between attempts. One sequence then still leaves a stale
  `'failed'` row:
  1. Attempt 1 finds declared `scan_9` missing, has no retriable envelope failure, and
     reconciles.
  2. A concurrent run writes `scan_9.result.json`.
  3. Attempt 2 ingests it.

  That is pre-existing cross-run contamination, and it needs overlapping runs of the same scan.
  It is out of scope here.

- **The guard's late-delivery protection is unchanged.**

## Migration Plan

No schema migration. The rollout is described in D4. Rolling back means reverting the poller
commit, re-pinning bloomctl, or both. Each half can be rolled back on its own.

## Open Questions

None.
