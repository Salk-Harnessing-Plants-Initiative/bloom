> **Commit grouping**: four commits in one PR against `staging` (per this repo's proposal+implementation
> convention). This mirrors PR #469's *content* (claim/complete/fail shape, worker loop, compose wiring)
> but not its commit shape — #469 landed as one monolithic commit; Phase 1 (`add-cyl-pipeline-trigger`)
> is the actual precedent for splitting by layer with tests bundled into the same commit as their
> implementation (verified against that change's real pre-squash commits, not just its squash-merge).
> 1. `feat(db): add claim/complete/fail wrapper functions for cyl_pipeline_dispatch (#404)` — sections 1+2.
> 2. `feat(workflows): dispatch queued pipeline batches to Argo via K8s API (#11)` — sections 3+4.
> 3. `chore(deploy): add cyl-pipeline-worker service to compose (#11)` — section 5. **Must land after
>    commit 2**, not before and not combined ahead of it — a compose service referencing
>    `dispatch_worker.py` before that file exists fails CI's compose-health-check job immediately
>    (confirmed: it fails the build on any exited container).
> 4. `docs(workflows): document dispatch worker, K8s credential vars, namespace v1 limitation (#11)` —
>    section 6.
> Sections 3+4 have no dependency on 1+2 (the worker's tests mock every DB call), so if useful they could
> be written/reviewed first — but land in this order regardless, matching the DB-layer-first convention
> Phase 1 used.

## 1. Wrapper-function tests first (red)

- [x] 1.1 Extend `tests/integration/test_cyl_pipeline_dispatch.py` (the file Phase 1 created for the
      `enqueue_cyl_pipeline_batch` tests — same `pg_conn` fixture, same rollback-wrapped pattern; do
      **not** create a second file for the same queue). Write, and confirm currently **fail**
      (functions don't exist yet):
  - `test_claim_returns_enqueued_batch_and_hides_it` — enqueue a batch, claim it, assert the returned
    `run_id`/`batch_index`/`scan_ids`/`msg_id` match, then assert a second immediate claim returns
    nothing (visibility timeout).
  - `test_claim_on_empty_queue_returns_nothing` — no enqueue; assert claim returns no rows.
  - `test_claim_redelivers_after_visibility_timeout_expires` — claim with a short `p_vt`; wait past
    it (or manipulate `pgmq`'s underlying visibility column directly, matching PR #469's
    `test_claim_dead_letters_poison_message`'s technique for manipulating `read_ct`); assert a second
    claim succeeds.
  - `test_claim_dead_letters_past_max_reads` — seed `read_ct` past the configured max (same technique
    as PR #469's `test_claim_dead_letters_poison_message`); assert claim returns nothing, the batch's
    scan rows are `'failed'`, and the message is archived (`pgmq.a_cyl_pipeline_dispatch`), not merely
    hidden.
  - `test_claim_dead_letter_of_last_batch_settles_the_run` — a run with exactly one batch; seed that
    batch's message past `max_reads` (same technique as above); claim it (dead-lettering it); assert
    `cyl_pipeline_runs.status` becomes `'failed'` — **not** left at `'queued'`/`'submitted'` — proving
    `claim`'s own dead-letter path runs the same run-completion aggregation `fail`/`complete` do.
  - `test_complete_records_workflow_name_on_every_scan_in_batch` — enqueue + claim a 3-scan batch;
    call complete with a workflow name; assert all 3 `cyl_pipeline_run_scans` rows have that
    `argo_workflow_name`; assert the message is deleted (a fresh claim finds nothing).
  - `test_complete_marks_run_submitted_when_last_batch_settles` — a run with 2 batches; complete both;
    assert `cyl_pipeline_runs.status = 'submitted'` only after the second.
  - `test_concurrent_complete_of_last_two_batches_settles_run_exactly_once` — a run with exactly 2
    remaining batches; using `tests/integration/conftest.py`'s `pg_conninfo` fixture, open two
    genuinely independent connections and have each call `complete_cyl_pipeline_batch` for one of the
    two batches at (as close to) the same instant as a `threading.Barrier` can arrange (matching PR
    #469's own `test_concurrent_enqueue_dedupes_to_one_job`'s two-thread technique); assert the run
    settles to `'submitted'` exactly once, with no lost update and no double-count — this is the actual
    proof for the race `design.md`'s "aggregate inside the SQL function" decision exists to prevent;
    the sequential test above does not exercise it.
  - `test_fail_marks_batch_scans_failed_and_dead_letters` — enqueue + claim a batch; call fail with an
    error string; assert every scan row in the batch is `status='failed'` with that `error_message`;
    assert the message is archived, not redeliverable.
  - `test_fail_increments_attempts_on_every_scan_in_batch` — enqueue + claim a batch; call fail; assert
    every scan row's `attempts` column increased by 1 from its prior value.
  - `test_complete_is_idempotent_on_redelivery` — mirroring PR #469's own
    `test_complete_is_idempotent_on_redelivery`: call `complete_cyl_pipeline_batch` twice with the same
    already-deleted `msg_id`; assert the second call does not raise and the batch's recorded state is
    unchanged.
  - `test_run_marked_partial_when_batches_mixed` — a run with 2 batches, one completed successfully,
    one failed; assert `cyl_pipeline_runs.status = 'partial'`.
  - `test_run_marked_failed_when_all_batches_fail` — a run with 1 batch, failed; assert
    `cyl_pipeline_runs.status = 'failed'`.
  - `test_wrappers_denied_to_public_and_session_roles` — extend PR #469-style
    `has_function_privilege` check to the three new functions, for `anon`/`authenticated`/`PUBLIC`/
    `bloom_user`/`bloom_writer`/`bloom_admin` (all denied) and `bloom_workflows` (allowed).
- [x] 1.2 Rollback fidelity, matching Phase 1's own section 1.6:
  - `test_migration_body_is_idempotent` — apply the new migration's SQL body a second time in an open
    transaction; confirm no error.
  - `test_rollback_removes_new_functions` — apply the migration, then the rollback, in one open
    transaction; assert `claim_cyl_pipeline_batch`/`complete_cyl_pipeline_batch`/
    `fail_cyl_pipeline_batch` no longer exist; assert `enqueue_cyl_pipeline_batch` and the queue itself
    (both from Phase 1) are untouched. (No `UPDATE`-grant assertions here — this migration adds no
    table grant; see `design.md`'s "no direct `UPDATE` grant" decision.)

**Result:** all new tests fail as expected (`UndefinedFunction`) before section 2, confirming red.

## 2. Migration (green)

- [x] 2.1 Re-confirm the latest migration timestamp on `origin/staging` immediately before writing the
      migration — do not reuse a timestamp scoped at proposal-drafting time without re-checking.
- [x] 2.2 Add `supabase/migrations/<timestamp>_add_cyl_pipeline_dispatch_functions.sql`:
      `claim_cyl_pipeline_batch`/`complete_cyl_pipeline_batch`/`fail_cyl_pipeline_batch` (modeled on PR
      #469's `claim_cyl_video_job`/`complete_cyl_video_job`/`fail_cyl_video_job` — same
      `pgmq.read`/`read_ct` poison-message guard shape, same `'processing'`-style settle-guard against a
      late/duplicate complete-or-fail call, adapted to this queue's message shape
      `{run_id, batch_index, scan_ids}` instead of `{job_id, scan_id, experiment_id}`); the run-status
      aggregation logic (per `design.md`'s decision) in **all three** functions — `complete`, `fail`,
      **and** `claim`'s own poison-message dead-letter branch, not just the first two;
      `fail_cyl_pipeline_batch` incrementing `attempts`; and the explicit
      `REVOKE EXECUTE ... FROM PUBLIC, anon, authenticated` / `GRANT ... TO bloom_workflows` for all
      three. **No table-level grant changes** — all three functions are `SECURITY DEFINER` and need
      no `UPDATE` grant on `bloom_workflows` (see `design.md`'s decision; do not add one).
- [x] 2.3 Add the companion
      `supabase/rollbacks/<timestamp>_add_cyl_pipeline_dispatch_functions_rollback.sql` (drops the
      three new functions only; leaves Phase 1's queue/table/`enqueue` function untouched).
- [x] 2.4 `make migrate-local` (or direct `psql` against the running local dev stack, matching Phase
      1's own fallback); re-run all of section 1's tests — confirm every one now passes.
- [x] 2.5 Run the migration linter against the new migration file before moving on.
      (`bash scripts/lint_migrations.sh origin/staging` — passed: "checked 1 new file(s)".)

**Implementation notes (section 1+2):** `make migrate-local` needed the `supabase` CLI, unavailable
in this environment — matching Phase 1's own precedent, applied the migration directly via `psql`
instead. Rather than mutate the shared dev DB the main checkout's session had running (Docker
containers are host-wide, not per-worktree), the user had that stack spun down and a fresh, isolated
one spun up from this worktree — using a **copy** of the main checkout's already-fully-migrated
`volumes/db/data` (bypassing the known separate "fresh local init lacks auth/storage schemas" gap),
so `db-dev` alone was sufficient for these DB-only tests. Docker's `-f /tmp/...` path needed
`MSYS_NO_PATHCONV=1` to avoid Git Bash mangling it. One real bug found and fixed while writing
`test_concurrent_complete_of_last_two_batches_settles_run_exactly_once`: its cleanup `finally` block
deleted the seeded DB rows but not the two pgmq messages themselves, leaking stale messages into
later test runs (pgmq is FIFO) — fixed by explicitly deleting/archiving both message ids in cleanup.

## 3. K8s client + worker tests first (red)

- [x] 3.1 Create `services/workflows/tests/test_k8s_client.py`, mocking `httpx` the same way
      `services/workflows/tests/test_auth.py` already mocks it for JWT validation (a monkeypatched
      `_FakeClient`/`_FakeResp` onto `k8s_client.httpx.Client` — follow that file's existing
      convention rather than introducing `unittest.mock.patch`). Name two distinct exception classes
      up front — `K8sConfigError` (missing/invalid credentials, raised before any network call) and
      `K8sSubmissionError` (a genuine submission attempt that failed) — the worker tests in 3.2 depend
      on being able to tell these apart. Write, and confirm currently **fail** (module doesn't exist
      yet):
  - `test_missing_token_raises_k8sconfigerror_before_any_network_call`,
    `test_missing_ca_cert_raises_k8sconfigerror_before_any_network_call`,
    `test_missing_api_url_raises_k8sconfigerror_before_any_network_call` — each asserts the raised
    exception is `K8sConfigError`, names the specific missing variable, and that no `httpx` call was
    attempted.
  - `test_namespace_defaults_to_busch_lab_when_unset`.
  - `test_ttl_defaults_to_3600_when_unset` — assert `build_workflow_body`'s `ttlStrategy` uses `3600`
    when `WORKFLOWS_K8S_TTL_SECONDS` is unset, and does NOT raise `K8sConfigError` (unlike the three
    credential vars, a missing TTL has a safe default, not a fatal misconfiguration).
  - `test_ca_cert_escaped_newlines_are_unescaped_then_converted_to_an_ssl_context` — `httpx`'s
    `verify=` parameter accepts a path or an `ssl.SSLContext`, not raw PEM text, and this repo's
    env-injection pipeline can only carry a PEM's newlines as literal `\n` escape sequences (a real
    multi-line value would break every line-oriented deploy tool); feed in a PEM with escaped `\n`s,
    assert they're restored to real newlines and the result is converted (e.g. via
    `ssl.create_default_context(cadata=...)`) into what gets passed as `verify=`.
  - `test_submit_workflow_posts_to_the_exact_resource_path` — asserts the POST URL is exactly
    `{api_url}/apis/argoproj.io/v1alpha1/namespaces/{namespace}/workflows`, with the bearer token in
    the `Authorization` header.
  - `test_submit_workflow_returns_the_generated_name_on_2xx` — a mocked 2xx response with
    `metadata.name` in the body; assert that name is returned.
  - `test_submit_workflow_raises_k8ssubmissionerror_on_4xx_5xx` and
    `test_submit_workflow_raises_k8ssubmissionerror_on_network_error` — a mocked non-2xx response, and
    a mocked `httpx` exception (timeout/connection error); assert both raise `K8sSubmissionError`
    specifically, distinct from `K8sConfigError`.
  - `test_submit_workflow_error_message_is_generic_not_raw` — mock a 500 response whose body contains
    the real API server URL / internal detail, and separately a raw `httpx.ConnectError` with a
    revealing message; assert `str(K8sSubmissionError)` in both cases is a fixed, generic message
    (mirroring PR #469's `worker.py`'s `_safe_detail` convention) — never the raw response body,
    exception text, or API URL. This is the value `fail_cyl_pipeline_batch` will store in
    `error_message`, a column Phase 3 exposes via an API.
  - `test_build_workflow_body_has_correct_apiversion_kind_and_generatename` — assert
    `apiVersion == "argoproj.io/v1alpha1"`, `kind == "Workflow"`, and that `metadata.generateName` (not
    `metadata.name`) is set.
  - `test_build_workflow_body_includes_required_labels` — given a `run_id`/`batch_index`/`scan_ids`,
    assert the constructed body's `metadata.labels` includes `submitted-by: bloom-pipeline`,
    `pipeline-run-id`, and `batch-index` with correct values.
  - `test_build_workflow_body_includes_ttl_strategy` — assert
    `spec.ttlStrategy.secondsAfterCompletion` equals the value of `WORKFLOWS_K8S_TTL_SECONDS`.
  - `test_build_workflow_body_parameterizes_scan_ids_for_this_batch_only` — a batch of `[12, 47, 9]`;
    assert the body's `scan-ids` argument is exactly those three ids.
  - `test_build_workflow_body_dag_references_all_four_templates_in_order` — assert the DAG's four
    tasks reference `sleap-roots-images-downloader-template` →
    `sleap-roots-predictor-template` → `sleap-roots-trait-extractor-template` →
    `sleap-roots-write-back-template`, with dependencies matching that order.
  - `test_submit_workflow_targets_same_namespace_for_batches_from_different_runs` — build/submit for
    two batches with different `run_id`s under the same configured namespace; assert both POST URLs
    contain the identical namespace segment.
- [x] 3.2 Create `services/workflows/tests/test_dispatch_worker.py`, matching PR #469's
      `test_worker.py` convention (mock the claim/submit/complete/fail seam — no real K8s, DB, or
      `httpx` needed). Write, and confirm currently **fail**:
  - `test_process_one_returns_false_on_empty_queue`.
  - `test_run_sleeps_the_poll_interval_after_an_empty_claim` — mock `claim_cyl_pipeline_batch` to
    return nothing; assert the loop sleeps for `WORKFLOWS_WORKER_POLL_SECONDS` before polling again
    (the empty-queue spec scenario has two clauses — "no batch" and "then sleeps" — this test is the
    one that exercises the second half, which `test_process_one_returns_false_on_empty_queue` alone
    does not).
  - `test_process_one_treats_a_dead_lettered_claim_like_an_empty_one` — mock `claim_cyl_pipeline_batch`
    to return nothing (simulating a poison-message claim that `claim` itself already dead-lettered);
    assert `submit_workflow` is never called — makes explicit, at the worker level, what is otherwise
    only implied by the empty-queue test (a dead-lettered claim and a genuinely empty queue look
    identical from `process_one`'s point of view, and neither reaches submission logic).
  - `test_process_one_submits_and_completes_on_success` — mock `claim_cyl_pipeline_batch` to return a
    batch, `submit_workflow` to succeed, assert `complete_cyl_pipeline_batch` is called with the
    returned workflow name and `fail_cyl_pipeline_batch` is not.
  - `test_process_one_fails_batch_on_k8ssubmissionerror` — mock `submit_workflow` to raise
    `K8sSubmissionError`; assert `fail_cyl_pipeline_batch` is called with a description of the error
    and `complete_cyl_pipeline_batch` is not.
  - `test_process_one_does_not_fail_batch_on_k8sconfigerror` — mock `submit_workflow` to raise
    `K8sConfigError` (missing/misconfigured credentials); assert **neither** `complete_cyl_pipeline_batch`
    **nor** `fail_cyl_pipeline_batch` is called — a config error must leave the claim unsettled
    (reclaimable after its visibility timeout once the config is fixed), not permanently fail real
    scans over a deploy/ops mistake.
  - `test_process_one_does_not_fail_after_completion_error` — mirroring PR #469's
    `test_process_one_does_not_fail_after_completion_error`: submission succeeded, but the
    `complete_cyl_pipeline_batch` call itself raises (e.g. a lost response) — assert `fail_...` is
    NOT called (a submitted Workflow must never be marked as a failed submission).
  - `test_run_loop_reconnects_on_unexpected_error` — mirroring PR #469's `run()` loop-level
    reconnect-and-continue behavior.
  - `test_signal_during_submission_lets_it_finish_before_exiting` — send the shutdown signal (set the
    module's running-flag to `False`, matching PR #469's `_stop` handler's own testing approach) while
    `process_one` is mid-submission; assert the in-flight `submit_workflow`/`complete_or_fail` call
    still completes normally before the loop exits.
  - `test_signal_while_idle_does_not_start_a_new_claim` — set the running-flag to `False` before the
    next poll; assert `claim_cyl_pipeline_batch` is not called again.

**Result:** all new tests fail at collection (`ModuleNotFoundError`) before section 4, confirming red.

## 4. K8s client + worker implementation (green)

- [x] 4.1 Add `services/workflows/k8s_client.py`: `K8sConfigError`/`K8sSubmissionError` exception
      classes (the latter constructed with a fixed, generic message — never raw response/exception
      text, per `design.md`'s sanitization decision); env-var reads
      (`WORKFLOWS_K8S_TOKEN`/`WORKFLOWS_K8S_CA_CERT`/`WORKFLOWS_K8S_API_URL`/`WORKFLOWS_K8S_NAMESPACE`
      default `runai-busch-lab`/`WORKFLOWS_K8S_TTL_SECONDS` default `3600`), an eager all-present
      validation function mirroring `supabase_client.py`'s pattern (raising `K8sConfigError` — only for
      the three credentials, not the two defaulted values), the PEM-unescape +
      `ssl.SSLContext` conversion for `WORKFLOWS_K8S_CA_CERT`, `build_workflow_body(run_id,
      batch_index, scan_ids)` (the DAG + labels + `ttlStrategy` construction), and
      `submit_workflow(body) -> str` (the `httpx` POST; on failure, `logger.warning`/`logger.error` the
      real status/body/exception, then raise `K8sSubmissionError` with the generic message only).
- [x] 4.2 Add `services/workflows/pipeline_queue.py` (or extend an existing queue-helpers module if one
      exists by this point): `claim_batch`/`complete_batch`/`fail_batch` wrappers around the three new
      RPC calls, matching `video_queue.py`'s thin-wrapper style.
- [x] 4.3 Add `services/workflows/dispatch_worker.py`: the polling loop (`process_one`, `run`,
      `SIGTERM`/`SIGINT` graceful shutdown, backlog logging) modeled directly on PR #469's `worker.py`
      — same shape, different middle step (`submit_workflow` instead of
      `generate_experiment_scan_video`), and `process_one` explicitly distinguishing `K8sConfigError`
      (leave unsettled) from `K8sSubmissionError` (call `fail_cyl_pipeline_batch`). The signal handler
      only flips the running-flag (matching PR #469's `_stop`) — it does not interrupt an in-flight
      `process_one` call, so a mid-submission batch's `complete`/`fail` call always finishes; the loop
      simply doesn't start a new claim afterward. `stop_grace_period`/visibility-timeout defaults
      should reflect that this worker's unit of work is a single HTTP POST, not a multi-second render —
      no need for #469's 150s grace period.
- [x] 4.4 Re-run sections 1 and 3's tests — confirm every one now passes. Run the full
      `services/workflows/tests/` suite — confirm no regressions to existing tests. (27/27 new,
      104/104 full suite, no regressions. Two more tests —
      `test_run_retries_startup_connection_until_supabase_is_reachable` and
      `test_signal_while_waiting_to_connect_exits_cleanly` — were added after live validation surfaced
      the startup-crash-loop gap; see 5.3's note. Final count: 29 new, 106/106 full suite.)

**Implementation note (section 3+4):** `test_k8s_client.py`'s initial `_configured` fixture used a
placeholder string (`"-----BEGIN CERTIFICATE-----\nfake\n..."`) for `CA_CERT` — this broke every
`submit_workflow`-calling test, since `submit_workflow` always builds a real `ssl.SSLContext` even
when `httpx.Client` itself is mocked, and `ssl.create_default_context` genuinely parses the PEM.
Fixed by generating a real, valid self-signed certificate (via `cryptography`) in the
`sample_pem_cert` fixture and using that (escaped) as the default `CA_CERT` instead.

## 5. Deployment wiring

- [x] 5.1 Add the `cyl-pipeline-worker` service to `docker-compose.dev.yml` and
      `docker-compose.prod.yml`: same image/build context as `workflows` and PR #469's
      `cyl-video-worker`, `command: ["python", "dispatch_worker.py"]`, same hardening (`read_only`,
      `tmpfs /tmp`, `no-new-privileges`, `cap_drop: [ALL]`), env block including the five
      `WORKFLOWS_K8S_*` vars (values left as deploy-time secrets, not filled in here) plus the existing
      `WORKFLOWS_SUPABASE_*` vars (the worker also needs `app_client()` to call the wrapper RPCs). This
      commit must land after section 4's files exist (see the commit-grouping note at the top) —
      otherwise the compose-health-check CI job fails on `python: can't open file 'dispatch_worker.py'`.
- [x] 5.2 Of the five `WORKFLOWS_K8S_*` vars, only **three** are real credentials
      (`_TOKEN`/`_CA_CERT`/`_API_URL`) — add exactly those three to
      `tests/unit/test_env_defaults.py`'s `SENSITIVE_INVENTORY` set, matching exactly how
      `WORKFLOWS_SUPABASE_EMAIL`/`_PASSWORD` were handled when the `workflows` service was built
      (confirmed precedent: neither appears in `.env.prod.defaults`/`.env.staging.defaults`, only in
      `SENSITIVE_INVENTORY`). The other **two** — `WORKFLOWS_K8S_NAMESPACE` and
      `WORKFLOWS_K8S_TTL_SECONDS` — are plain config values with safe defaults, not credentials, and
      do NOT go in `SENSITIVE_INVENTORY`: add real `WORKFLOWS_K8S_NAMESPACE=runai-busch-lab` and
      `WORKFLOWS_K8S_TTL_SECONDS=3600` entries to **both** `.env.prod.defaults` and
      `.env.staging.defaults` instead (required by `test_prod_staging_key_sets_are_identical`, which
      needs the same keys in both files). **Do not put `NAMESPACE` in `SENSITIVE_INVENTORY`** — a
      subtle trap: it's easy to bucket it with the three real credentials since it's also
      environment-specific, but a `SENSITIVE_INVENTORY`-only entry means `docker-compose --env-file`
      injects an *empty string* if no default-file value exists, which silently overrides
      `k8s_client.py`'s own `runai-busch-lab` code default with `""` — a real, previously-uncaught
      bug, not a hypothetical. Confirm `test_all_compose_vars_are_sourced` and
      `test_prod_staging_key_sets_are_identical` both pass with the new service's env block referenced
      in both compose files.
- [x] 5.3 Confirm the service builds and boots locally against `docker-compose.dev.yml` (it will log a
      clear `K8sConfigError` and idle-poll harmlessly without real `WORKFLOWS_K8S_*` credential values
      set locally — that's expected; the point is confirming it doesn't crash-loop on import errors).
      **First attempt found two real gaps, both fixed:**
      1. This worktree's local `.env.dev` (copied from the main checkout to get an isolated test DB)
         had no `WORKFLOWS_SUPABASE_EMAIL`/`_PASSWORD` at all — a pre-existing local-dev-setup gap
         Phase 1's own tasks.md already hit and recorded. Fixed locally (not a code change) by bringing
         up `auth`+`rest`, creating a `bloom_workflows` app user via the Auth Admin API
         (`app_metadata: {"is_workflows": true}`), confirming its JWT resolves to the `bloom_workflows`
         role claim, and adding its credentials to `.env.dev`.
      2. With that fixed, a second, real issue surfaced: `dispatch_worker.py`'s `run()` called
         `app_client()` once, unguarded, before entering the poll loop — identical to PR #469's own
         `worker.py`. A transient Supabase outage at container startup would crash the process, and
         Docker's `restart: unless-stopped` would crash-loop it forever, unlike the in-loop reconnect
         a few lines down, which already retries. Fixed via TDD (`_connect_with_retry()`: retries
         `app_client()` with backoff until it succeeds or a shutdown signal arrives, mirroring the
         in-loop pattern) — two new tests,
         `test_run_retries_startup_connection_until_supabase_is_reachable` and
         `test_signal_while_waiting_to_connect_exits_cleanly`, both confirmed red before the fix.
      3. **End-to-end validation against the real stack, with both fixes in place:** rebuilt and
         started `cyl-pipeline-worker` — it logged in, idle-polled cleanly every 5s. Seeded a real
         run/scan/batch and called `enqueue_cyl_pipeline_batch` directly via `psql`; the worker claimed
         it within one poll cycle, logged the exact expected `K8sConfigError` message (K8s credentials
         still intentionally unset), left the batch `status='queued'`/`argo_workflow_name=NULL`
         (correctly unsettled, not incorrectly failed), and kept polling — container never restarted.
         Separately, ran a one-off instance with a deliberately wrong `WORKFLOWS_SUPABASE_PASSWORD`:
         confirmed it logs `"could not connect on startup, retrying in 5.0s"` every 5 seconds
         indefinitely, `docker ps` shows it `Up` the whole time (no restart), proving the crash-loop
         fix works under real conditions, not just mocked ones. Test run/scan/queue rows cleaned up
         afterward.
- [x] 5.4 **Deploy-sequencing note, not a code task**: record in the PR description that
      `cyl-pipeline-worker` must not be started in staging/prod until real `WORKFLOWS_K8S_TOKEN`/
      `_CA_CERT`/`_API_URL` values are set there — Phase 1's trigger route is already live and enqueuing
      real batches in those environments, and (per this phase's terminal-failure design) a
      config-missing worker claiming a real batch would otherwise leave it unsettled rather than
      failed (per 3.2's `test_process_one_does_not_fail_batch_on_k8sconfigerror`) — reclaimable once
      fixed, but idle in the meantime, not data-destructive. **Also note the sharper failure mode**: a
      *present-but-wrong* value for one of the two defaulted vars (e.g. `NAMESPACE` misconfigured per
      5.2's trap above, or a `CA_CERT` whose newlines weren't escaped correctly) does NOT raise
      `K8sConfigError` at all — `k8s_client.py` only eagerly validates the three true credentials as
      "present or not," never validates that a defaulted value is *correct* — so it surfaces later as
      `K8sSubmissionError` on the first real claim, which (correctly, per this phase's design) marks
      those scans `'failed'` with a deliberately generic error message. That's safe for data integrity
      but gives on-call staff no clue the root cause was a deploy-config mistake rather than a
      transient cluster issue; check the worker's logs (which DO get the real, unsanitized detail),
      not just `cyl_pipeline_run_scans.error_message`, when diagnosing a wave of submission failures
      right after a deploy. Confirm this note actually makes it into the PR description at
      PR-creation time (see `/pr-description`).

## 6. Documentation

- [x] 6.1 Update `services/workflows/README.md` — every sentence below needs correcting, not just the
      first one:
  - The "On-demand vs queued generation" section's *"Phase 1 (this route) only enumerates/enqueues; a
    later phase adds the worker that actually claims a batch and submits it to Argo."* — Phase 1 wrote
    it forward-looking; this phase is that later phase.
  - The "Pipeline trigger" subsection's *"This phase does not submit anything to Argo/Kubernetes —
    that's a later phase's dispatch worker."* — same correction, different sentence; easy to miss since
    the wording differs from the one above.
  - The "Layer 2 — service identity" grant list's *"`SELECT`/`INSERT` (no `UPDATE` yet) on
    `cyl_pipeline_runs`/`cyl_pipeline_run_scans`"* — this becomes misleading once three new RPC
    functions exist, even though the underlying fact (`bloom_workflows` still has no direct `UPDATE`,
    per `design.md`) doesn't change; reword to state the RPCs write these columns as `SECURITY
    DEFINER`, not that nothing new writes them.
  - That same grant list's `"EXECUTE on enqueue_cyl_pipeline_batch"` bullet — append
    `claim_cyl_pipeline_batch`/`complete_cyl_pipeline_batch`/`fail_cyl_pipeline_batch`.
  - The closing attribution sentence naming which migration sets up which grants — add a clause for
    this phase's new migration.
  - Add the new `WORKFLOWS_K8S_*` env vars (five rows: `_TOKEN`/`_CA_CERT`/`_API_URL`/`_NAMESPACE`/
    `_TTL_SECONDS`) to the Configuration table, and a one-line note that the Configuration table and
    "Layer 2" grants now apply to two processes (the `workflows` API and `cyl-pipeline-worker`), not
    one.
  - Document that submission targets a single hardcoded namespace for v1 (known limitation, not a bug).
  - Add a note (in the Configuration table's row for `_CA_CERT`, or the "## Provisioning" section)
    that whoever provisions this secret must store the PEM with literal `\n` escape sequences in place
    of real newlines — this repo's line-oriented deploy pipeline (`scripts/verify_env_parity.py`,
    `deploy.yml`'s heredoc) cannot carry a genuinely multi-line secret value, and there is nothing
    elsewhere in this repo's docs that would tell a future operator this without it being stated here.
- [x] 6.2 Update `_WIKI/SUPABASE/README.md`: **correct**, not just append to, the existing sentence
      stating `bloom_workflows` holds no `UPDATE` because "a later phase adds its own small grant
      migration when a push-based status writer needs it" — this phase *is* that later phase, and the
      resolution is "no `UPDATE` grant needed at all" (SECURITY DEFINER functions write on its behalf),
      not "grant added now." Document the three new wrapper functions alongside the existing
      `enqueue_cyl_pipeline_batch` entry.

## 7. Validation

- [x] 7.1 `openspec validate add-cyl-pipeline-dispatch --strict` passes: "Change 'add-cyl-pipeline-dispatch' is valid".
- [x] 7.2 Full integration suite for the extended file (`tests/integration/test_cyl_pipeline_dispatch.py`)
      passes: 35/35 (18 pre-existing Phase 1 + 17 new Phase 2).
- [x] 7.3 Full `services/workflows/tests/` suite passes, no regressions: 106/106 (77 pre-existing +
      29 new — see 4.4's note on the 2 tests added after live validation).
- [x] 7.4 `ruff check` (pinned v0.9.9) + `black --check`/`black` (pinned 26.3.1, `--target-version
      py311` to match `language_version: python3.11` in `.pre-commit-config.yaml`) on every
      new/changed Python file — ruff clean immediately; black reformatted 4 of the 5 new files
      (import-wrapping, line-length) on the first run, clean after. `uvx pip-audit@2.10.0` on
      `services/workflows`: "No known vulnerabilities found" (no new dependencies added, as expected).
- [x] 7.5 `tests/unit/test_env_defaults.py::test_all_compose_vars_are_sourced` and
      `::test_prod_staging_key_sets_are_identical` pass with the new service's env vars in place (see
      5.2). Six unrelated tests in this file (`test_validator_rejects_*`, `test_validator_accepts_*`)
      fail in this local environment regardless of this change — `subprocess.run(["bash", ...])`
      resolves to a WSL `bash.exe` shim that can't parse the Windows path it's given, not a
      content/logic issue; matches this repo's own already-documented WSL/CRLF local-environment quirk
      (Phase 1's `add-cyl-pipeline-trigger` tasks.md §2.6 hit the same class of issue for the migration
      linter).
- [x] 7.6 Live boot/smoke test — see 5.3's implementation note for the full finding and fix: the local
      dev credential gap is resolved (a real `bloom_workflows` app user provisioned), and the
      startup-retry fix is verified end-to-end against the real stack (idle-poll, real claim → clean
      `K8sConfigError` → continued polling with the batch correctly left unsettled, and a real
      wrong-password run proving indefinite retry instead of crash-looping).
