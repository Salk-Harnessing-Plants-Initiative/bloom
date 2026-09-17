# Fix: RPC-side fallback for cross-workflow write-back re-delivery (bloom#875)

## Why

`insert_cyl_result_envelope`'s no-op branch marks a scan's `cyl_pipeline_run_scans` row
`'written'` by joining on `(argo_workflow_name, source_id)`. A **new** pipeline run
re-dispatching an already-ingested scan gets a fresh row with `source_id IS NULL` (only the
non-no-op path ever writes `source_id`, and only for the workflow that first wrote the data).
That join therefore matches zero rows, `status_update_matched` comes back `false`,
`ingest_one_envelope` reports the delivery `failed` (`retriable=False`), and end-of-batch
reconciliation (`fail_cyl_pipeline_run_scans_without_result`) closes the scan out as `'failed'`.

Measured live on staging 2026-09-17 (bloom#875, itself filed by #871's own design.md Risks
section before it was observed): a 3-scan batch with 2 already-ingested scans and 1 genuine
failure produced `done_count=0, failed_count=3` on a Workflow that reported **Succeeded**. Both
of the operator-facing signals sleap-roots-pipeline#56's exit gate tells a reader to trust — the
Workflow's own color and `cyl_pipeline_runs.failed_count` — disagree with the data: the two
re-delivered scans' traits and blobs are correct in `cyl_trait_sources`/`cyl_scan_intermediates`.
Production is dormant, which is the only reason this isn't biting there yet.

This is not fixable by reordering `ingest_one_envelope`'s two checks (`status_update_matched`
before `was_noop`): `fix-cyl-pipeline-run-scan-status` legislates a non-zero exit on
`status_update_matched=false`, and `fix-cyl-redelivery-blob-collision` (#871) legislates zero on
`was_noop=true`. Neither spec addresses the intersection, and reordering the checks would
silently override the first sibling's requirement while leaving the row stranded at `queued`
anyway. The fix belongs where the status contract is defined: the RPC itself.

## What Changes

- `insert_cyl_result_envelope`'s no-op branch: when the existing
  `(argo_workflow_name, source_id)`-keyed `UPDATE` matches zero rows, fall back to a
  `scan_id`-scoped `UPDATE` within the same `argo_workflow_name`, where `scan_id` is looked up
  from an existing `cyl_pipeline_run_scans` row already carrying this `source_id` (stamped by
  the original successful delivery) — never re-derived from this delivery's own `image_ids`,
  preserving the existing "same key, different scan" rule that the run of record's scan, not the
  new delivery's claim, governs a no-op.
- No `bloomctl`/`ingest.py` changes. The client-side `status_update_matched is False → failed`
  check and its message are already correct; they were only ever wrong because the RPC handed
  them a false negative.
- **MODIFIED** `cyl-trait-writeback`: "Write-back RPC ingests a ResultEnvelope" gains the
  fallback step.
- **MODIFIED** `cyl-ingest-cli`: "Re-ingest is a benign, distinctly-reported no-op" gains a
  scenario for the cross-workflow re-delivery case. This makes the *behavior* bloom#875
  describes correct, but it does not edit the sentence that documents the gap as open —
  that sentence lives in a different, ADDED requirement in `fix-cyl-redelivery-blob-collision`
  ("An already-ingested envelope skips blob upload") that this change does not touch. See
  `design.md`'s Risks section for why, and the follow-up task that annotates it post-deploy
  instead.

## Impact

- Affected specs: `cyl-trait-writeback`, `cyl-ingest-cli`.
- Affected code: one migration (`CREATE OR REPLACE` on the existing
  `insert_cyl_result_envelope(jsonb, text)` signature — no `DROP FUNCTION`, the signature does
  not change) plus its `supabase/rollbacks/` partner. No application code changes.
- Archive-ordering hazard: `fix-cyl-pipeline-run-scan-status` (64/72 tasks) and
  `fix-cyl-redelivery-blob-collision` (60/73 tasks, bloom#871) are both unarchived and carry
  `MODIFIED` deltas on `cyl-ingest-cli` requirements adjacent to (but textually distinct from)
  the one this change modifies; see `design.md`'s Risks section for the clause-by-clause
  reconciliation and why neither sibling is archived as part of this change.
- Verification is constrained: a full live Argo re-test needs scan_ids with no prior envelope,
  which the existing E2E test experiment no longer has; see `design.md`'s Verification section.
