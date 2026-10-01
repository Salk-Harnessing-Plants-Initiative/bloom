# Design: no-op re-delivery scan resolution (bloom#900, bloom#875)

## Context

`insert_cyl_result_envelope`'s no-op branch must mark the re-delivering Workflow's own
`cyl_pipeline_run_scans` row, but the no-op never resolves a scan from the delivery itself: the
"same key, different scan" rule (`cyl-trait-writeback`, "Write-back is idempotent and
provenance-immutable") says the run of record's scan governs, not the re-delivery's `image_ids`.
#880 therefore found the scan through another run-scan row carrying the source, which only exists
when the source's first delivery was itself Bloom-dispatched.

`cyl_trait_sources.scan_id` (added by `20260930120000_add_cyl_trait_recipe_key.sql`, stamped by
`20260930120100` at the point the fresh path resolves its scan, and backfilled with the same
resolution rules) records that scan on the source row itself.

## Decisions

### D1 — Resolve from the source's own `scan_id`; keep #880's lookup only as a backup

On a no-op with `p_argo_workflow_name` set, the order becomes:

1. Primary update by `(argo_workflow_name, source_id)` — unchanged.
2. If it matched nothing: `SELECT scan_id FROM cyl_trait_sources WHERE id = v_source_id`.
3. If that is NULL: #880's `SELECT scan_id FROM cyl_pipeline_run_scans WHERE source_id =
   v_source_id LIMIT 1` — unchanged.
4. If a scan was found: the targeted update by `(argo_workflow_name, scan_id)` with
   `status != 'failed'` — unchanged.

Why the source row first: it is the record of the first write, so #880's rule still holds; it
exists for every source the RPC wrote since #976 and for every backfilled one, so it covers both
origins of an uncarried source (manual `ingest-result`, hand-run `argo submit`); and it is a
primary-key read.

Why keep #880's lookup at all: `scan_id` can still be NULL for a source the backfill could not
resolve, or after its scan row was deleted (`ON DELETE SET NULL`). For data the RPC wrote, both
lookups name the same scan, so the order changes nothing observable there.

Scoping is still done by the update itself: it only touches rows whose `argo_workflow_name` is
this Workflow's, so a source whose scan this Workflow did not request matches zero rows and
reports `status_update_matched: false`, as today. No separate "did this Workflow request this
scan" check is needed.

The no-op return value is unchanged: `scan_id` stays `null` (the `cyl-ingest-cli` "already
ingested" message and its tests rely on that), `trait_count`/`blob_count` 0.

**Alternatives rejected.**

- *Resolve from `cyl_scan_traits` / `cyl_scan_intermediates` by `source_id`* (the bloom#900
  comment's suggestion, written before `cyl_trait_sources.scan_id` existed). `cyl_scan_traits` has
  about 28.8M rows on staging and no index leading on `source_id` (its indexes lead on `id` or
  `scan_id`), so every re-delivery would scan the table and could hit the same statement timeout
  class as bloom#992. It also misses trait-less sources.
- *Resolve from this delivery's `image_ids`.* Breaks the "same key, different scan" rule.
- *Write `'reused'`, or skip resolution.* See D2.

### D2 — The rescued row is `'written'`

The poller (`services/workflows/status_poller.py`) and the web UI treat `'reused'` exactly like
`'written'` (done, "Result recorded"), so the choice has no visible effect today. `'written'`
matches what #880's fallback and the same-Workflow no-op already write, and keeps
`source_id IS NOT NULL ⇒ status = 'written'` true. `'reused'` stays reserved for the
unimplemented pre-dispatch skip-if-done mechanism (`cyl-pipeline-runs`).

### D3 — A residual unmatched no-op stays a non-retriable failure, with a true message

After D1, a no-op can still return `status_update_matched: false` when: the source has no
`scan_id` and no carrying run-scan row; this Workflow never dispatched that scan; or this
Workflow's row for it is already `'failed'`. `bloomctl` keeps reporting these as failed with
`retriable=False` (exit code unchanged): in the first two cases the row, if any, stays `queued`
and reconciliation closes it `failed`, so reporting `skipped` would make bloomctl's summary and
the run's `failed_count` disagree. The message changes to say what happened: already ingested as
source N, nothing written, and this Workflow's row for the scan not updated. The `cyl-ingest-cli`
"MUST NOT be reported as a failure … regardless of which `ARGO_WORKFLOW_NAME`" sentence is
narrowed to "whenever the RPC matched this Workflow's row" — the sentence assumed the fallback
always matches, which bloom#900 disproved.

### D4 — Remove the UI's bloom#900 note

Once a no-op re-delivery ends `'written'`, the note can only mislabel rows, and the common
mislabel (a failed row whose scan a later run fixed) is documented on bloom#900. Removed with it:
`isNoOpCandidate`'s second named cause (a write-back landing after the poller's backstop already
failed the row) loses its hint too; that row still shows `failed` with no `source_id`, and
"current in trait views" stays "no". Rows already recorded `failed` by the bug stay `failed`
(forward-only); they exist only on staging test runs, since prod's dispatch is switched off.

### D5 — Migration shape

A new forward migration (timestamp after `20261001180000`) with `CREATE OR REPLACE` of the same
`(jsonb, text)` signature; no `DROP FUNCTION`, since the return type is unchanged. The body is the
`20260930120100` body verbatim except the fallback block, enforced by a unit test that diffs the
two files (the precedent is `tests/unit/test_cyl_trait_recipe_migration_files.py`). It re-states
owner, the full `REVOKE … FROM PUBLIC, anon, authenticated` (as `20260928130100` requires of any
re-definition) and the grant. The rollback restores the `20260930120100` body verbatim, with the
same owner/revoke/grant. The migration does not re-run the recipe backfill: no source written
between `20260930120100` and this migration can lack `scan_id`, because the live body stamps it.

## Risks / Trade-offs

- **Archive-ordering hazard.** `fix-cyl-redelivery-blob-collision` MODIFIES "Re-ingest is a
  benign, distinctly-reported no-op" with text identical to the live spec; this change's MODIFIED
  block is a superset of it, and that sibling's delta must be raised to this change's text before
  either archives (tasks §6). `add-cyl-trait-recipe-key` ADDS "Write-back stamps each new source
  …", whose "keeps … the fallback that resolves the scan from an existing run-scan row" clause is
  raised in this PR. `openspec validate --strict` cannot see either.
- **A batch that dispatched two scans sharing one idempotency key.** The fallback can mark the
  first-written scan's row `'written'` from the other scan's delivery. That requires two scans
  with one `scan_key` (the key hashes `scan_key`), and that scan does have a result; #880 had the
  same property.
- **Local integration coverage.** The shared dev DB is not migrated; new tests apply this
  migration's body inside their own rolled-back transaction. Older RPC tests run locally against
  the current live body; CI runs everything against a fresh DB with all migrations.
- **Rollout split.** The RPC fix is effective at staging deploy; the message fix only when the
  Argo templates' bloomctl pin moves. Until then a residual unmatched no-op keeps today's
  misleading text.
- **Not addressed:** bloom#881 (run-scan `source_id` index, partial unique on
  `(argo_workflow_name, scan_id)`), bloom#857 (`complete` with `failed_count > 0`), bloom#864.
