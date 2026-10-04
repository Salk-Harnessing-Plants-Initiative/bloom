This change ships as two PRs (design D7). PR A covers §1–4. PR B covers §5–11 and is opened
only after PR A is deployed to staging.

How each "Red" section works:

- Write and run it before its "Green" section.
- Save the red run's output to the scratchpad and paste it into the PR body. Don't make a red-only
  commit.
- **Red** means the test must fail before the change. **Guard** means it passes before and after.

# PR A: database

## 1. Red: SQL (integration, real Postgres via `pg_conn`)

New file: `tests/integration/test_cyl_pipeline_run_workflows.py`.

- Import the seed helpers from `test_cyl_pipeline_status_polling.py` / `test_cyl_writeback_rpc.py`
  where that's possible.
- Cleanup deletes `cyl_pipeline_run_workflows` rows before their run, because the FK has no
  cascade.

- [x] 1.1 `test_record_phase_inserts_a_first_terminal_phase`
- [x] 1.2 `test_record_phase_same_phase_is_a_noop`: returns `false`; `observed_at` is unchanged.
- [x] 1.3 `test_record_phase_different_phase_replaces_and_advances_observed_at`
- [x] 1.4 `test_record_phase_unknown_workflow_for_run_writes_nothing`: covers both a name no row
      carries and a name only another run carries.
- [x] 1.5 `test_record_phase_rejects_non_terminal_phase`: parametrized over `'Running'`,
      `'Pending'`, `''` and `NULL`.
- [x] 1.6 `test_record_phase_works_as_bloom_workflows_without_table_write_grant`: run it under
      `SET LOCAL ROLE bloom_workflows`.
- [x] 1.7 `test_concurrent_record_phase_same_key_does_not_raise`: two connections via
      `pg_conninfo`.
- [x] 1.8 `test_close_run_workflow_scans_is_scoped_to_its_run`: runs `r1` and `r2` share `'wf-a'`.
      Assert the return of 1, that the other rows are untouched, that a second call returns 0, and
      that `'written'`/`'failed'` rows are left as they are.
- [x] 1.9 `test_run_workflows_privileges`: covers every grant and privilege in the spec scenario
      "Only bloom_workflows may call the functions…", including the SELECT policy checks under
      `SET LOCAL ROLE`.
- [x] 1.10 `test_run_workflow_functions_are_hardened`: both are `SECURITY DEFINER` with
      `search_path = pg_catalog, public`.
- [x] 1.11 `test_run_workflows_migration_is_idempotent` and
      `test_run_workflows_rollback_restores_the_previous_status_rpc`. Follow the existing
      idempotency and rollback tests in `test_cyl_pipeline_status_polling.py`.

In `tests/integration/test_cyl_pipeline_status_polling.py`:

- [x] 1.12 Replace `test_update_accepts_partial_as_a_source_state_and_advances_completed_at` with
      `test_dispatch_settled_partial_is_confirmed_once_then_final`.
  - The first call writes `'partial'` and stamps both columns.
  - The second call is parametrized over `'partial'`/`'failed'`/`'complete'`/`'running'` with
    different counts, and changes nothing.
- [x] 1.13 `test_running_write_never_stamps_poller_concluded_at`: covers both `'submitted'` →
      `'running'` and dispatch-settled `'partial'` → `'running'`.
- [x] 1.14 `test_concurrent_terminal_writes_conclude_once`: two connections write `'partial'` and
      `'failed'`; exactly one takes effect.
- [x] 1.15 Guard: the existing tests "a run already complete or failed is untouched" and "with
      counts" also assert that `poller_concluded_at` is unchanged.

In `tests/integration/test_cyl_writeback_rpc.py`:

- [x] 1.16 Full-flow test beside the existing reconcile, status and counts test: reconcile, then a
      `'partial'` status write, then a later `'failed'` write that does nothing. The counts stay as
      first written.

**Red:** 1.1–1.14 and 1.16. **Guard:** 1.15's original assertions.

Recorded red run: 23 failed, 20 passed. 1.5 passed vacuously before the migration (the missing
function raised `psycopg.Error`); it is meaningful only against the migrated schema.

Also changed: `test_cyl_pipeline_dispatch.py::test_rollback_removes_everything` now runs this
change's rollback first, since `cyl_pipeline_run_workflows` references `cyl_pipeline_runs`.

## 2. Green: migration

Use the `database-migration` skill. Choose the timestamp when the file is created; it must be
later than the newest migration on staging at PR time.

