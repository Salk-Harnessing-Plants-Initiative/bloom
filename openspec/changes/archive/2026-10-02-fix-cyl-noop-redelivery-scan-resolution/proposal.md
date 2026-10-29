# Fix: a no-op re-delivery finds its scan from the source row (bloom#900, bloom#875)

## Why

A pipeline run over a scan that already has a pipeline source reports that scan `failed`, even
though write-back's re-delivery is a healthy first-writer-wins no-op and the scan's traits are
correct and unchanged. Reproduced live on staging on 2026-09-30 (bloom#900 comment): Bloom run 11
(Workflow `sleap-roots-pipeline-mlq9k`) re-ran scan 12894756, and its re-delivery matched, by
idempotency key, source 228, written by the hand-submitted Workflow `sleap-roots-pipeline-cdbnp`.
Write-back logged `Ingested 0/1 … FAILED scan_12894756: write-back succeeded (source_id=228) …`,
row 31 ended `failed` with `source_id` null, and run 11 ended `complete` with `done_count 0`,
`failed_count 1`.

The mechanism (run 11 hit `20260928130000_cyl_writeback_contract_a9.sql`; the live
`20260930120100_stamp_cyl_trait_source_recipe_and_run.sql` carries the same no-op branch):

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
   (`bloomcli/src/bloomctl/cyl/ingest.py`): `ingest_one_envelope` returns `failed` with
   `retriable=False`, and `cyl ingest-result` exits non-zero. The message says "write-back
   succeeded … The written trait/blob data is correct", which is false on a no-op: nothing was
   written.
5. End-of-batch reconciliation (`fail_cyl_pipeline_run_scans_without_result`) closes the still
   `queued` row as `failed` with "no result produced for this scan by write-back".

Per the sleap-roots-pipeline roadmap's row-6 E2E note, every A4-PIPELINE-E2E-TEST scan that
existed before that row, and scans 289/577/1009, is among `cdbnp`'s 15 keys (sources 219–233), so
none can pass a Bloom-dispatched E2E, and "Re-run failed scans" in the run UI fails the same way
every time.
bloom#875 stays open for the same reason: #880 fixed the Bloom-dispatched-original case, but
#875's own live reproduction was the hand-submitted shape.

Since `add-cyl-trait-recipe-key` (PR #976), each source row records its scan:
`cyl_trait_sources.scan_id`, stamped by the RPC when it creates a source and backfilled for older
sources from their stored `image_ids`. That is the "run of record's own scan" #880's rule wants,
reachable by primary key.

## What Changes

Two PRs under this one change (design D7):

- **PR A — database.** A forward migration `CREATE OR REPLACE`s
  `insert_cyl_result_envelope(jsonb, text)`, changing only the no-op fallback: resolve the scan
  from `cyl_trait_sources.scan_id`, with #880's run-scan lookup as the backup when it is NULL, and
  never overwrite a row already linked to a different source (design D1, D8). Rollback, SQL tests,
  and this proposal ride with it.
- **PR B — bloomctl and web, after PR A deploys to staging.**
  - `bloomctl`: when the RPC returns `was_noop: true` and `status_update_matched: false`, report a
    failure whose message says the envelope was already ingested as source N and nothing was
    written (design D3). Exit codes and `retriable` are unchanged. CHANGELOG and README follow.
  - Web: remove the bloom#900 note (`NO_OP_NOTE`, `isNoOpCandidate`, `NO_OP_RERUN_WARNING`) and
    its tests (design D4); say that "Result recorded" includes matched results (D9); and name a
    result of this run that arrived after its row was closed (D10).
- **Specs.** The no-op's run-scan update is specified against the source's recorded scan; the
  "never existed reports no match" scenario becomes "is marked written"; the CLI's "regardless of
  `ARGO_WORKFLOW_NAME`" sentence is narrowed to the cases the RPC can match; two false statements
  in the live RPC requirement are corrected (the step-8 update is not the only writer of
  `source_id`; no column comment documents `'reused'`), and so is the "already reflected in the
  run's `failed_count`" clause of the CLI requirement, which the code's message no longer asserts.
- **Sibling deltas raised** so archive order cannot revert this change (tasks §2).

## Impact

- Affected specs: `cyl-trait-writeback`, `cyl-ingest-cli`, `cyl-batch-ingest-result`,
  `cyl-pipeline-ui`; wording raises in the unarchived `fix-cyl-redelivery-blob-collision` and
  `add-cyl-trait-recipe-key` deltas.
- PR A code: `supabase/migrations/<ts>_resolve_cyl_noop_redelivery_scan_from_source.sql` and its
  rollback; new `tests/integration/test_cyl_noop_redelivery_scan.py` and
  `tests/unit/test_cyl_noop_redelivery_migration_files.py`; docstrings in
  `tests/integration/test_cyl_writeback_rpc.py`.
- PR B code: `bloomcli/src/bloomctl/cyl/ingest.py`, `bloomcli/src/bloomctl/cyl/_batch.py`
  (comment), `bloomcli/tests/test_cyl_ingest.py`, `bloomcli/CHANGELOG.md`, `bloomcli/README.md`;
  `web/lib/cyl-pipeline/failure-hints.ts`,
  `web/app/app/cyl-pipeline-runs/[runId]/RunDetailLive.tsx`, `RunScansTable.tsx` and their tests.
- No table, column, constraint or index changes; `database.types.ts` is unchanged.
- Issues: Part of bloom#900 and bloom#875; both are closed by hand after the staging acceptance
  runs (tasks §8), per bloom#780. Related: bloom#881, bloom#704, bloom#857, bloom#864.
