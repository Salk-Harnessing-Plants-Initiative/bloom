## 1. Red: tests first

Write these and run them before section 2. Capture the red output to the scratchpad and paste it
into the PR body. Don't create a standalone red commit: no workflow runs on a feature-branch push,
so a red commit proves nothing.

- [ ] 1.1 `bloomcli/tests/test_cyl_ingest.py`: add
      `test_batch_ingest_cli_defers_reconcile_when_an_envelope_fails_retriably`.
      - Setup: `ARGO_WORKFLOW_NAME="wf-a"`, a manifest listing `scan_1` and `scan_2`, `scan_1`
        ingests ok, and `scan_2`'s `ingest_one_envelope` returns `failed` with `retriable=True`.
      - Assert `reconcile_unresolved_scans` is **never** called.
      - Assert the output has no `<reconciliation>` entry.
      - Assert exit 1.
- [ ] 1.2 Add `test_batch_ingest_cli_retry_after_a_retriable_failure_marks_the_scan_written`, the
      #1034 regression.
      - Use a stateful fake that models `cyl_pipeline_run_scans` for one workflow:
        - Rows `scan_1`, `scan_2` and `scan_3` (dispatched, but with no envelope) start `'queued'`.
        - The fake's insert sets a row `'written'` only `WHERE status != 'failed'` and returns
          `status_update_matched` to match.
        - The fake's reconcile sets `'queued'` → `'failed'`.
      - Attempt 1: `scan_2`'s insert raises a retriable error. Assert exit 1.
      - Attempt 2: the same directory and workflow name, with `scan_2`'s insert now succeeding.
      - Assert the end state: `scan_1` and `scan_2` are `'written'` and `scan_3` is `'failed'`.
      - Assert attempt 2 exits 0 and reports no `status_update_matched` mismatch.
      - Run it against the current code and confirm it fails with `scan_2 == 'failed'`.
- [ ] 1.3 Add `test_batch_ingest_cli_non_retriable_failure_still_reconciles`.
      - Setup: the only failure is a non-retriable `status_update_matched` mismatch.
      - Assert the reconcile is called once and the exit is 0.
- [ ] 1.4 Add `test_batch_ingest_cli_missing_declared_scan_key_with_a_retriable_failure_defers`.
      - Setup: a missing declared `scan_9` alongside a retriable `scan_2` failure.
      - Assert there is no reconcile call and the exit is 1. The retriable envelope is what
        decides it.
- [ ] 1.5 Update `test_batch_ingest_cli_isolates_unreadable_file_among_several` (`:1819`). Today
      it asserts `calls == ["wf-corrupt"]` ("reconciliation must still run despite the corrupt
      file"). An unreadable file is a retriable envelope failure, so it now defers. Assert
      `calls == []`, exit non-zero, and that `scan_1` and `scan_3` are still ingested. Update its
      docstring to match.
- [ ] 1.6 Re-run the existing reconcile tests unchanged. These must stay green, proving the
      no-manifest and missing-declared paths still reconcile:
      - `:2035`, `:2070`, `:2094`
      - `:2119`–`:2330`
      - `:2481`
      - `:2789`, `:2815`, `:2835`
- [ ] 1.7 `tests/integration/test_cyl_writeback_rpc.py`: add
      `test_retry_delivery_without_an_intervening_reconcile_marks_written`. It pins the DB-level
      contract D1 relies on, against the real RPCs:
      - Two scans are queued under `wf-a`.
      - Deliver `scan_1`.
      - Deliver `scan_2` as the "retry", with no reconcile in between.
      - Call `fail_cyl_pipeline_run_scans_without_result('wf-a')`.
      - Assert both rows are `'written'`, the call returned `0`, and both deliveries'
        `status_update_matched` is `true`.
      - This needs the local stack but **no migration**. Don't run `make migrate-local`. Check
        that the other session (#1022 PR 2) isn't mid-migration before running integration tests,
        and leave no test rows behind.

## 2. Green: implement

- [ ] 2.1 In `batch_ingest_result` (`bloomcli/src/bloomctl/cyl/ingest.py`), compute
      `retry_could_still_write = any(r.status == "failed" and r.retriable for r in ingest_results)`.
      - It is computed over `ingest_one_envelope` results only, so it is `False` when nothing was
        ingested.
      - Gate the existing `_reconcile_unresolved_scans_result` call on
        `argo_workflow_name and not retry_could_still_write`.
      - When it is skipped for that reason, log at info level that reconciliation is deferred to
        the status poller, naming the count of retriable envelope failures.
- [ ] 2.2 Update the comment block and the command docstring to state the rule and point to
      bloom#1034. Leave `status_update_matched_message` as it is: a late delivery after the
      poller closed a row is still a real cause.
- [ ] 2.3 Run section 1. All tests pass.

## 3. Docs and specs

- [ ] 3.1 `bloomcli/README.md` (`batch-ingest-result`, around lines 735-755): describe the
      reconcile condition and the hand-off to the status poller. Replace "never prevents this
      call from running" for unreadable files.
- [ ] 3.2 `services/workflows/README.md` (around line 380) and the `status_poller.py`
      docstring/comment that say a leftover `'queued'` row "can only mean write-back never ran":
      add the deferred-after-final-retry cause. Make no code change in the poller.
- [ ] 3.3 `bloomcli/CHANGELOG.md`: add an Unreleased "Fixed" entry citing bloom#1034.
- [ ] 3.4 Run `openspec validate fix-cyl-writeback-retry-reconcile --strict`.

## 4. Verify

- [ ] 4.1 Run the bloomcli test suite, then `ruff check` and `ruff format --check` in `bloomcli/`.
- [ ] 4.2 Run the integration test from 1.7 and the existing reconcile, guard and poller suites:
      - `tests/integration/test_cyl_writeback_rpc.py`
      - `tests/integration/test_cyl_noop_redelivery_scan.py`
      - `services/workflows/tests/test_status_poller.py`
- [ ] 4.3 Run `/pre-merge`, then open the PR to `staging` (`Part of #1034`). Never merge.

## 5. Post-merge: rollout (blocks archive)

- [ ] 5.1 Confirm the bloomctl image for the merge commit was published (`sha-<short>` tag and
      digest).
- [ ] 5.2 With the user's OK, open a sleap-roots-pipeline PR bumping
      `sleap-roots-write-back-template.yaml`'s bloomctl pin to that digest. After it merges,
      confirm the template is re-registered on the cluster (`scripts/check_template_contract.py`
      or `argo template get`).
- [ ] 5.3 Evidence: on the first run whose write-back step retried, or on a deliberately induced
      retriable failure in staging, `cyl_pipeline_run_scans` for the scan shows `'written'`, not
      `'failed'`. If no such run happens soon, record the bloomctl log line "reconciliation
      deferred" from a staging run instead. Then close #1034 and archive.