- [x] 2.1 Write `supabase/migrations/<ts>_add_cyl_pipeline_run_workflows.sql`. It must be
      re-runnable (`IF NOT EXISTS`, `DROP POLICY IF EXISTS`, `CREATE OR REPLACE`, and named
      constraints guarded in `DO` blocks). It contains:
  - `SET LOCAL lock_timeout = '5s'` right after `BEGIN`;
  - the table with its named PK, FK and CHECK;
  - `REVOKE ALL ... FROM PUBLIC, anon, authenticated, service_role, bloom_user, bloom_writer,
bloom_agent, bloom_admin, bloom_workflows`, then the SELECT grants and policies, and ALL for
    `bloom_admin`;
  - both new functions, each with a triple REVOKE and a single GRANT;
  - `ADD COLUMN IF NOT EXISTS poller_concluded_at`;
  - `CREATE OR REPLACE update_cyl_pipeline_run_status` with the D6 guard and stamping, with its
    grants re-issued;
  - `NOTIFY pgrst, 'reload schema';` after `COMMIT`.
- [x] 2.2 Write `supabase/rollbacks/<ts>_add_cyl_pipeline_run_workflows_rollback.sql`.
  - Its header says to redeploy code that doesn't use these objects first.
  - It restores the `20260912111000` body of `update_cyl_pipeline_run_status`, drops both functions,
    the table and the column, then runs `NOTIFY pgrst`.
- [x] 2.3 Hand-edit `web/lib/database.types.ts`: the new table and both functions' Args and
      Returns. Don't bulk-regenerate the file. The PR body notes that the `packages/*` copies are
      left as they are.
  - `poller_concluded_at` is **not** added to the `cyl_pipeline_runs` types here. `RunRow` is that
    table's Row type, and three app files build full `RunRow` literals
    (`components/cyl-pipeline/ExperimentRunsPanel.tsx`, `lib/cyl-pipeline/__fixtures__/rows.ts`,
    `lib/cyl-pipeline/realtime-reducer.test.ts`), so the column breaks `tsc` until they change.
    App files can't ride in a migration PR, so this moves to task 9.5.
- [x] 2.4 Run `make erd`, or commit CI's artifact, so `_WIKI/SUPABASE/erd.md` is current.
- [x] 2.5 Run `./scripts/lint_migrations.sh origin/staging` and then §1, against a fresh
      `make migrate-local`. All green.

## 3. Verification for PR A

- [x] 3.1 Run `openspec validate fix-cyl-poller-unconcluded-runs --strict`.
- [x] 3.2 Run `/pre-merge`, and `cd web && npm run build` (which type-checks the edited types).
- [x] 3.3 Fill in the PR body's **Schema changes** section:
  - a mermaid `erDiagram` showing `cyl_pipeline_runs` and `cyl_pipeline_run_workflows`;
  - a constraints table listing the PK, FK and CHECK;
  - the new column.

  Run `make erd-snapshot CHANGED=origin/staging` and `make pr-body-check BODY=<file>`.

- [x] 3.4 Add one line to `fix-cyl-writeback-retry-reconcile/tasks.md` §6 saying it must be
      archived before `fix-cyl-poller-unconcluded-runs`.
- [x] 3.5 In the PR body, note that the only text the poller requirement drops is the "exit gate
      routes more runs into it" sentence in "`'complete'` does not imply…".

- [x] 3.6 Before PR A merges, record a read-only baseline on staging and prod: every
      `'partial'`/`'running'`/`'submitted'` run with its `completed_at` and workflow names. The old
      poller makes each existing `'partial'` final within a cycle of the migration (design D6).
  - **Recorded 2026-10-04** (read-only, `default_transaction_read_only=on`):
    - **prod:** no open runs.
    - **staging:** no `'partial'` runs, so the old poller has nothing to make final early. One
      open run: **run 17**.
  - **Run 17:**
    - `'running'` since 2026-09-30, 1,515 scans across 61 workflows, every row `'queued'`,
      `done_count` 0.
    - This is bloom#1042 item 1. PR A doesn't change it.
    - PR B's removal rule should conclude it. Check that in 11.3.
