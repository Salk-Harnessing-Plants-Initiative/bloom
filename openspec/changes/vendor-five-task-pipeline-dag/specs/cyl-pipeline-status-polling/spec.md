## ADDED Requirements

### Requirement: A `'complete'` rollup does not imply every scan produced a result

The rollup SHALL continue to compute run status from the *effective phases* defined by the rollup
rule — the real Argo phase per distinct workflow, plus a synthesized `'Failed'` for scans whose
dispatch failed and that have no workflow at all — and callers SHALL NOT infer per-scan completeness
from the result. Since the pipeline DAG gained its terminal exit gate, a Workflow phase of
`Succeeded` means "the gate accepted the producers' exit codes", which includes the partial-success
code `3` — so rule (2)'s `'complete'` is reachable for a run in which individual scans genuinely
failed. The poller SHALL therefore keep recording real per-scan outcomes in
`done_count`/`failed_count` independently of the phase-derived status, and no consumer of
`cyl_pipeline_runs.status` may treat `'complete'` as equivalent to `failed_count = 0`.

Three combinations follow. None is a defect in the rollup:

1. **`'complete'` with `failed_count > 0`** — reachable when `images-downloader` isolates some
   scans' failures and stages the rest, and the failed scans' keys are not already carried in the
   shared `RunManifest` from an earlier run. It is specific to that stage: a scan isolated later, by
   `predictor` or `trait-extractor`, is already recorded in the manifest, so write-back finds a
   declared `scan_key` with no result and exits non-zero, the gate is omitted, and a single-batch
   run reads `'failed'` (a multi-batch run whose other batches succeeded reads `'partial'`).
2. **`'complete'` with `done_count = 0`** — the producers' exit `3` has no floor, so a batch in
   which every scan failed exits the same code as one in which a single scan failed. A
   totally-failed batch is therefore indistinguishable from a partially-failed one by status alone.
3. **`'failed'` with `done_count > 0`** — **already reachable before the exit gate**, because each
   envelope's per-scan `'written'` update commits in that envelope's own transaction, so any
   write-back that ingested some envelopes and then exited non-zero produced it. The gate does not
   create this combination; it adds a **second route** to it, by rejecting a producer exit code
   outside `{0,3}` after write-back has already committed results. Consumers must not read
   `'failed'` as "nothing was written".

**Known bound on the "read `failed_count`, not `status`" instruction.** Rule (2) withholds
`'complete'` when any of the run's workflows returned 404 this cycle, and skips the run entirely
rather than writing partial information. A TTL-GC'd workflow 404s permanently, so a multi-batch run
whose batches finish more than `WORKFLOWS_K8S_TTL_SECONDS` apart can reach a state where the
counters are never written at all. This change does not create that behaviour, but it routes more
runs into it: batches that previously ended `Failed` and settled now end `Succeeded`, so runs that
used to roll up to a terminal status now roll up all-`Succeeded` and meet the withhold condition.

Pipeline-level `'partial'` (rule (4)) is unchanged in definition but narrowed in practice: it no
longer arises from partial failure *within* a batch. It still arises from terminal effective phases
differing across a run — whole batch Workflows ending differently, or a dispatch-failed batch that
never had a Workflow at all alongside one that succeeded.

#### Scenario: A downloader-stage isolation still rolls up to complete

- **WHEN** a run's only batch has `images-downloader` exit `3` after isolating one scan, and the
  exit gate accepts that code so the Workflow phase is `Succeeded`
- **THEN** the rollup returns `'complete'`
- **AND** the run's `failed_count` is non-zero, recorded from real per-scan status rather than
  inferred from the phase

#### Scenario: A failed run may have written results

- **WHEN** write-back commits results for some scans and the Workflow then ends `Failed` — whether
  because write-back itself exited non-zero and the gate was omitted, or because the gate rejected a
  producer's exit code after write-back had already committed
- **THEN** the rollup returns `'failed'`
- **AND** `done_count` is non-zero, so a consumer must not treat `'failed'` as "nothing was written"

#### Scenario: An infrastructure failure of the gate itself fails a fully successful run

- **WHEN** every scan in a run is written successfully but the `exit-gate` pod never reaches a
  terminal success — it is preempted and the node reports `Error`, or it stays `Pending` past the
  template's timeout across its retries
- **THEN** the rollup returns `'failed'` with `done_count` equal to the run's scan count and
  `failed_count` of `0` — a row that contradicts itself
- **AND** no cause is recorded in `error_message`, and the Workflow object carrying the real cause is
  TTL-GC'd, so an automated consumer that re-dispatches on `'failed'` will re-dispatch a run whose
  every scan already succeeded
