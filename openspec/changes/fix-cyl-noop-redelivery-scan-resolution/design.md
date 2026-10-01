# Design: no-op re-delivery scan resolution (bloom#900, bloom#875)

## Context

The no-op branch must mark the re-delivering Workflow's own `cyl_pipeline_run_scans` row without
resolving a scan from the delivery itself: the "same key, different scan" rule
(`cyl-trait-writeback`, "Write-back is idempotent and provenance-immutable") says the run of
record's scan governs. #880 found that scan through another run-scan row carrying the source,
which exists only when the source's first delivery was itself Bloom-dispatched.
`cyl_trait_sources.scan_id` (`20260930120000_add_cyl_trait_recipe_key.sql`) holds it on the source
row: the RPC stamps it when it creates a source (`20260930120100`, after step 6 resolves the
scan), and the recipe backfill set it for older sources from their stored `image_ids`, leaving it
NULL where that resolution would have raised.

## Decisions

### D1 — Resolve from the source's own `scan_id`; keep #880's lookup only as a backup

On a no-op with `p_argo_workflow_name` set:

1. Primary update by `(argo_workflow_name, source_id)` — unchanged.
2. If it matched nothing: `SELECT scan_id FROM cyl_trait_sources WHERE id = v_source_id`.
3. If that is NULL: #880's `SELECT scan_id FROM cyl_pipeline_run_scans WHERE source_id =
   v_source_id LIMIT 1` — unchanged.
4. If a scan was found: the targeted update by `(argo_workflow_name, scan_id)`, guarded by
   `status != 'failed'` and D8.

The source row comes first because it is the record of the source's own scan, so #880's rule still
holds; it exists for every source the RPC created since #976 and for every resolvable older one, so
it covers both origins of an uncarried source (manual `ingest-result`, hand-run `argo submit`); and
it is a primary-key read. #880's lookup stays for a source whose `scan_id` is NULL: one the
backfill could not resolve, or whose scan row was deleted (`ON DELETE SET NULL`). For data the RPC
wrote, both lookups name the same scan.

The update itself does the scoping: it touches only this Workflow's rows, so a source whose scan
this Workflow did not request matches zero rows and reports `status_update_matched: false`, as
today. The no-op return value is unchanged (`scan_id` null, counts 0).

**Rejected.**

- *`cyl_scan_traits` by `source_id`* (the bloom#900 comment's suggestion, written before
  `cyl_trait_sources.scan_id` existed). No index leads on `source_id` (indexes: the PK,
  `idx_cyl_scan_traits (scan_id)`, `UNIQUE (scan_id, source_id, trait_id)`), and the table is
  about 28.8M rows on staging (read 2026-10-01), so each re-delivery would scan it — the statement
  timeout class of bloom#992. It also misses trait-less sources.
- *`cyl_scan_intermediates` by `source_id`.* Indexed (its UNIQUE leads on `source_id`), but misses
  blob-less sources, and duplicates what the source row already holds.
- *This delivery's `image_ids`.* Breaks the "same key, different scan" rule.
- *`'reused'`, or no resolution.* See D2.

### D2 — The rescued row is `'written'`

The status poller (`services/workflows/status_poller.py`) and the web UI treat `'reused'` exactly
like `'written'` (done, "Result recorded"), so the choice is invisible today. `'written'` matches
what #880's fallback and the same-Workflow no-op already write, and keeps
`source_id IS NOT NULL ⇒ status = 'written'` true. `'reused'` stays reserved for a later phase in
which the cluster-side skip-if-done check records a scan that needed no new work
(`cyl-pipeline-runs`).

### D3 — A residual unmatched no-op stays a non-retriable failure, with a true message

After D1, a no-op still returns `status_update_matched: false` when the source has no recorded
scan and no carrying row, when this Workflow never dispatched that scan, when this Workflow's row
is already `'failed'`, or (D8) when the row is linked to a different source. bloomctl keeps
reporting these as failed (`retriable=False` in the batch helper; non-zero exit for
`cyl ingest-result`): in the first two cases any row stays `queued` and reconciliation closes it
`failed`, so reporting `skipped` would make bloomctl's summary disagree with `failed_count`. The
message changes to what happened: already ingested as source N, nothing written, this Workflow's
row for the scan not updated. The `was_noop: false` message is unchanged.

### D4 — Remove the UI's bloom#900 note

Once a no-op re-delivery ends `'written'`, the note only mislabels rows; the common mislabel (a
failed row whose scan a later run fixed) is on bloom#900. `isNoOpCandidate` also named a second
cause — a write-back landing after the poller's backstop already failed the row — and that hint
goes too; such a row still shows `failed` with no `source_id`, and "current in trait views" says
no. `BACKSTOP_MESSAGE` and `WRITEBACK_NO_RESULT_MESSAGE` stay: #988's `failedScanCause` test uses
the latter, and both keep their source-equality tests, which stop the texts drifting from the
poller and from `ingest.py`. Rows already recorded `failed` by the bug stay `failed`
(forward-only). On staging these are test runs; prod's web trigger is off
(`CYL_PIPELINE_TRIGGER_ENABLED=false`); prod rows were not checked.

### D5 — Migration shape

A forward migration, timestamp after `20261001200000` (open PR #997) and after the staging tip at
push time, `CREATE OR REPLACE` of the same `(jsonb, text)` signature; no `DROP FUNCTION`, the
return type is unchanged. The body is `20260930120100`'s verbatim except **the fallback block**:
the comment beginning "bloom#875 fallback" through the `END IF;` that closes
`IF v_status_rows = 0 THEN` (lines 161–190 of `20260930120100`). Both false statements in that
comment ("source_id is written only by the non-no-op path"; "If this source was never delivered
under any workflow name … the fallback correctly does nothing") are rewritten. The body keeps
exactly one `v_was_noop := true;` (`test_noop_stamp_guard_detects_mutation`) and one
`pinned_version constant text :=` (`test_contract_migration_match.py`), including in comments. It
re-states owner, the full `REVOKE … FROM PUBLIC, anon, authenticated` (`20260928130100`) and the
grant, and does not re-run the recipe backfill: every source created since `20260930120100` has
its `scan_id` stamped. The rollback restores `20260930120100`'s body verbatim with the same
owner/revoke/grant, and its header says to apply it before `20260930120100`'s own rollback and
that the next staging deploy re-applies the migration after a `migration repair --status
reverted`.

### D6 — Local integration tests never migrate the shared dev DB

New integration tests apply this migration's body inside their own transaction on `pg_conn`
(supabase_admin) and roll it back. CI's `compose-health-check` applies every migration to a fresh
DB and runs all of `tests/integration/`, so it is the only run of the existing RPC suite against
the new body; a green CI run is required evidence.

### D7 — Two PRs

`.claude/commands/database-migration.md` ("A PR that changes `supabase/migrations/` ships on its
own") and CI's migration-isolation lint keep the migration apart from `bloomcli/` and `web/` code.
PR A carries this proposal, the migration, rollback and SQL tests, and the sibling raises; PR B
carries bloomctl and web, and opens after PR A is deployed to staging, so the UI note never
disappears while the bug is still live.

### D8 — A no-op never replaces another source's link

The targeted update adds `AND (source_id IS NULL OR source_id = v_source_id)`. Scans can carry
several sources (scan 12894756 has four on staging: 133, 168, 198, 228), so a Workflow that
delivered a fresh source X for a scan and then a no-op of an older source Y for the same scan would
otherwise relink the row from X to Y. With the guard the row keeps X and the no-op reports
`status_update_matched: false`.

## Risks / Trade-offs

- **Archive ordering** — handled in tasks §2; `openspec validate --strict` cannot see it.
- **Rollout split** — PR A alone makes a matched re-delivery return `status_update_matched: true`,
  which the deployed bloomctl image (`sha-1bc3056`, same check order as HEAD) already reports
  `skipped`. PR B's message reaches the cluster only when the Argo templates' bloomctl pin next
  moves; until then a residual unmatched no-op keeps today's misleading text.
- **Two scans sharing one idempotency key in one batch.** The key hashes `scan_key`, so this needs
  two scans with one `scan_key`; the fallback would then mark the recorded scan's row, which does
  have a result. #880 had the same property.
- **Not addressed:** bloom#881 (run-scan `source_id` index and partial unique on
  `(argo_workflow_name, scan_id)`), bloom#857, bloom#864.