- [x] 3.7 Review follow-ups (PR #1045 review): service_role revoke and `OWNER TO postgres` on the
      functions, FK guard compares its definition, close-out refuses a blank message, deterministic
      conclude-once test, phase-guard test matching the RPC's own error, barrier timeouts and
      hung-thread checks, defaults/Realtime/admin-grant/close-as-bloom_workflows tests.

## 4. After PR A merges

- [x] 4.1 Once deployed to staging, confirm the table, the functions and the column exist, and that
      the old poller still writes run status. This is a read-only check over the staging SSH
      access.
  - **Checked 2026-10-04** (deploy run 37221976279, read-only):
    - migration `20261004120000` is recorded;
    - the table and `poller_concluded_at` exist;
    - all three functions are SECURITY DEFINER, owned by `postgres`, with EXECUTE held only by
      `bloom_workflows`;
    - there are no stored phases and no concluded runs;
    - run 17 is unchanged (`'running'`, 1,515 `'queued'`).
  - The redeployed `cyl-status-poller` sweeps run 17 every cycle, and all 61 of its workflows return
    `404 Not Found`. That is bloom#1042 item 1 live: an empty rollup, so no write. Run 17 is the only
    candidate, so no status write could be watched on staging.

# PR B: code (after PR A is on staging)

## 5. Red: `k8s_client`

All in `services/workflows/tests/test_k8s_client.py`.

- [x] 5.1 `test_get_workflow_status_raises_on_unverified_404`, parametrized over:
  - a body whose `.json()` raises (a new `_FakeResp` subclass);
  - `{}`;
  - a JSON list;
  - `reason: "Forbidden"`;
  - `details` set to `null` or to a string;
  - `details.name` missing or naming another workflow;
  - `details.kind: "pods"`;
  - another `details.group`.

  Each case raises `K8sStatusError` with the generic message, never `AttributeError`, and the body
  appears only in the log.

- [x] 5.2 `test_get_workflow_status_returns_none_for_another_runs_label`, plus
      `..._returns_phase_for_own_or_missing_label`.
- [x] 5.3 Replace the empty-body `test_get_workflow_status_returns_none_on_404` with
      `..._returns_none_on_verified_not_found`. This one is a guard.
- [x] 5.4 Rewrite the two `get_workflow_status` tests that monkeypatch `k8s_client.get_workflow` so
      they drive the HTTP fake instead.
- [x] 5.5 Guard: `test_get_workflow_still_returns_none_on_any_404`, with the same parametrization
      as 5.1.

**Red:** 5.1, 5.2. **Guard:** 5.3, 5.5. 5.4 is rewritten so it stays meaningful.

Recorded red run: 14 failed, 165 passed (all 11 unverified-404 cases returned `None`; the
three label tests had no `run_id` parameter). Green: the whole `services/workflows` suite,
1,282 passed.

## 6. Green: `k8s_client`

- [x] 6.1 Add a private fetch shared by `get_workflow` and `get_workflow_status`, plus
      `_is_verified_not_found(resp, name)`. Add the `run_id` parameter and the label check.
- [x] 6.2 Update the docstrings: the module docstring's TTL framing, `K8sStatusError` (no longer
      "non-404" only), `get_workflow_status`.
- [x] 6.3 Confirm §5 and `test_rnaseq_status_poller.py` are green.

## 7. Red: poller

All in `services/workflows/tests/test_status_poller.py`.

Test hooks:

- `_FakeClient` gains a recording `.rpc(name, params)` that returns configurable data or raises.
  Every test that runs the real `_fetch_effective_phases` with a terminal phase uses it, so record
  calls are seen, not swallowed.
- The clock is patched through `worker._monotonic` and `worker._utcnow`.
- `_row()` defaults `created_at` to a timestamp older than the TTL.
- A new autouse fixture `_reset_not_found_tracker` resets the tracker between tests.
- A record failure reaches `sweep_once` through one new `EffectivePhases` field, `record_clean`.
  Every test that patches `_fetch_effective_phases` with a raw tuple is moved to a helper that
  builds the named tuple.

Tests:

- [ ] 7.1 `test_terminal_phase_is_recorded_once_and_used_after_gc`
- [ ] 7.2 `test_record_phase_sends_the_real_rpc_shape` and
      `test_fetch_stored_phases_reads_only_this_runs_rows`
- [ ] 7.3 `test_same_stored_phase_makes_no_record_call`, with a positive control in the same test:
      a different stored phase does make the call.
- [ ] 7.4 `test_live_phase_wins_over_stored_phase`
- [ ] 7.5 `test_gone_never_seen_workflow_is_removed_after_grace`. Assert:
  - nothing is called on cycles 1–2, or before the grace period ends;
  - on removal, the close-out goes through `close_cyl_pipeline_run_workflow_scans` with this run id
    and the removed message;
  - the run is written `'failed'` with counts 2/1.
- [ ] 7.6 `test_removed_workflow_with_all_rows_written_counts_succeeded` and
      `test_removed_workflow_with_written_and_failed_rows_counts_failed`
