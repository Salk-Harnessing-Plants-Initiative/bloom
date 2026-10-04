## 1. Red: bloomctl tests first

Write these before §2 and run them. Save the output to the scratchpad and paste it into the PR
body. Don't create a standalone red commit: no workflow runs on a feature-branch push.

- **Expected red:** 1.1, 1.2, 1.4, 1.5.
- **Expected green (guards):** 1.3, 1.6.

All tests are in `bloomcli/tests/test_cyl_ingest.py`. Record reconcile calls with the existing
`_record_reconcile` helper and assert `== []`. Don't use a stub that raises:
`_reconcile_unresolved_scans_result` catches every exception, so a raising stub can't fail the
test.

- [x] 1.1 `test_batch_ingest_cli_defers_reconcile_when_an_envelope_fails_retriably`
  - Parametrize the injected failure over `TimeoutError` (generic path) and `_api_error(...)`
    (`map_rpc_error` path).
  - Setup: `ARGO_WORKFLOW_NAME="wf-a"`; the manifest lists `scan_1` and `scan_2`; `scan_1`
    ingests; `scan_2`'s `call_insert_envelope` raises.
  - Assert: - no reconcile call; - no `<reconciliation>` entry in `--json`; - exit 1; - stderr contains `reconciliation deferred to the status poller: 1 envelope(s) failed
retriably`; - stdout is still valid JSON.
- [x] 1.2 `test_batch_ingest_cli_retry_after_a_retriable_failure_marks_the_scan_written` (the
      #1034 regression). Use a stateful fake over a dict of rows:
  - **Rows:** `scan_1`, `scan_2` and `scan_3` start `'queued'`. The run manifest lists only
    `scan_1` and `scan_2`; `scan_3` models a scan whose stage-in failed, which
    `write_run_manifest` leaves out.
  - **Fake `call_insert_envelope`:**
    - on the injected failure, it raises _before_ changing any state;
    - on a first delivery, it sets `'written'` only `WHERE status != 'failed'` and returns
      `status_update_matched` to match;
    - on a second delivery of the same key, it returns `was_noop: True`, matched against the
      row's current status.
  - **Fake `reconcile_unresolved_scans`:** changes `'queued'` to `'failed'` and returns the count.
  - **Attempt 1:** `scan_2` raises a retriable error. Assert `scan_1 == 'written'`,
    `scan_2 == scan_3 == 'queued'`, 0 reconcile calls, and exit 1.
  - **Attempt 2:** same directory and name; `scan_2` succeeds. Assert in this order, so the red
    run fails on the first one:
    1. end state is `scan_1 == scan_2 == 'written'`, `scan_3 == 'failed'`;
    2. exactly one reconcile call, which returned 1;
    3. `--json` shows `scan_1` `skipped` and `scan_2` `ok`;
    4. exit 0.
- [x] 1.3 `test_batch_ingest_cli_missing_declared_and_non_retriable_mismatch_still_reconcile`
  - Setup: the manifest lists `[scan_1, scan_9]`; `scan_1` returns
    `{**RESULT_OK, "status_update_matched": False}`; there is no `scan_9` file.
  - Assert: reconcile is called with `["wf-a"]`; exit 1; `scan_9` is failed and retriable;
    `scan_1` has `retriable is False`; no `<reconciliation>` entry; no deferral line on stderr.
- [x] 1.4 `test_batch_ingest_cli_missing_declared_scan_key_with_a_retriable_failure_defers`
  - Setup: declared `scan_9` is missing alongside a retriable `scan_2` failure.
  - Assert: no reconcile call; exit 1.
- [x] 1.5 Update `test_batch_ingest_cli_isolates_unreadable_file_among_several`
  - It currently asserts `calls == ["wf-corrupt"]`. Change that to `calls == []`.
  - Keep the assertions that the run exits non-zero and that `scan_1` and `scan_3` are still
    ingested.
  - Update its docstring.
- [x] 1.6 Strengthen the two existing tests that mix a retriable failure with a non-retriable
      mismatch (`test_batch_ingest_cli_exits_nonzero_when_a_genuine_failure_also_present` and
      `test_batch_unmatched_noop_with_a_retriable_failure_exits_nonzero`). Add `_record_reconcile` and assert `== []`.
  - The other existing reconcile tests stay unchanged and green. They cover the no-manifest path,
    the missing-declared path, reconcile failure, `PGRST202`, the count log, the unset name, and
    the manifest errors.

## 2. Red: poller tests first

All tests are in `services/workflows/tests/test_status_poller.py`.

- **Expected red:** 2.1, 2.3.
- **Expected green (guards):** 2.2, 2.4, 2.5, 2.6.

- [x] 2.1 `test_sweep_reconciles_a_terminal_workflows_queued_rows_while_a_sibling_still_runs`
  - Setup: run phases `wf-a: Failed`, `wf-b: Running`; queued names `["wf-a"]`; reconcile returns
    2; the recount is `(3, 2)`.
  - Assert:
    - `reconcile_calls == ["wf-a"]`;
    - the status write is `('running', 3, 2)`, using the fresh recount;
    - a docstring cites bloom#1034.
- [x] 2.2 (Done as a test parametrized over `Pending` and `Running`, plus a sibling test parametrized over every terminal phase, and a `PGRST202` test for the running branch.) `test_sweep_does_not_reconcile_a_still_running_workflows_queued_rows`
  - Setup: `wf-a: Running`, `wf-b: Succeeded`; queued names `["wf-a"]`.
  - Assert no reconcile call. This updates or keeps the existing
    `test_sweep_does_not_reconcile_queued_rows_while_still_running`.
- [x] 2.3 `test_sweep_leaves_a_404d_workflows_rows_for_the_run_level_backstop_while_running`
  - Setup: `wf-a` 404s, `wf-b: Running`; queued names `["wf-a"]`.
  - Assert no reconcile call this cycle.
  - It is red only if the implementation must expose per-name phases. Otherwise it is a guard.
    Label it in the PR either way.
- [x] 2.4 `test_sweep_running_run_reconcile_failure_skips_the_status_write`
  - Setup: the per-workflow reconcile raises a non-`PGRST202` error while the rollup is
    `'running'`.
  - Assert: no `update_cyl_pipeline_run_status` call for that run this cycle; the cycle is marked
    unclean; the next candidate is still processed.
- [x] 2.5 `test_sweep_reconciles_rows_write_back_deferred_after_its_final_retry`
  - Setup: phases `["Failed"]`, snapshot `(1, 0, ["wf-a"])`, reconcile returns 2, recount
    `(1, 2)`.
  - Assert `reconcile_calls == ["wf-a"]` and the update is `(run, "failed", 1, 2)`.
  - This maps the modified spec scenario.
- [x] 2.6 Existing reconcile tests in the file stay green: terminal rollup, multiple names, fresh
      recount, unsettled-on-failure, no queued rows, withheld `'complete'` on 404, unresolved
      sibling, `PGRST202`, non-`PGRST202`.

## 3. Integration test (expected green: pins the DB contract D1 relies on)

- [x] 3.1 In `tests/integration/test_cyl_writeback_rpc.py`, add
      `test_retry_attempt_sequence_without_an_intervening_reconcile_ends_written`.
  - **Seed:** one run with 3 queued scans under one `_wf()` name, using the
    `test_batch_workflow_stamps_its_one_run` seeding pattern. Use uuid idempotency keys.
  - **Attempt 1:**
    - deliver `scan_1`;
    - inside `with pg_conn.transaction():` (a savepoint), deliver an envelope for `scan_2` that
      the RPC rejects, and assert it raises and `scan_2` stays `'queued'`;
    - no reconcile.
  - **Attempt 2:**
    - re-deliver `scan_1`; assert `was_noop` and `status_update_matched` are both `true`;
    - deliver a valid `scan_2`; assert `status_update_matched` is `true`;
    - call `fail_cyl_pipeline_run_scans_without_result`; assert it returns `1`;
    - assert the final rows are written / written / failed.
  - **Docstring:** cite `test_late_delivery_after_already_failed_does_not_resurrect` as the
    contrast.
  - **Cleanup:** follow the file's rollback pattern; commit nothing.
  - **Dev DB:** needs the local stack, but **no migration**. Don't run `make migrate-local`.
    Check that the #1022 PR 2 session isn't mid-migration before running.

## 4. Green: implement

- [x] 4.1 **bloomctl:** in `batch_ingest_result`:
  - Initialise `retriable_envelope_failures = 0` before the `if discovered.paths:` branch, so the
    no-envelope path binds it.
  - In that branch, count `ingest_results` entries with `status == "failed" and retriable`.
  - Gate the reconcile on `argo_workflow_name and not retriable_envelope_failures`.
  - When it is skipped for that reason, print
    `reconciliation deferred to the status poller: N envelope(s) failed retriably` with
    `click.echo(..., err=True)`.
  - Update the comment block, the command docstring (one sentence; it is `--help` output), and
    the `reconcile_unresolved_scans` docstring.
- [x] 4.2 **`ScanResult.retriable` docstring** (`_batch.py`): note that a retriable _envelope_
      failure also defers `batch-ingest-result`'s reconciliation, so a failure must not be made
      retriable "just to fail the Workflow".
- [x] 4.3 (Every existing `_fetch_effective_phases` mock now returns a 6th element, `{}`, which keeps its behaviour unchanged. The log messages say "before writing status", since the status may now be `'running'`.) **Poller:**
  - `_fetch_effective_phases` additionally returns the `{workflow_name: phase | None}` map.
  - In `sweep_once`, when the rollup is `'running'`, reconcile only queued names whose phase is in
    `{"Succeeded", "Failed", "Error"}`, then recount. When it is non-`'running'`, keep today's
    behaviour.
  - Reuse the existing failure, `PGRST202` and recount handling. Don't duplicate it.
  - Update the three docstrings/comments that say a leftover `'queued'` row "can only mean
    write-back never ran": `_fetch_effective_phases`, `_reconcile_unresolved_scans`, and the
    `sweep_once` backstop comment.
- [x] 4.4 Run §1–§3; all pass.

## 5. Docs, specs, verification

- [x] 5.1 `bloomcli/README.md` (the `batch-ingest-result` reconcile bullet): describe the
      condition, the stderr line and the hand-off to the poller. Replace "never prevents this
      call from running".
- [x] 5.2 `services/workflows/README.md` (the backstop paragraph): describe per-Workflow
      reconciliation while the run is running, and the run-level backstop for 404s. Add the
      deferred-retry cause.
- [x] 5.3 `web/lib/cyl-pipeline/failure-hints.ts` (comment on `NO_RESULT_MESSAGE`): change "at the
      end of each batch" to "at the end of a batch with no retriable envelope failure".
- [x] 5.4 `bloomcli/CHANGELOG.md` `[Unreleased]` → Fixed, in house style (bloom #1034), saying it
      takes effect with the template pin bump.
- [x] 5.5 `openspec validate fix-cyl-writeback-retry-reconcile --strict`.
- [x] 5.6 (bloomctl: 14 failures that also fail on clean `origin/staging` on Windows, from permissions, symlinks and logs; none in `test_cyl_ingest.py`. Poller suite: 1245 passed. Integration: 174 passed against the dev DB, which already had 20261001220000; no migration run, no rows left. Prettier already fails on staging's copies of the 4 md/ts files, so they aren't reformatted here. Black and ruff-format disagree on one pre-existing assert in `test_status_poller.py`, and the file keeps staging's ruff style.) Run checks with the exact CI invocations:
  - **bloomctl tests:** `cd bloomcli && uv run --extra test pytest tests/ -m "not integration"`
  - **bloomctl lint:** `cd bloomcli && uvx ruff@0.9.9 check .` (the release gate; PR CI doesn't
    lint bloomcli, see #531). Don't run `ruff format` over bloomcli: it isn't enforced and
    would reformat 36 files.
  - **Poller tests:** `cd services/workflows && uv run --frozen --extra test pytest tests/test_status_poller.py -v`
  - **Integration tests:** `uv run --extra test pytest tests/integration/test_cyl_writeback_rpc.py tests/integration/test_cyl_noop_redelivery_scan.py tests/integration/test_cyl_pipeline_status_polling.py -v`
- [x] 5.7 (PR #1038.) Run `/pre-merge` and open the PR to `staging`.
  - Title: "Leave a retried write-back's scans to the status poller so a retry can mark them
    written (Part of #1034)".
  - Use "Part of #1034" only. No closing keyword anywhere in the title or body, including
    quoted tasks. `/pr-description` defaults to `Closes`, so override it.
  - Never merge.

## 5a. /review-pr round 1 (PR #1038, review 5402607775)

- [x] 5a.1 (Reverted in 5b.1.) Poller: closes out _settled_ workflows in every cycle that writes no terminal status.
  - "Settled" means a terminal phase, or a 404 whose `'queued'` rows were all dispatched more than
    the TTL plus 5 minutes ago.
  - This covers `'running'`, an unconcluded rollup, and a withheld `'complete'`.
  - It closes the stuck-forever case for a run whose workflows all 404 (design D2).
  - Tests: `test_a_404_dispatched_longer_ago_than_the_ttl_is_settled`,
    `test_a_recent_or_undated_404_is_not_settled`,
    `test_sweep_closes_a_garbage_collected_workflows_rows_when_no_rollup_concludes`,
    `test_sweep_closes_settled_rows_while_withholding_complete`, and others.
- [x] 5a.2 Poller: a `'running'` run writes its progress even when the close-out or recount fails.
  - It writes the snapshot counts and marks the cycle unclean.
  - Tests: `test_sweep_running_run_reconcile_failure_still_writes_progress`,
    `..._recount_failure_still_writes_progress`, `..._signature_not_found_is_quiet` (asserts no
    WARNING).
- [x] 5a.3 Poller:
  - `_fetch_effective_phases` returns an `EffectivePhases` `NamedTuple`.
  - Each close-out logs how many rows it closed.
  - The backstop text says "…recorded a result for this scan; its result file may exist", kept
    in sync with `failure-hints.ts` `BACKSTOP_MESSAGE` by `failure-hints.test.ts`.
  - The module docstring is updated.
- [x] 5a.4 Poller tests:
  - The sibling test runs through the real `_fetch_effective_phases`, with queued rows on both
    workflows.
  - `settled_workflow_names` replaces the phase map. Existing mocks pass `[]` (nothing settled),
    so the still-running test means what its docstring says again.
  - Several settled workflows are each reconciled once, with one recount.
- [x] 5a.5 bloomctl:
  - Adds the `RECONCILE_DEFERRED_MESSAGE` constant.
  - The comment states the rule is conservative.
  - The `_batch.py` docstring is clarified.
  - Tests: N=2 counting, the non-retriable failure left out of N in both mixed tests, the summary
    on stdout unchanged, and no reconcile and no deferral line when `ARGO_WORKFLOW_NAME` is unset.
- [x] 5a.6 Spec and design:
  - The poller requirement covers settled workflows and the `'running'` write. Its "sole
    remaining exception" wording is corrected.
  - The new scenarios cover a GC'd workflow with no conclusion and a failed close-out in a running
    run.
  - D2/D4/Risks are rewritten: the residual eviction race, the poller-first rollout order, and
    the real error not kept on the row.

## 5b. /review-pr round 2 (PR #1038, review 5402723820)

- [x] 5b.1 Revert 5a.1's 404/TTL "settled" rule. It measured time since dispatch, not since the
      Workflow finished, so it could permanently fail a live batch's rows on a non-GC 404; the
      poller also never received `WORKFLOWS_K8S_TTL_SECONDS`.
  - Now only a confirmed terminal phase is settled, and only while the run is `'running'`.
  - Unconcluded and withheld cycles close nothing and write nothing, as before.
  - Tests: `test_a_404d_workflow_is_never_settled`,
    `test_sweep_leaves_a_404d_workflows_rows_alone_while_the_run_runs_end_to_end`,
    `test_sweep_closes_nothing_and_writes_nothing_without_a_conclusion`.
- [x] 5b.2 Backstop text: "write-back recorded no result for this scan before its workflow
      ended; check whether a result file exists before re-running prediction" (mirrored in
      `failure-hints.ts`).
- [x] 5b.3 `_close_out_workflows`:
  - takes a `context` phrase for logs, instead of a misleading "before writing status …";
  - uses one exception handler;
  - documents its three return outcomes.
- [x] 5b.4 Tests:
  - `ok = ok and clean` is pinned in both branches
    (`test_a_clean_close_out_does_not_clear_an_earlier_runs_unclean_cycle`);
  - a non-`PGRST202` `APIError` is tested on the running branch;
  - the bloomctl deferral literal is pinned.
- [x] 5b.5 Design: D2 and Risks are rewritten (404 never settled, and why); the stuck-run case is
      left for the follow-up issue (5b.6).
- [x] 5b.6 Filed as bloom#1042 (approved): deferred rows of a 404'd Workflow in an
      unconcluded or withheld run, the pre-existing withheld-`'complete'` stall, and the
      pre-existing `'partial'`-run re-polling. Link it here and in the PR body.

## 6. Post-merge rollout (blocks archive)

Archive this change **before** `fix-cyl-poller-unconcluded-runs` (bloom#1042). Both MODIFY "A
standalone poller periodically reconciles…", and that change's delta is written on top of this one's.

- [ ] 6.1 Confirm that `docker-build-bloomcli` published `sha-<squash short>`, using
      `docker buildx imagetools inspect ghcr.io/salk-harnessing-plants-initiative/bloomctl:sha-<short>`.
  - If the build was cancelled by a later staging push (concurrency `cancel-in-progress`), use
    the next published staging sha that contains the merge, and list the commits it carries
    along.
  - Record the digest and the rollback target: `sha-88cbcbf@sha256:0259ec0a…`.
  - Re-run `git log origin/main..origin/staging -- bloomcli`. Confirm that no ride-along commit
    needs an RPC or migration that prod lacks, because the bump reaches prod at once (design D4).
- [ ] 6.2 In sleap-roots-pipeline (WSL; kubectl and argo live there), run
      `bash scripts/check_cluster_drift.sh`. Record the before state: comparator, namespace, date,
      exit code.
- [ ] 6.3 **Only after prod's workflows service runs this commit** (6.5; design D4: the template
      bump reaches prod at once, the poller only with a staging→main promotion), and with the
      user's OK, open a sleap-roots-pipeline PR that bumps bloomctl in **all three**
      templates (write-back, images-downloader, exit-gate) to the immutable sha and digest.
  - Follow the upstream pin-comment format: sha, digest, squash commit, version, what changed,
    ride-along commits, rollback target.
  - Use "Part of salk-harnessing-plants-initiative/bloom#1034", never `Closes`.
  - Run `scripts/check_manifests.py` / `check_all.sh` by hand (upstream has no CI).
  - No bloom `SLEAP_ROOTS_PIPELINE_REF` bump is needed: the vendored Workflow doesn't change.
- [ ] 6.4 After the PR merges and the templates are re-registered:
  - re-run `check_cluster_drift.sh`;
  - run `kubectl -n runai-busch-lab get workflowtemplate sleap-roots-write-back-template -o jsonpath='{.spec.templates[0].container.image}'`;
  - record that the digest matches.
- [ ] 6.5 (Do before 6.3.) Confirm the poller half is live: the workflows service on staging and prod is running the merge
      commit (`BUILD_SHA` or the image tag). Prod gets it at the next staging→main promotion.
- [ ] 6.6 Evidence: the first run whose write-back step retried, or a run with a scan failing
      write-back in a multi-Workflow run, shows:

  - the `reconciliation deferred` stderr line in the write-back pod log;
  - a retried scan ending `'written'`, or a failed scan reading `'failed'` while sibling
    Workflows were still running.

  Then mark bloom#1034 done by hand, and open the archive PR separately.

- [ ] 6.7 Prod run 2's 5 stale rows are tracked in bloom#1035 (already filed); link it here. Post any comment there only with the
      user's OK.
