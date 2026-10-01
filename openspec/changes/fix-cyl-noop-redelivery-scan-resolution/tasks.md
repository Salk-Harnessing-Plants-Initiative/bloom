Two PRs under this change (design D7). §1–§6 are PR A (database); §7 is PR B (bloomctl and web),
opened from `staging` after PR A is deployed there; §8 is post-deploy.

## 1. Scaffolding

- [x] 1.1 Worktree `.worktrees/cyl-noop-redelivery-900`, branch `fix/cyl-noop-redelivery-900`
  from `origin/staging`, upstream unset so a bare push cannot land on `staging`; `origin/staging`
  merged in after #988 landed (`2a90e279`).
- [x] 1.2 Read bloom#900 and bloom#875 with their comments, the live RPC body
  (`20260930120100_stamp_cyl_trait_source_recipe_and_run.sql`), `20260930120000` (which added
  `cyl_trait_sources.scan_id`), and every unarchived delta on the four affected capabilities.
- [x] 1.3 Staging, read-only, 2026-10-01: source 228's `scan_id` is 12894756; scan 12894756 has
  four sources (133, 168, 198, 228); 0 of 91 object-metadata sources lack `scan_id`;
  `cyl_scan_traits` is about 28.8M rows.
- [x] 1.4 Decisions D1–D8 in `design.md` (author's answers, 2026-10-01).
- [x] 1.5 `/review-openspec` (5 reviewers); findings applied in this revision.

## 2. Archive-ordering hazard (`openspec validate --strict` cannot see it)

- [x] 2.1 `fix-cyl-redelivery-blob-collision` MODIFIES `cyl-ingest-cli` "Re-ingest is a benign,
  distinctly-reported no-op". Its block is raised to this change's block, byte-identical, and its
  HTML note above the block is updated to say so.
- [x] 2.2 That raise makes the sibling's block describe this change's behaviour, so its tasks 9.10
  now says it must not archive before this change has merged and deployed to staging.
- [x] 2.3 `add-cyl-trait-recipe-key` ADDS `cyl-trait-writeback` "Write-back stamps each new source
  with its recipe, scan, Workflow and run"; its clause describing #880's lookup is reworded to
  refer to the fallback in "Write-back RPC ingests a ResultEnvelope", true before and after.
- [x] 2.4 No other unarchived change touches the eight requirements this change modifies (grep of
  every `### Requirement:` heading under `openspec/changes/*/specs/`, excluding `archive/`).
  `isolate-cyl-pipeline-environments` (#988, merged) modifies only `cyl-pipeline-ui` "Starting
  pipeline runs can be switched off per environment".
- [ ] 2.5 At archive time, re-run 2.4's grep, archive this change before
  `fix-cyl-redelivery-blob-collision` (or in the same PR, this one first), and `diff` each
  modified block against `openspec/specs/…` afterwards; record the result here.

## 3. PR A tests first (red)

Tests are committed together with the implementation that makes them pass; the red evidence is
recorded here, not as a red commit.

### 3a. `tests/integration/test_cyl_noop_redelivery_scan.py` (new)

Fixture contract (design D6): an autouse fixture finds the migration by glob
(`*_resolve_cyl_noop_redelivery_scan_from_source.sql`, exactly one match, else fail), applies its
body (`sql_body`, BEGIN/COMMIT stripped) on `pg_conn` with `SET LOCAL lock_timeout = '5s'`, asserts
the connection is in a transaction, and calls `pg_conn.rollback()` in teardown. Tests use
`SAVEPOINT` / `ROLLBACK TO SAVEPOINT` only and never `commit()`/`rollback()` mid-test; each ends by
asserting the new fallback is still the live body (`pg_get_functiondef` contains
`FROM public.cyl_trait_sources`). No test here uses a second connection (it would see the
committed old body). Helpers come from `tests/integration/test_cyl_writeback_rpc.py`
(`_seed_scan`, `_envelope`, `_call`, `_seed_run_scan_for_writeback`, `_run_scan_status`,
`_source_id`, `_source_snapshot`). "Unchanged" means equal `_source_snapshot` before and after;
"row untouched" means equal `(ctid, status, source_id)` before and after.

- [ ] 3.1 `test_manual_origin_noop_is_marked_written`: deliver with no workflow name; seed `wf-b`'s
  queued row for the scan; re-deliver under `wf-b`. `was_noop` true, `status_update_matched` true,
  `scan_id` null, `trait_count`/`blob_count` 0; `wf-b`'s row `('written', <source>)`; the source is
  unchanged and no `cyl_scan_traits` row of the scan has another new `source_id`.
- [ ] 3.2 `test_hand_submitted_origin_noop_is_marked_written`: as 3.1, but the first delivery is
  under `wf-a` with no run-scan rows for `wf-a`.
- [ ] 3.3 `test_backfilled_source_noop_is_marked_written`: seed the source the way staging's
  source 228 looks — created with recipe and run columns NULL, then `scan_id` set directly — and
  re-deliver under `wf-b` → `('written', <source>)`.
- [ ] 3.4 `test_source_scan_id_wins_over_a_carrying_row` (D1 order): deliver under `wf-a` (stamps
  `wf-a`'s S1 row); `UPDATE cyl_pipeline_run_scans SET scan_id = S2` on that row; seed `wf-b`
  queued rows for S1 and S2; re-deliver under `wf-b` → S1 row `('written', <source>)`, S2 row
  untouched.
- [ ] 3.5 `test_run_scan_lookup_is_the_backup_when_source_scan_is_null`: deliver under `wf-a` with
  a seeded row; set the source's `scan_id` NULL; seed `wf-b`'s queued row; re-deliver → written.
- [ ] 3.6 `test_noop_with_no_recorded_scan_and_no_carrying_row_reports_no_match`: manual origin,
  `scan_id` NULL → matched false, `wf-b`'s row untouched.
- [ ] 3.7 `test_noop_under_workflow_that_did_not_dispatch_the_scan_matches_nothing`: manual origin
  for S1; `wf-b` has a queued row for S2 only → matched false, S2 row untouched.
- [ ] 3.8 `test_noop_does_not_resurrect_a_failed_row`: `wf-b`'s row `'failed'` → matched false,
  row untouched.
- [ ] 3.9 `test_noop_does_not_replace_another_source_link` (D8): fresh delivery of source X under
  `wf-b` for scan S (row `('written', X)`); then a manual-origin source Y for S (different key) is
  re-delivered under `wf-b` → matched false, row untouched.
- [ ] 3.10 `test_same_key_different_scan_noop_marks_only_the_recorded_scan`: manual origin for S1;
  re-deliver the same key with S2's `image_ids` under `wf-b`, which has queued rows for S1 and S2 →
  S1 `('written', <source>)`, S2 untouched, no `cyl_scan_traits` rows for S2.
- [ ] 3.11 `test_manual_origin_chain_across_two_workflows`: manual, then `wf-b`, then `wf-c` → both
  rows written with the same source.
- [ ] 3.12 `test_noop_without_workflow_name_touches_no_run_scan_row`: `status_update_matched` null,
  every seeded row untouched.
- [ ] 3.13 `test_manual_origin_redelivery_survives_reconciliation`: `wf-b` dispatched two scans; one
  is a manual-origin re-delivery, the other delivers nothing; call
  `fail_cyl_pipeline_run_scans_without_result('wf-b', …)`; counting `written`/`reused` vs `failed`
  gives `(1, 1)` and the rescued row is still `('written', <source>)`.
- [ ] 3.14 `test_noop_reads_no_trait_or_blob_table`: `pg_stat_xact_user_tables` `seq_scan +
  idx_scan` for `cyl_scan_traits` and `cyl_scan_intermediates` is unchanged across a no-op call
  that takes the new path (D1 timeout guard, behavioural half).
- [ ] 3.15 `test_redefinition_keeps_hardening_acl_and_single_overload`: EXECUTE ACL is
  `{postgres, service_role, bloom_writer, bloom_admin, bloom_workflows}`, owner `postgres`,
  `prosecdef` true, `proconfig` pins `search_path`, only the 2-arg overload exists.
- [ ] 3.16 `test_migration_body_is_idempotent`: applying the body a second time succeeds; overloads
  and ACL as 3.15.
- [ ] 3.17 `test_rollback_restores_the_previous_behaviour`: apply the rollback body; 3.1's shape now
  reports matched false; 3.15's assertions hold.

### 3b. `tests/unit/test_cyl_noop_redelivery_migration_files.py` (new)

- [ ] 3.18 `test_differs_from_previous_only_in_the_fallback_block`: ordered removed/added line lists
  (as `M2_REMOVED`/`M2_ADDED` in `test_cyl_trait_recipe_migration_files.py`) for the function
  section; the trailing backfill `SELECT` is absent. Comments are compared too, so the two
  rewritten comments of design D5 are pinned.
- [ ] 3.19 `test_previous_is_the_newest_definition_before_this_one` (`index - 1`, as the
  precedents; no "newest overall" tripwire).
- [ ] 3.20 `test_noop_branch_reads_the_source_row_first`: on comment-stripped text, from
  `IF v_was_noop THEN` to the first `RETURN jsonb_build_object(`: no `cyl_scan_traits` or
  `cyl_scan_intermediates`; `FROM public.cyl_trait_sources WHERE id = v_source_id` appears before
  `FROM public.cyl_pipeline_run_scans WHERE source_id = v_source_id`, which sits inside
  `IF v_scan_id IS NULL`; the targeted update carries `source_id IS NULL OR source_id =
  v_source_id`.
- [ ] 3.21 `test_body_keeps_single_markers`: exactly one `v_was_noop := true;` and one
  `pinned_version constant text :=`.
- [ ] 3.22 `test_full_revoke_is_restated` (migration and rollback) and
  `test_rollback_restores_previous_body_verbatim`.

### 3c. Red run (record output here)

- [ ] 3.23 Write 3a/3b, then create the migration file as an unmodified copy of `20260930120100`'s
  function section. Expected: 3.1, 3.2, 3.3, 3.4, 3.9, 3.10 (S1 half), 3.11, 3.13, 3.14 fail
  (`status_update_matched` false, or the row stays queued, or 3.9's guard absent → relinked);
  3.5–3.8, 3.12, 3.15, 3.16, 3.19, 3.21 pass; 3.17, 3.18, 3.20, 3.22 fail (no rollback / no diff /
  old lookup). Then make the D1/D5/D8 edit and write the rollback.

## 4. PR A implementation (green)

- [ ] 4.1 `supabase/migrations/<ts>_resolve_cyl_noop_redelivery_scan_from_source.sql` per design
  D1, D5, D8; header naming bloom#900/#875, the change id and the rollback path.
- [ ] 4.2 `supabase/rollbacks/<ts>_resolve_cyl_noop_redelivery_scan_from_source_rollback.sql` per
  D5.
- [ ] 4.3 `tests/integration/test_cyl_writeback_rpc.py`: rewrite the three docstrings that become
  false — `test_noop_redelivery_under_new_workflow_name_falls_back_to_scan_id` (hand-submitted
  shape "has nothing to resolve scan_id from"), `…_never_dispatched_workflow_reports_no_match` (the
  lookup now finds the scan; the update matches nothing), `test_fallback_finds_nothing_for_a_never_dispatched_workflow`
  ("v_scan_id never resolves"). No assertion changes.
- [ ] 4.4 Run 3a/3b green: `uv run --extra test pytest tests/integration/test_cyl_noop_redelivery_scan.py -v`;
  `uv run --extra test pytest tests/unit/test_cyl_noop_redelivery_migration_files.py tests/unit/test_cyl_trait_recipe_migration_files.py tests/unit/test_cyl_writeback_a9_migration_files.py tests/unit/test_cyl_trait_sources_grants.py -v`.
  Record counts.

## 5. PR A validation

- [ ] 5.1 `openspec validate fix-cyl-noop-redelivery-scan-resolution --strict`, and the same for
  `fix-cyl-redelivery-blob-collision` and `add-cyl-trait-recipe-key`.
- [ ] 5.2 `scripts/lint_migrations.sh origin/staging` and
  `python3 scripts/lint_migration_isolation.py origin/staging`, before push and again just before
  merge (the queue re-checks against its base).
- [ ] 5.3 `/pre-merge`. Known Windows-only local failures (`test_env_defaults.py`
  `test_validator_*`, `test_weekly_backup.py` collection, `test_verify_env_parity`, some
  fastq/doctor/file-mode tests) are recorded, not fixed.
- [ ] 5.4 CI's `compose-health-check` green: the only run of the existing RPC suite against the new
  body (design D6).

## 6. PR A

- [ ] 6.1 `/pr-description`. Body says "Part of #900, part of #875"; no closing keyword anywhere in
  title, body or commits, including prose such as "fixes #875's …" (`auto-close-issues-on-staging`
  scans title and body; squash bodies come from commits; promotion is a merge commit).
- [ ] 6.2 Schema changes: keep the heading; put the literal line `No schema changes.` on its own
  line outside the template's HTML comment (`scripts/lint_migration_pr_body.py` accepts it only
  there); drop the empty constraints table; `make pr-body-check BODY=<file>`.
- [ ] 6.3 `git push -u origin fix/cyl-noop-redelivery-900` (create the ref via the REST API first
  if the push 500s); open against `staging`. Never rebase once review comments exist; merge
  `origin/staging` in.

## 7. PR B — bloomctl and web (new branch from `staging` after PR A is deployed)

### 7a. Tests first (red)

- [ ] 7.1 `bloomcli/tests/test_cyl_ingest.py`:
  `test_ingest_one_envelope_unmatched_noop_is_a_failure_that_wrote_nothing` —
  `{**RESULT_NOOP, "status_update_matched": False}`, `ARGO_WORKFLOW_NAME` set → `failed`,
  `retriable` false, message has `source_id=55`, "nothing was written" and "not updated", and has
  neither "write-back succeeded" nor "trait/blob data".
- [ ] 7.2 `test_cli_unmatched_noop_reports_already_ingested_then_fails`, plain and `--json`: exit
  non-zero; plain output has "Already ingested (no-op)"; both carry 7.1's message.
- [ ] 7.3 `test_written_mismatch_message_still_says_data_written` (helper and CLI):
  `{**RESULT_OK, "status_update_matched": False}` still has "write-back succeeded" and
  "trait/blob data is correct".
- [ ] 7.4 `test_cli_matched_noop_under_workflow_name_exits_zero`: `{**RESULT_NOOP,
  "status_update_matched": True}`, `ARGO_WORKFLOW_NAME` set → exit 0, "already ingested".
- [ ] 7.5 Batch, `--json`: an unmatched no-op as the only failure exits 0 with `retriable: false`
  and 7.1's message; mixed with a retriable failure, exits non-zero.
- [ ] 7.6 Guards that stay green unchanged: `test_cli_reports_status_update_mismatch_as_a_failure`,
  `test_ingest_one_envelope_reports_status_update_mismatch_as_failed`, both
  `…_status_mismatch_message_does_not_assume_a_single_cause`,
  `test_ingest_one_envelope_noop_redelivery_under_new_workflow_reports_skipped`,
  `test_ingest_one_envelope_noop_is_skipped`, `test_cli_noop_is_not_an_error`.
- [ ] 7.7 Web, `RunDetailLive.test.tsx`: replace the bloom#900 tests (`:569`, `:584`) with "a failed
  no-result row on a scan with results shows no bloom#900 note or warning" (both no-result texts;
  assert no element contains `bloom#900` and the old warning string is absent — not "no note",
  since "Re-run scans without a result" keeps its own); split `:251` keeping its likely-cause
  assertion; re-point `:304-323` at the `current=` cell instead of `NO_OP_NOTE`; drop `noOpNote`
  from the render stub (`:41`) and the `NO_OP_NOTE` import.
- [ ] 7.8 `failure-hints.test.ts`: delete the `isNoOpCandidate` and `NO_OP_NOTE` tests; keep the
  `BACKSTOP_MESSAGE`/`WRITEBACK_NO_RESULT_MESSAGE` source-equality tests and #988's
  `failedScanCause` tests. `RunScansTable.test.tsx`: drop `noOpNote` from fixtures and its
  assertion.
- [ ] 7.9 Red run: 7.1, 7.2, 7.5 fail on the current message; 7.7 fails (the note renders).

### 7b. Implementation (green)

- [ ] 7.10 `ingest.py`: in `ingest_one_envelope` and `cyl ingest-result`, branch the
  `status_update_matched is False` message on `was_noop`; rewrite the comments that assume
  `was_noop=false` (`:853-874`, `:1039-1050`) and `_batch.py:30-31`.
- [ ] 7.11 `bloomcli/CHANGELOG.md` `[Unreleased]` → `### Fixed`: the unmatched-no-op message; that
  a re-delivery of a source first written outside any Bloom run now reports `skipped` (server
  side, PR A); a correction pointer for 0.1.0a7's bloom#875 line.
- [ ] 7.12 `bloomcli/README.md` (`:611-612`, `:636-643`, `:706`): the unmatched-no-op exception and
  its non-zero exit.
- [ ] 7.13 Web: remove `NO_OP_NOTE` and `isNoOpCandidate` from `failure-hints.ts` (rewrite the
  `WRITEBACK_NO_RESULT_MESSAGE` comment, which describes the note), `NO_OP_RERUN_WARNING`,
  `noOpNote`, `noOpAmongFailed` and the note's `note=` use from `RunDetailLive.tsx` (the prop stays
  for `DOUBLE_PROCESSING_WARNING`), `noOpNote` from `RunScansTable.tsx`.
- [ ] 7.14 `rg -n "#900|NO_OP_|isNoOpCandidate|noOpNote|noOpAmongFailed" web bloomcli tests
  services` returns only 7.7's absence assertions; `rg -n "#900" docs openspec/specs bloomcli/*.md`
  (ignoring `deploy-migrations`, which uses #900 as an example number) has no stale claim.
- [ ] 7.15 Green: `cd bloomcli && uv run --extra test pytest tests/test_cyl_ingest.py -m "not
  integration" -v && uv run ruff check`; `cd web && npx vitest run lib/cyl-pipeline
  "app/app/cyl-pipeline-runs/[runId]" && npx tsc --noEmit`; `/pre-merge`; `/pr-description` with
  "Part of #900, part of #875", no closing keywords.

## 8. After deploy (each step needs the author's go-ahead)

- [ ] 8.1 After PR A deploys, read-only: `supabase_migrations.schema_migrations` has `<ts>`; the
  live body's no-op branch contains `FROM public.cyl_trait_sources`; the ACL is the sanctioned set.
- [ ] 8.2 Before each run: `kubectl get pods -n runai-busch-lab` for GPU contention; filter Bloom
  Workflows by `environment=staging`.
- [ ] 8.3 Acceptance A (bloom#900), after PR A: Bloom-dispatched staging run over scan 12894756.
  Read-only before and after: `max(id)`/`count(*)` of `cyl_trait_sources`, and count plus a value
  checksum of `cyl_scan_traits` for the scan. Expect the row `('written', 228)` (the source the
  re-delivery's key matches), no new source, unchanged traits, `failed_count` 0, write-back
  logging the scan skipped.
- [ ] 8.4 Acceptance B (bloom#875): a Bloom-dispatched re-run over one of scans 12894761–64 whose
  source an earlier Bloom run's row carries (check read-only first). Expect
  `('written', <same source>)` and no new source.
- [ ] 8.5 Drafts for the author to approve before posting: evidence comments on #900 and #875.
  Close both by hand only after 8.3/8.4 (bloom#780).
- [ ] 8.6 Draft the sleap-roots-pipeline roadmap update (row-6 E2E note) for the author; do not
  push.
- [ ] 8.7 PR B's bloomctl message reaches the cluster only when the Argo templates' bloomctl pin
  next moves; note it for that bump (author's call, cross-repo).
- [ ] 8.8 Archive only after PR B merges, 8.3–8.5, and 2.5.