- [ ] 7.7 `test_not_found_too_soon_after_row_creation_is_never_removed` and
      `test_newest_created_at_governs_ttl_guard`, each with a positive control: the same setup with
      old rows is removed.
- [ ] 7.8 `test_not_found_count_resets_on_any_other_result`: a live phase in between, and a run
      check error that resets every pair of that run. Positive control: three more cycles after
      the reset do remove it.
- [ ] 7.9 `test_tracker_is_keyed_by_run_and_name` and `test_tracker_drops_pairs_not_looked_up`
- [ ] 7.10 `test_removed_close_out_failure_leaves_workflow_unresolved`: parametrized over an error
      (cycle unclean) and `PGRST202` (cycle clean). In both, no status is written.
- [ ] 7.11 `test_terminal_conclusion_waits_for_an_unresolved_sibling`: no close-out and no write,
      then a conclusion once the sibling is removed.
- [ ] 7.12 `test_unconcluded_partial_with_unresolved_workflow_is_not_turned_failed`
- [ ] 7.13 `test_gcd_succeeded_sibling_keeps_partial_run_partial`, read through the real stored
      phase fetch.
- [ ] 7.14 `test_failed_record_call_keeps_live_phase_and_marks_cycle_unclean` and
      `test_record_phase_pgrst202_is_quiet_and_clean`
- [ ] 7.15 `test_poller_concluded_partial_is_not_a_candidate`, which replaces the assertion that
      every partial run is a candidate.
- [ ] 7.16 `test_stored_phase_workflow_queued_rows_closed_while_running`
- [ ] 7.17 `test_all_poller_close_outs_are_run_scoped`: the backstop and the running-run
      close-out both call `close_cyl_pipeline_run_workflow_scans` with `p_run_id`.
- [ ] 7.18 `test_created_at_parses_postgrest_timestamps`: `+00:00`, `Z`, 5-digit fractions.
- [ ] 7.19 `test_grace_env_resolution` (malformed or non-positive values fall back with a warning)
      and `test_non_positive_ttl_disables_removal_with_a_warning`
- [ ] 7.20 Guard: `test_dispatch_settled_partial_is_still_a_candidate`
- [ ] 7.21 Update the tests that pin today's 404 behaviour so they describe an **unresolved**
      workflow, and so they expect the new withhold-every-conclusion rule:
  - `test_rollup_skips_a_404d_workflow_rather_than_guessing`
  - `test_a_404_alongside_an_observed_succeeded_sibling_is_flagged_as_unknown`
  - `test_sweep_withholds_complete_when_a_workflow_is_unresolved_this_cycle`
  - `test_sweep_withheld_complete_on_404_never_reaches_reconciliation`
  - `test_a_404d_workflow_is_never_settled`, which also asserts no record call and a tracker count
    of 1
  - `test_sweep_leaves_a_404d_workflows_rows_alone_while_the_run_runs_end_to_end`, with the same
    extra assertions
  - `test_sweep_closes_nothing_and_writes_nothing_without_a_conclusion`
  - `test_sweep_still_concludes_failed_or_partial_despite_an_unresolved_workflow` and
    `test_sweep_still_reconciles_partial_or_failed_despite_an_unresolved_sibling_workflow`. These
    two are **inverted**: rename them to `..._waits_...` and assert there is no write.
- [ ] 7.22 Update every `_reconcile_unresolved_scans` stub (`_patch_sweep` and the inline lambdas)
      to the new signature `(client, run_id, name, message)`.

**Red:** 7.1, 7.2, 7.4–7.19 and the two inverted tests in 7.21. **Guard:** 7.3's control, 7.20,
the rest of 7.21, and 7.22.

## 8. Green: poller

- [ ] 8.1 Add the grace resolver. Import `k8s_client.TTL_SECONDS`, and warn at startup if it is
      ≤ 0.
- [ ] 8.2 Add `_fetch_stored_phases` and `_record_phase`, and make the close-outs run-scoped. The
      messages become module constants `_BACKSTOP_MESSAGE` and `_REMOVED_MESSAGE`.
- [ ] 8.3 Add the `_NotFoundTracker` with `(run_id, name)` keys, resets and pruning.
- [ ] 8.4 Extend `_fetch_effective_phases`:
  - pass `run_id` to the lookups;
  - resolve each effective phase;
  - close out removed workflows;
  - include stored phases in the "settled" set;
  - make `any_unknown` mean "unresolved".
- [ ] 8.5 In `sweep_once`, withhold every terminal conclusion while any workflow is unresolved
      (design D5).
