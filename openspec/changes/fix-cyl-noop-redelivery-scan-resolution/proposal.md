# Fix: a no-op re-delivery finds its scan from the source row (bloom#900, bloom#875)

## Why

A pipeline run over a scan that already has a pipeline source reports that scan `failed`, even
though write-back's re-delivery is a healthy first-writer-wins no-op and the scan's traits are
correct and unchanged. Reproduced live on staging on 2026-09-30 (bloom#900 comment): Bloom run 11
(Workflow `sleap-roots-pipeline-mlq9k`) re-ran scan 12894756, whose only source (228) came from
the hand-submitted Workflow `sleap-roots-pipeline-cdbnp`. Write-back logged
`Ingested 0/1 … FAILED scan_12894756: write-back succeeded (source_id=228) …`, row 31 ended
`failed` with `source_id` null, and run 11 ended `complete` with `done_count 0`,
`failed_count 1`.

The mechanism, in the live RPC body
(`supabase/migrations/20260930120100_stamp_cyl_trait_source_recipe_and_run.sql`):

1. The source insert hits `ON CONFLICT (idempotency_key) DO NOTHING` (the key carries no run
   id), so the call takes the `was_noop` branch.
2. The branch's primary update joins `argo_workflow_name = p_argo_workflow_name AND
   source_id = <existing source>`. The new Workflow's run-scan row has `source_id` NULL, so it
   matches nothing.
3. The bloom#875 fallback (PR #880) looks the scan up from an existing `cyl_pipeline_run_scans`
   row that carries the source. A source written by a manual `bloomctl cyl ingest-result` or a
   hand-run `argo submit` has no such row, so the fallback does nothing and
   `status_update_matched` is `false`.
4. `bloomctl` checks `status_update_matched` before `was_noop`
   (`bloomcli/src/bloomctl/cyl/ingest.py`, `ingest_one_envelope` and `cyl ingest-result`) and
   reports `failed`, `retriable=False`. Its message says "write-back succeeded … The written
   trait/blob data is correct", which is false on a no-op: nothing was written.
5. End-of-batch reconciliation (`fail_cyl_pipeline_run_scans_without_result`) closes the still
   `queued` row as `failed` with "no result produced for this scan by write-back".

This blocks every Bloom-dispatched E2E over the pre-existing A4 test scans (all of them are among
`cdbnp`'s 15 sources) and makes "Re-run failed scans" in the new run UI fail the same way every
time. It also leaves bloom#875 open: #880 fixed the Bloom-dispatched-original case, but #875's own
live reproduction was the hand-submitted shape.

Since bloom#935/#937 (`add-cyl-trait-recipe-key`, PR #976), every source row records the scan its
first write resolved: `cyl_trait_sources.scan_id`, stamped by the RPC on every fresh insert and
backfilled for older sources. On staging (read-only, 2026-10-01) no object-metadata source lacks
it (0 of 91), and source 228's `scan_id` is 12894756. That is exactly the "run of record's own
scan" the #875/#880 rule wants, reachable by primary key.

## What Changes

- **RPC (`cyl-trait-writeback`).** A new forward migration `CREATE OR REPLACE`s
  `insert_cyl_result_envelope(jsonb, text)` with one change to the no-op branch's fallback: when
  the primary `(argo_workflow_name, source_id)` update matches nothing, resolve the scan from the
  existing source's own `cyl_trait_sources.scan_id`; only when that is NULL, fall back to #880's
  run-scan-row lookup. The targeted update is unchanged: `argo_workflow_name =
  p_argo_workflow_name AND scan_id = <scan> AND status != 'failed'`, setting `status =
  'written'` and `source_id`. Everything else in the body, its signature, return shape, owner and
  grants stay as they are. Never resolved from `cyl_scan_traits` (about 28.8M rows on staging,
  no index leading on `source_id`).
- **bloomctl (`cyl-ingest-cli`, `cyl-batch-ingest-result`).** When the RPC returns
  `was_noop: true` and `status_update_matched: false`, keep reporting a non-retriable failure but
  with a message that is true: the envelope was already ingested as source N, nothing was
  written, and this Workflow's run-scan row for the scan was not updated. The `was_noop: false`
  message is unchanged. Exit codes are unchanged.
- **Web (`cyl-pipeline-ui`).** Remove the bloom#900 workaround: `NO_OP_NOTE`,
  `isNoOpCandidate`, `NO_OP_RERUN_WARNING`, the two no-result message constants that only
  existed for it, and their tests.
- **Specs.** The no-op's run-scan update is specified against the source's own scan; the
  "never existed reports no match" scenario becomes "is marked written"; the CLI "regardless of
  `ARGO_WORKFLOW_NAME`" sentence is narrowed to the cases the RPC can actually match; two
  inaccurate statements in the live RPC requirement are corrected (the step-9 update is not the
  only statement that writes `source_id`; no column comment documents `'reused'`).
- **Sibling delta raised.** `add-cyl-trait-recipe-key`'s ADDED requirement "Write-back stamps
  each new source with its recipe, scan, Workflow and run" says the RPC keeps "the fallback that
  resolves the scan from an existing run-scan row". That clause is updated in this PR so it stays
  true whichever change archives first.

Not changed: `'reused'` stays reserved for the unimplemented skip-if-done mechanism (the rescued
row is `'written'`, as #880's fallback already writes); the status poller and run counts; the
reconciliation RPC; the idempotency key; rows already recorded `failed` (forward-only).

## Impact

- Affected specs: `cyl-trait-writeback`, `cyl-ingest-cli`, `cyl-batch-ingest-result`,
  `cyl-pipeline-ui`; plus a wording raise in `add-cyl-trait-recipe-key`'s unarchived delta.
- Affected code: one new migration + rollback under `supabase/`;
  `bloomcli/src/bloomctl/cyl/ingest.py`; `web/lib/cyl-pipeline/failure-hints.ts`,
  `web/app/app/cyl-pipeline-runs/[runId]/RunDetailLive.tsx`, `RunScansTable.tsx`; tests in
  `tests/integration/test_cyl_writeback_rpc.py`, a new `tests/unit/` migration-file test,
  `bloomcli/tests/test_cyl_ingest.py`, and the web tests.
- Rollout: the RPC fix alone makes a re-delivery report `status_update_matched: true`, so the
  current bloomctl image already reports it `skipped`. The bloomctl message change reaches the
  cluster only when the Argo templates' bloomctl image pin next moves.
- Issues: Part of bloom#900 and bloom#875. Both are closed by hand after the staging acceptance
  runs (tasks §7), per bloom#780.
- Related: bloom#881 (run-scan `source_id` index; this change removes the fallback's routine
  dependence on that unindexed lookup but does not replace #881), bloom#704, bloom#857, PR #988
  (also edits `failure-hints.ts`/`RunDetailLive.tsx`; expect a small merge conflict).
