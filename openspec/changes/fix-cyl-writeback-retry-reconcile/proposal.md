## Why

`bloomctl cyl batch-ingest-result` closes out every still-`'queued'` scan of its Workflow as
`'failed'` at the end of **every** attempt of the write-back step. Argo retries a failed attempt
in the same Workflow (`retryStrategy: limit: 2, retryPolicy: Always`). When a retry then ingests
a scan the earlier attempt had closed, `insert_cyl_result_envelope` writes the data, but its
`status != 'failed'` guard keeps the row `'failed'`. In prod run 2 (Workflow
`sleap-roots-pipeline-x68sv`, 2026-10-02), 5 of 24 scans have results but read as failures
(bloom#1034).

## What Changes

- **`bloomctl cyl batch-ingest-result` skips its reconciliation call when any envelope it tried
  to ingest failed with `retriable: true`.** Those are the only failures a retry of the step might
  turn into a write.
  - Missing declared files, a missing run manifest and non-retriable failures don't count. So the
    call still runs on every clean exit and on the no-manifest path, which keeps
    `NO_RUN_MANIFEST_MESSAGE`.
  - When it skips, it prints one line to stderr saying reconciliation was deferred to the status
    poller. The exit code and the summary/JSON output are unchanged.
- **`status_poller.py` closes a Workflow's leftover `'queued'` rows once that Workflow can
  write nothing more**, even while the run's status can't be written yet.
  - "Nothing more" means its own Argo phase is `Succeeded`, `Failed` or `Error`, or it 404s
    and its rows were dispatched longer ago than the Workflow TTL.
  - This applies while sibling Workflows still run, while no rollup can be concluded, and while
    `'complete'` is withheld.
  - Today it waits for the whole run's rollup to stop being `'running'`. A run is split into
    25-scan Workflows that can finish hours apart.
  - A `'running'` run still writes its progress when a close-out fails.
  - The backstop message now says write-back "recorded" no result and that the result file may
    exist.
- **No SQL change.** The guard and `fail_cyl_pipeline_run_scans_without_result` are untouched.
- **Docs, docstrings and the bloomcli changelog** are updated wherever they say the reconcile
  runs after every batch, or that a leftover `'queued'` row "can only mean write-back never ran".

The bloomctl half takes effect only once the upstream write-back template's image pin is bumped
(design D4, tasks §6). The poller half deploys with bloom.

**Not included:**

- Repairing prod run 2's 5 rows (bloom#1035).
- Relaxing the guard (#1034 option 2).
- Passing Argo's retry count into the pod (#1034 option 1 as first framed).
- Reclassifying deterministic envelope failures as non-retriable.
- Shared-directory cross-run contamination (srp#37).

## Impact

- **Affected specs:** `cyl-batch-ingest-result`, `cyl-trait-writeback`,
  `cyl-pipeline-status-polling`.
- **Affected code:**
  - `bloomcli/` (bloomctl):
    - `src/bloomctl/cyl/ingest.py`: `batch_ingest_result`, `reconcile_unresolved_scans` docstring
    - `src/bloomctl/cyl/_batch.py`: `ScanResult.retriable` docstring
    - `tests/test_cyl_ingest.py`
    - `README.md`
    - `CHANGELOG.md`
  - `services/workflows/` (status poller):
    - `status_poller.py`: `_fetch_effective_phases`, `sweep_once`, docstrings
    - `tests/test_status_poller.py`
    - `README.md`
  - `tests/integration/test_cyl_writeback_rpc.py`
  - `web/lib/cyl-pipeline/failure-hints.ts` (comment only)
- **No migration**, and no change to the RPCs or the vendored Workflow.
- **Behaviour visible to operators:**
  - A scan that a write-back retry ingests now ends `'written'`.
  - A scan that write-back still failed on its last attempt reads `'queued'` until the first
    poller cycle after its Workflow ends. It then reads `'failed'` with the poller's message
    ("workflow reached a terminal status before write-back recorded a result for this scan; its
    result file may exist") instead of bloomctl's.
  - In a multi-Workflow run, a Workflow's unresolved scans now settle when that Workflow ends,
    not when the last one does.