- [ ] 8.6 In `_fetch_candidate_runs`, select `id, status, poller_concluded_at` and filter in code.
- [ ] 8.7 Update the docstrings and comments: the module docstring, `_fetch_effective_phases`,
      `_reconcile_unresolved_scans`, and `sweep_once`'s addendum-8 block.
- [ ] 8.8 Confirm §7 and the whole `services/workflows` suite are green.

## 9. UI: red, then green

- [ ] 9.1 Red, in the `RunDetailLive.test.tsx` "re-run actions" block:
  - "does not offer a failed row whose late result is this run's": 3 failed rows, 1 with a note.
    Expect "Re-run failed scans (2)" and that the submit sends the other 2 ids. The excluded row
    still reads failed with its note and is still counted in the header.
  - "offers every failed row when the latest-source lookup fails"
  - "leaves a late-result row out of Re-run scans without a result" (the spec's 9-id case)
  - "hides Re-run failed when every failed row shows a late-result note"
  - "drops a row from Re-run failed once its late-result lookup lands", using fake timers
- [ ] 9.2 Green: in `RunDetailLive.tsx`, `failedIds` and `unresultedIds` skip rows with a
      `lateResultNote`. Update the "Re-run actions" docstring at the top of the file.
- [ ] 9.3 Update `failure-hints.ts`:
  - add the removal close-out to `lateResultNote`'s list of ways a row gets closed;
  - export `REMOVED_WORKFLOW_MESSAGE`;
  - point the `BACKSTOP_MESSAGE` and the new source-equality tests at the poller's constants (red,
    then green).
- [ ] 9.4 Update the comments in `run-display.ts` (only dispatch stamps an early `completed_at`)
      and `realtime-reducer.ts` (a concluded `'partial'` run gets no more sweep updates).
- [ ] 9.5 Add `poller_concluded_at` to the `cyl_pipeline_runs` Row, Insert and Update types in
      `web/lib/database.types.ts`, and to the three `RunRow` literals named in task 2.3.
- [ ] 9.6 Confirm `cd web && npm run test:unit` is green.

## 10. Config and docs

- [ ] 10.1 Pass `WORKFLOWS_K8S_TTL_SECONDS` to both `cyl-status-poller` and `rnaseq-status-poller`:
  - `${WORKFLOWS_K8S_TTL_SECONDS:-3600}` in dev, `${WORKFLOWS_K8S_TTL_SECONDS}` in prod. This keeps
    `test_the_poller_has_the_cyl_pollers_environment` green.
  - Update the "submission-only" comments in both compose files, and note that the NotFound
    tracker is kept per replica.
  - Leave `WORKFLOWS_NOT_FOUND_GRACE_SECONDS` unset.
- [ ] 10.2 Update the `.env.prod.defaults`, `.env.staging.defaults` and `.env.dev.example`
      comments, which file the TTL under `cyl-pipeline-worker` only.
- [ ] 10.3 Update `services/workflows/README.md`:
  - the partial-sweep paragraph;
  - the "404 is permanent" / addendum-8 paragraph;
  - replace the "render unknown" counts note;
  - the TTL row (drop "cyl-pipeline-worker only");
  - a new `WORKFLOWS_NOT_FOUND_GRACE_SECONDS` row;
  - the RNA-seq paragraph, which says the sleap-roots TTL doesn't apply.

## 11. Verification for PR B, then after merge

- [ ] 11.1 Run `openspec validate --strict`, then each of:
  - `cd services/workflows && uv run --frozen --extra test pytest tests/ -q`
  - `uv run --extra test pytest tests/unit/`
  - `cd web && npm run test:unit && npm run build`
  - `pre-commit run --all-files` (CI has no ruff or black step for `services/workflows`)
  - `/pre-merge`
- [ ] 11.2 Run a read-only query on staging and prod listing the candidate runs the new poller will
      conclude, and their expected outcomes. Record them in the PR body. CI's DB is empty, so this
      is the only check against real data.
- [ ] 11.3 After staging deploy, compare those runs with their actual status, counts and closed
      rows. Staging run 17 (task 3.6) must end concluded with no `'queued'` rows. Confirm a fresh run's workflows appear in `cyl_pipeline_run_workflows`. Record the
      results here.
- [ ] 11.4 Promote PR A to main before or together with PR B. #1038's poller must already be on
      main.
- [ ] 11.5 Archive `fix-cyl-writeback-retry-reconcile` first, then this change, after the prod
      check (repeat 11.3 on prod). Re-run `openspec validate --strict` after the first archive.
