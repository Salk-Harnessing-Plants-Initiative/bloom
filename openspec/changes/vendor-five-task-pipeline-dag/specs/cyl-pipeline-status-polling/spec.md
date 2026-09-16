## ADDED Requirements

### Requirement: A `'complete'` rollup does not imply every scan produced a result

The rollup SHALL continue to compute run status purely from Workflow phases, and callers SHALL NOT
infer per-scan completeness from the result. Since the pipeline DAG gained its terminal exit gate, a
Workflow phase of `Succeeded` means "the gate accepted the producers' exit codes", which includes
the partial-success code `3` — so rule (2)'s `'complete'` is reachable for a run in which individual
scans genuinely failed. The poller SHALL therefore keep recording real per-scan outcomes in
`done_count`/`failed_count` independently of the phase-derived status, and no consumer of
`cyl_pipeline_runs.status` may treat `'complete'` as equivalent to `failed_count = 0`.

Two combinations follow. Neither is a defect in the rollup:

1. **`'complete'` with `failed_count > 0`** — reachable when `images-downloader` isolates some
   scans' failures and stages the rest. It is specific to that stage: a scan isolated later, by
   `predictor` or `trait-extractor`, is already recorded in the `RunManifest`, so write-back finds a
   declared `scan_key` with no result and exits non-zero, the gate is omitted, and the run reads
   `'failed'` instead.
2. **`'failed'` with `done_count > 0`** — **already reachable before the exit gate**, because each
   envelope's per-scan `'written'` update commits in that envelope's own transaction, so any
   write-back that ingested some envelopes and then exited non-zero produced it. The gate does not
   create this combination; it adds a **second route** to it, by rejecting a producer exit code
   outside `{0,3}` after write-back has already committed results. Consumers must not read
   `'failed'` as "nothing was written".

Pipeline-level `'partial'` (rule (4)) is unchanged in definition but narrowed in practice: it no
longer arises from partial failure *within* a batch, only from whole batch Workflows differing in
outcome across a multi-batch run.

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
