## Why

Argo's write-back step (`sleap-roots-write-back-template`, `retryStrategy: limit: 2, retryPolicy:
Always`) re-runs `bloomctl cyl batch-ingest-result` in the **same** Workflow when an attempt exits
non-zero. At the end of every attempt, the command closes out every scan of this Workflow that is
still `'queued'` as `'failed'` (`reconcile_unresolved_scans` →
`fail_cyl_pipeline_run_scans_without_result`, `bloomcli/src/bloomctl/cyl/ingest.py:1200-1205`). That
includes scans whose envelope failed retriably in this attempt, which a retry may still ingest.
When the retry does, `insert_cyl_result_envelope` writes the data but cannot mark the row
`'written'`: every status update there carries `AND status != 'failed'`, a deliberate guard
against resurrecting a closed scan. The row stays `'failed'` and the run page reports a failure
for a scan that has a result.

This happened in prod run 2 (Workflow `sleap-roots-pipeline-x68sv`, 2026-10-02): 5 of 24
envelopes hit the #1022 sequence bug on the first attempt, the retry ingested all 5 (sources
25–29), and the 5 rows stayed `'failed'` (bloom#1034). The #1022 fix (#1029) removes that trigger,
but any retriable write-back failure that a retry then fixes leaves the same stale rows.

The end-of-attempt reconcile also isn't needed as a backstop. `services/workflows/status_poller.py`
already closes out every leftover `'queued'` row with the same RPC, and does so only once the
Workflow's rollup is no longer `'running'`. Argo keeps a Workflow `Running` until the step's last
retry has finished.

## What Changes

- `bloomctl cyl batch-ingest-result` makes its reconciliation call only when no retry of the step
  could still write a result. That holds unless an envelope this invocation tried to ingest failed
  with `retriable: true`. A retry re-reads the same files, so only those envelopes' outcomes can
  change.
  - A missing manifest-declared scan_key, a missing run manifest, and a non-retriable envelope
    failure don't count. Their missing files come from earlier DAG steps that a retry of this
    step doesn't re-run.
  - So the call still runs on every clean exit, and on the no-manifest path with its specific
    `NO_RUN_MANIFEST_MESSAGE`.
- When an envelope failed retriably, the command makes **no** reconciliation call and leaves this
  Workflow's `'queued'` rows to the status poller. The exit code is unchanged.
- **No SQL change.** The `status != 'failed'` guard and `fail_cyl_pipeline_run_scans_without_result`
  stay exactly as they are, so there is no migration.
- Spec and doc updates:
  - `cyl-batch-ingest-result` gets the new reconcile condition.
  - `cyl-trait-writeback` updates bloomctl's call contract and drops a guard-scenario example that
    can no longer happen.
  - `cyl-pipeline-status-polling` notes that the poller now also closes out rows bloomctl deferred
    after its final attempt.
  - `bloomcli/README.md`, `services/workflows/README.md` and `bloomcli/CHANGELOG.md` are updated
    to match.

**Deployment.** The cluster runs the bloomctl image pinned in upstream
`sleap-roots-pipeline/sleap-roots-write-back-template.yaml`, so the fix is live only once that pin
is bumped to an image built from this merge and re-registered. That is a cross-repo follow-up,
tracked in `tasks.md` §5 and opened only with the maintainer's OK.

**Not included:**
- Repairing prod run 2's 5 stale rows (handled separately per #1034).
- Changing the guard to allow `'failed'` → `'written'` (option 2 on #1034).
- Passing Argo's retry count into the pod (option 1 as first framed). The predicate above needs no
  attempt number.

## Impact

- Affected specs: `cyl-batch-ingest-result`, `cyl-trait-writeback`, `cyl-pipeline-status-polling`
- Affected code: `bloomcli/src/bloomctl/cyl/ingest.py` (`batch_ingest_result`),
  `bloomcli/tests/test_cyl_ingest.py`, `tests/integration/test_cyl_writeback_rpc.py`, the READMEs
  and changelog above, and a docstring/comment in `services/workflows/status_poller.py`
- No migration, no change to the RPCs, the poller's behaviour or the vendored Workflow
- Behaviour change visible to operators: after a write-back step whose final attempt still had a
  retriable envelope failure, the affected scans read `'queued'` until the next poller cycle, then
  `'failed'` with the poller's message ("workflow reached a terminal status before write-back
  produced a result for this scan") instead of bloomctl's.
