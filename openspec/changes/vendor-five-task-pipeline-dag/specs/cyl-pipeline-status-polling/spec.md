## ADDED Requirements

### Requirement: A `'complete'` rollup does not imply every scan produced a result

The rollup SHALL continue to compute run status purely from Workflow phases, and callers SHALL NOT
infer per-scan completeness from the result. Since the pipeline DAG gained its terminal exit gate,
a Workflow phase of `Succeeded` means "the gate accepted the producers' exit codes", which includes
the partial-success code `3` — so rule (2)'s `'complete'` is reachable for a run in which
individual scans genuinely failed. The poller SHALL therefore keep recording real per-scan outcomes
in `done_count`/`failed_count` independently of the phase-derived status, and no consumer of
`cyl_pipeline_runs.status` may treat `'complete'` as equivalent to `failed_count = 0`.

Two consequences follow and are intended, not defects in the rollup:

1. **`'complete'` with `failed_count > 0`** — the normal outcome of a batch that isolated some
   scans' failures and completed the rest.
2. **`'failed'` with `done_count > 0`** — previously impossible. Before the exit gate, a red DAG
   never reached write-back, so a failed run had written nothing. Now write-back can commit results
   for the scans that succeeded and the Workflow can still end `Failed` (the gate rejecting a
   producer's non-`{0,3}` exit code, or write-back itself failing so the gate is omitted and
   inherits `Failed`).

Pipeline-level `'partial'` (rule (4)) is unchanged in definition but narrowed in practice: it no
longer arises from partial failure *within* a batch, only from whole batch Workflows differing in
outcome across a multi-batch run.

#### Scenario: A batch that isolated a scan failure still rolls up to complete

- **WHEN** a run's only batch exits `3` from a producer, the exit gate accepts it, and the Workflow
  phase is `Succeeded`
- **THEN** the rollup returns `'complete'`
- **AND** the run's `failed_count` is non-zero, recorded from real per-scan status rather than
  inferred from the phase

#### Scenario: A failed run may have written results

- **WHEN** write-back commits results for the scans that succeeded, and the Workflow then ends
  `Failed` because the exit gate rejected a producer's exit code
- **THEN** the rollup returns `'failed'`
- **AND** `done_count` is non-zero, so a consumer must not treat `'failed'` as "nothing was written"
