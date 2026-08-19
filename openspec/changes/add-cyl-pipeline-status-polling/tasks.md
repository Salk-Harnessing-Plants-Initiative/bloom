> **Commit grouping**: six commits in one PR against `staging`, matching Phase 2's own precedent.
> 0. `refactor(workflows): rename DISPATCH_WORKER_POSTGREST_TIMEOUT_SECONDS to
>    SINGLE_ROW_RPC_TIMEOUT_SECONDS (#11)` — section 0. A pure, behavior-preserving rename of an
>    existing Phase 2 constant, landed separately so the poller commit (2, below) stays purely
>    additive rather than carrying an unrelated diff against already-shipped `dispatch_worker.py`.
>    Has no dependency on anything else in this proposal and could land first or be reviewed
>    independently.
> 1. `feat(db): add update_cyl_pipeline_run_status wrapper function (#11)` — section 1+2.
> 2. `feat(workflows): poll Argo Workflow status and progress run status (#11)` — section 3+4.
> 3. `feat(workflows): add GET /workflows/runs/{id} read route (#11)` — section 5.
> 4. `chore(deploy): add cyl-status-poller service to compose (#11)` — section 6. **Must land after
>    commit 2**, not before — a compose service referencing `status_poller.py` before that file exists
>    fails the compose-health-check CI job, matching the exact ordering constraint Phase 2 already hit
>    for `cyl-pipeline-worker`.
> 5. `docs(workflows): document the status poller and the new read route (#11)` — section 7.
> Section 5 (the GET route) has no dependency on 0-4 and could be written/reviewed first if useful, but
> lands in this order regardless.

## 0. Rename shared timeout constant (preliminary, unrelated to this phase's own behavior)

- [x] 0.1 In `services/workflows/supabase_client.py`, rename `DISPATCH_WORKER_POSTGREST_TIMEOUT_SECONDS`
      to `SINGLE_ROW_RPC_TIMEOUT_SECONDS` — this poller (section 4) becomes its second consumer, and a
      name that says "dispatch worker" no longer fits once a second, unrelated process uses it.
- [x] 0.2 Update `services/workflows/dispatch_worker.py`'s existing import and usages of the old name
      (grep for the exact current name/line numbers before editing — do not assume the count from this
      proposal's drafting time is still accurate).
- [x] 0.3 Update `services/workflows/tests/test_supabase_client.py`'s existing assertions against the
      old name to the new one.
- [x] 0.4 Run the full `services/workflows/tests/` suite — confirm no regressions from the rename alone
      (this commit changes no behavior, only a name).

## 1. Wrapper-function tests first (red)

- [x] 1.1 Create `tests/integration/test_cyl_pipeline_status_polling.py` (a new file — this is a
      distinct RPC/migration from Phase 2's pgmq functions, not an extension of the same queue;
      matching `test_cyl_pipeline_dispatch.py`'s `pg_conn` fixture and rollback-wrapped pattern). Write,
      and confirm currently **fail** (function doesn't exist yet):
  - `test_update_marks_submitted_run_running` — seed a run at `status='submitted'`; call
    `update_cyl_pipeline_run_status(run_id, 'running')`; assert `status = 'running'` and
    `completed_at IS NULL`.
  - `test_update_marks_running_run_complete_and_sets_completed_at` — seed a run at `'running'` with
    `completed_at IS NULL`; call with `'complete'`; assert `status = 'complete'` and `completed_at` is
    now set.
  - `test_update_does_not_overwrite_completed_at_if_already_set` — seed a run already `'complete'` with
    a real `completed_at` timestamp; call again with `'complete'` (should be a no-op per the next test,
    but verify specifically that IF the guard were ever loosened, `completed_at` still wouldn't move —
    i.e. this test pins the "don't clobber" behavior independent of the terminal-status guard).
  - `test_update_is_a_noop_on_a_run_already_terminal` — seed a run at `'failed'`; call with
    `'complete'`; assert the row is completely unchanged (`status` stays `'failed'`, `completed_at`
    unchanged).
  - `test_update_is_a_noop_on_a_run_still_queued` — seed a run at `'queued'` (never dispatched); call
    with `'running'`; assert the row is unchanged (a queued run was never submitted — this function has
    nothing to say about it).
  - `test_update_rejects_invalid_status_value` — call with `p_status = 'bogus'`; assert the call raises
    and no row anywhere is modified.
  - `test_update_marks_run_partial` — seed a run at `'submitted'`; call with `'partial'`; assert
    `status = 'partial'` and `completed_at` is set.
  - `test_update_marks_run_failed` — seed a run at `'submitted'`; call with `'failed'`; assert
    `status = 'failed'` and `completed_at` is set.
  - `test_update_on_nonexistent_run_id_is_a_harmless_noop` — call with a `p_run_id` that matches no
    row; assert the call does not raise (`RETURNS void`, `UPDATE` affects 0 rows).
  - `test_concurrent_update_calls_leave_the_run_in_a_valid_terminal_state` — mirroring
    `test_concurrent_complete_of_last_two_batches_settles_run_exactly_once`'s exact technique from
    `tests/integration/test_cyl_pipeline_dispatch.py` (two-thread `threading.Barrier`, two genuinely
    independent connections via the `pg_conninfo` fixture — not `pg_conn`, since each connection needs
    its own transaction): seed the run row and **commit it** (`pg_conn.commit()`, matching the cited
    precedent) so it's visible to both independent connections, then have each connection call
    `update_cyl_pipeline_run_status` for the same run at (as close to) the same instant, one with
    `'complete'` and one with `'partial'`; assert no error, and the run ends up in exactly one of the
    two valid terminal statuses (whichever wrote first — the guard `WHERE status IN
    ('submitted','running')` prevents the second call from clobbering the first), not a corrupted
    intermediate state. Because the seed row was committed outside the normal per-test rollback, clean
    it up explicitly in a `finally` block rather than relying on fixture teardown.
  - `test_wrapper_denied_to_public_and_session_roles` — `has_function_privilege` check for
    `anon`/`authenticated`/`PUBLIC`/`bloom_user`/`bloom_writer`/`bloom_admin` (all denied) and
    `bloom_workflows` (allowed), matching every prior wrapper's own such test.
- [x] 1.2 Rollback fidelity, matching every prior migration's own section:
  - `test_migration_body_is_idempotent` — apply the new migration's SQL body a second time in an open
    transaction; confirm no error.
  - `test_rollback_removes_new_function` — apply the migration, then the rollback, in one open
    transaction; assert `update_cyl_pipeline_run_status` no longer exists; assert every other function
    from Phase 1/2 is untouched.

**Result:** all new tests fail as expected (`UndefinedFunction`) before section 2, confirming red.

## 2. Migration (green)

- [x] 2.1 Re-confirm the latest migration timestamp on `origin/staging` immediately before writing the
      migration — do not reuse a timestamp scoped at proposal-drafting time without re-checking.
- [x] 2.2 Add `supabase/migrations/<timestamp>_add_cyl_pipeline_run_status_polling.sql`:
      `update_cyl_pipeline_run_status(p_run_id bigint, p_status text) RETURNS void`, `SECURITY
      DEFINER`, validating `p_status` against `'running'|'complete'|'failed'|'partial'` (raise on
      anything else), `UPDATE ... WHERE id = p_run_id AND status IN ('submitted','running')`, setting
      `completed_at = now()` only when transitioning into a terminal status and `completed_at IS NULL`.
      `REVOKE EXECUTE ... FROM PUBLIC, anon, authenticated` / `GRANT ... TO bloom_workflows`. No table
      or column changes — the `CHECK` constraint already allows every value this function writes.
- [x] 2.3 Add the companion
      `supabase/rollbacks/<timestamp>_add_cyl_pipeline_run_status_polling_rollback.sql` (drops the new
      function only).
- [x] 2.4 Apply the migration against the local dev stack; re-run all of section 1's tests — confirm
      every one now passes.
- [x] 2.5 Run the migration linter (`bash scripts/lint_migrations.sh origin/staging`) against the new
      migration file.

## 3. k8s_client + poller tests first (red)

- [x] 3.1 Extend `services/workflows/tests/test_k8s_client.py` (same `_FakeClient`/`_FakeResp`
      monkeypatch convention already established — extend `_FakeClient` with a `.get(...)` method).
      Write, and confirm currently **fail**:
  - `test_get_workflow_status_returns_the_phase_on_success` — a mocked 2xx response with
    `status.phase = "Succeeded"`; assert `get_workflow_status` returns `"Succeeded"`.
  - `test_get_workflow_status_returns_none_on_404` — a mocked 404 response; assert the return value is
    `None`, no exception raised.
  - `test_get_workflow_status_raises_k8sstatuserror_on_5xx` and
    `test_get_workflow_status_raises_k8sstatuserror_on_network_error` — mirroring
    `test_submit_workflow_raises_k8ssubmissionerror_on_4xx_5xx`/`_on_network_error`'s existing pattern,
    for the new error class.
  - `test_get_workflow_status_error_message_is_generic_not_raw` — mirroring
    `test_submit_workflow_error_message_is_generic_not_raw`: a mocked 500 body containing the real API
    URL; assert `str(K8sStatusError)` is fixed/generic, never the raw body.
  - `test_get_workflow_status_requires_config_before_any_network_call` — missing credentials; assert
    `K8sConfigError`, no `httpx` call attempted.
  - `test_get_workflow_status_requests_the_exact_resource_path` — assert the GET URL is exactly
    `{api_url}/apis/argoproj.io/v1alpha1/namespaces/{namespace}/workflows/{name}` with the bearer token
    in the `Authorization` header.
- [x] 3.2 Create `services/workflows/tests/test_status_poller.py`, matching `test_dispatch_worker.py`'s
      convention (mock the DB-read + `get_workflow_status` seam — no real K8s/DB/httpx). Write, and
      confirm currently **fail** (module doesn't exist yet):
  - `test_rollup_running_when_any_phase_running` — a run with phases `["Succeeded", "Running"]`;
    assert the computed status is `'running'`.
  - `test_rollup_running_when_only_pending` — a run with phases `["Pending"]` (no `Running` phase
    present at all); assert the computed status is `'running'` — a dedicated case, not folded into the
    `Running` test above, since `Pending` never otherwise appears in any test's input data.
  - `test_rollup_complete_when_all_succeeded` — phases `["Succeeded", "Succeeded"]`; assert
    `'complete'`.
  - `test_rollup_failed_when_none_succeeded` — phases `["Failed", "Error"]`; assert `'failed'`.
  - `test_rollup_partial_when_mixed_terminal` — phases `["Succeeded", "Failed"]`; assert `'partial'`.
  - `test_rollup_treats_dispatch_failed_scan_as_effective_failed_phase` — one scan
    `status='failed'`/`argo_workflow_name IS NULL`, one workflow `Succeeded`; assert `'partial'`.
  - `test_rollup_skips_a_404d_workflow_rather_than_guessing` — one workflow returns `None` (404), no
    other workflows for the run; assert the poller does not call `update_cyl_pipeline_run_status` for
    that run this cycle.
  - `test_rollup_returns_none_for_an_empty_phase_list_not_a_vacuous_complete` — the pure rollup
    function, called with an empty effective-phase list; assert it returns `None` (rule (0) in the
    spec's rollup requirement). This one assertion is sufficient to catch the real bug it targets: a
    naive implementation that omits the emptiness guard and falls straight to rule (2)'s `all(p ==
    'Succeeded' for p in phases)` would return `'complete'` here instead — Python's `all()` over an
    empty iterable is vacuously `True`, so this single output check is enough to distinguish "rule (0)
    implemented" from "rule (0) missing," not merely "the test happens to pass."
  - `test_run_with_no_workflow_names_and_no_dispatch_failures_is_left_unchanged` — a candidate run
    whose `cyl_pipeline_run_scans` rows have no non-null `argo_workflow_name` and no `'failed'`
    dispatch outcome either (an empty rollup input from the DB read itself, not from a 404) — distinct
    from the 404-skip case above, which has a workflow name that came back not-found; this case never
    even calls `get_workflow_status`. Assert `update_cyl_pipeline_run_status` is not called.
  - `test_sweep_selects_only_submitted_and_running_runs` — seed candidate runs at `'queued'`,
    `'submitted'`, `'running'`, `'complete'`; assert only the `'submitted'`/`'running'` ones are
    checked.
  - `test_sweep_calls_update_with_the_computed_status` — a full happy-path sweep of one run; assert
    `update_cyl_pipeline_run_status` is called with that run's id and the correctly-computed status.
  - `test_sweep_with_no_candidate_runs_does_not_error` — the candidate query returns nothing; assert
    `sweep_once` completes without error and calls neither `get_workflow_status` nor
    `update_cyl_pipeline_run_status`.
  - `test_sweep_isolates_a_k8sstatuserror_on_one_run_from_the_rest` — two candidate runs; the first
    run's `get_workflow_status` call raises `K8sStatusError`; assert the second run is still checked
    and updated this same cycle (a transient K8s API blip on one run must not abort the whole sweep).
  - `test_sweep_leaves_a_run_unsettled_on_k8sconfigerror_and_continues_to_the_next_run` — mirroring
    `dispatch_worker.py`'s `test_process_one_does_not_fail_batch_on_k8sconfigerror`, for a sweep with
    real candidate runs already `'submitted'`/`'running'` (the actual deploy-sequencing scenario
    §6.3's PR-description note describes): `get_workflow_status` raises `K8sConfigError` for every
    workflow; assert no run is left in a worse state than before (no incorrect write), and the next
    candidate run in the same cycle is still checked, not skipped because of the first run's error.
  - `test_sweep_logs_and_continues_when_update_call_fails` — `get_workflow_status` succeeds but the
    `update_cyl_pipeline_run_status` RPC call itself raises; assert the sweep continues to the next
    run rather than aborting the cycle.
  - `test_signal_during_sweep_lets_it_finish_before_exiting` — mirroring
    `test_signal_during_submission_lets_it_finish_before_exiting`'s technique from
    `test_dispatch_worker.py`.
  - `test_run_retries_startup_connection_until_supabase_is_reachable` — mirroring
    `dispatch_worker.py`'s own already-tested `_connect_with_retry` behavior for this new module.
  - `test_signal_while_waiting_to_connect_exits_cleanly` — mirroring `dispatch_worker.py`'s own such
    test; the poller's task list must not omit this despite claiming to fully mirror that convention.
  - `test_poll_interval_defaults_to_15_when_env_var_unset` — a module-level constant read once at
    import time can't be re-evaluated by `monkeypatch.delenv` after the fact (the same gotcha
    `dispatch_worker.py`'s own `POLL_INTERVAL` never had to face because no test ever exercises its
    default this way). Follow `k8s_client.py`'s own established pattern instead (see task 4.2's new
    `_resolve_poll_interval()` requirement): `monkeypatch.delenv("WORKFLOWS_STATUS_POLL_SECONDS",
    raising=False)`, then call `status_poller._resolve_poll_interval()` directly and assert it returns
    `15` — not an assertion against the already-resolved `POLL_INTERVAL` module attribute, which a
    plain `os.environ.get(...)` one-liner (matching `dispatch_worker.py`'s shape) would make this test
    unable to actually exercise.

**Result:** all new tests fail at collection (`ModuleNotFoundError`) before section 4, confirming red.

## 4. k8s_client + poller implementation (green)

- [x] 4.1 Extend `services/workflows/k8s_client.py`: `K8sStatusError` exception class (fixed, generic
      message, same convention as `K8sSubmissionError`), `get_workflow_status(name: str) -> str | None`
      (GET to `{url}/{name}`, `_validate_config()` first, `None` on 404, `K8sStatusError` on any other
      non-2xx/network failure).
- [x] 4.2 Add `services/workflows/status_poller.py`: the sweep loop (`sweep_once`, `run`,
      `SIGTERM`/`SIGINT` graceful shutdown, `_connect_with_retry` — modeled directly on
      `dispatch_worker.py`'s equivalents), the rollup function (pure, taking a list of effective
      phases and returning one of `'running'|'complete'|'failed'|'partial'`, or `None` if there's
      nothing to conclude yet), and the per-run DB read (distinct `argo_workflow_name`s plus any
      dispatch-failed scans) using `app_client()`. `POLL_INTERVAL` SHALL be resolved via a
      `_resolve_poll_interval()` helper (not a flat `float(os.environ.get(...))` one-liner like
      `dispatch_worker.py`'s own `POLL_INTERVAL`), mirroring `k8s_client.py`'s existing
      `_resolve_namespace()`/`_resolve_ttl_seconds()` pattern — this is what makes
      `test_poll_interval_defaults_to_15_when_env_var_unset` (§3.2) actually re-evaluable via
      `monkeypatch.delenv` + a direct call, rather than stuck asserting against an already-resolved,
      import-time-frozen module attribute. Import `SINGLE_ROW_RPC_TIMEOUT_SECONDS` from
      `supabase_client.py` (already renamed in section 0 below — this poller is simply its second
      consumer, no rename logic lives in this commit).
- [x] 4.2a Confirm `dispatch_worker.py`'s import and `test_supabase_client.py`'s assertions from
      section 0 still pass unmodified now that the poller also imports the renamed constant (a quick
      sanity check, not new work — section 0 already did the actual rename).
- [x] 4.3 Re-run sections 1 and 3's tests — confirm every one now passes. Run the full
      `services/workflows/tests/` suite — confirm no regressions.

## 5. GET /workflows/runs/{id} route

- [x] 5.1 Add a route-wiring test (new file `services/workflows/tests/test_main.py`, or an existing
      route-test module if one better fits by then — check before creating a second file for the same
      kind of test `test_pipeline.py` already contains) using `TestClient` + `dependency_overrides`,
      matching `test_pipeline.py`'s own route-wiring tests: `test_get_run_returns_run_and_scans`,
      `test_get_run_404s_for_unknown_run_id`, `test_get_run_requires_auth`,
      `test_get_run_rate_limited_returns_429` (mirroring `test_pipeline.py`'s own
      `test_pipeline_route_429_before_any_work` — this route enforces the same per-user rate limit).
      Confirm they fail first (route doesn't exist).
- [x] 5.2 Add `GET /workflows/runs/{run_id}` to `services/workflows/main.py`: `Depends
      (require_supabase_user)` + `enforce_rate_limit`, reads `cyl_pipeline_runs`/
      `cyl_pipeline_run_scans` via `app_client()`, 404 if the run doesn't exist. Confirm 5.1's tests now
      pass.

## 6. Deployment wiring

- [x] 6.1 Add the `cyl-status-poller` service to `docker-compose.dev.yml` and `docker-compose.prod.yml`:
      same image/build context as `workflows`/`cyl-pipeline-worker`, `command: ["python",
      "status_poller.py"]`, same hardening (`read_only`, `tmpfs /tmp`, `no-new-privileges`,
      `cap_drop: [ALL]`), env block including only the **four** `WORKFLOWS_K8S_*` vars this poller
      actually reads — `TOKEN`/`CA_CERT`/`API_URL`/`NAMESPACE` (used by `_validate_config`/
      `_ssl_context`/`get_workflow_status`) — **not** `TTL_SECONDS` or `ENV_LABEL`, which are
      submission-only (`build_workflow_body`, never called by this poller). All four are already
      sourced — no new entries needed in either `.env.*.defaults` or `SENSITIVE_INVENTORY`. Plus the existing
      `WORKFLOWS_SUPABASE_*` vars. **Do NOT add `WORKFLOWS_STATUS_POLL_SECONDS: ${WORKFLOWS_STATUS_POLL_SECONDS}`
      to either compose file's `environment:` block** — confirmed by grep,
      `WORKFLOWS_WORKER_POLL_SECONDS` (the precedent this mirrors) does not appear in either compose
      file at all, which is *why* it needs no default-file entry: its Python-side
      `os.environ.get(..., "5")` default is what actually governs it, invisible to
      `docker-compose --env-file` entirely. Referencing the new var via `${...}` without a real
      default-file entry would make compose inject an empty string and silently override the code
      default — the same trap Phase 2's `design.md` already found and fixed for `NAMESPACE`. Leave the
      var unreferenced in compose, exactly like its precedent. This commit must land after section 4's
      files exist, matching Phase 2's own compose-ordering constraint.
- [x] 6.2 Confirm the service builds and boots locally against `docker-compose.dev.yml` — expect a
      clean idle-poll with a `K8sConfigError`-per-candidate-run log line if no real `WORKFLOWS_K8S_*`
      values are set locally (matching Phase 2's own local-boot precedent); the point is confirming no
      crash-loop on import errors.
- [x] 6.3 **Deploy-sequencing note, not a code task**: record in the PR description that
      `cyl-status-poller` is safe to deploy with an **empty** candidate set (an idle/empty query result
      means it never reaches the credential check) — but that a deploy into an environment with
      **real, already-`'submitted'`/`'running'` candidate runs** (Phase 2's dispatch is already live in
      staging/prod) before real `WORKFLOWS_K8S_*` values are set there is only safe because of
      `test_sweep_leaves_a_run_unsettled_on_k8sconfigerror_and_continues_to_the_next_run` (§3.2) —
      each such run is left unchanged, not incorrectly written, and every other candidate in the same
      cycle is still checked. Do not write this note as unqualified fact until that test exists and
      passes. Also record the poll-interval-vs-TTL operational constraint from `design.md`'s "404
      handling" decision in the PR description, so whoever tunes either value later has the tradeoff in
      front of them.

**Implementation notes (section 6):** Bringing `cyl-status-poller` up via `docker compose up` from
this worktree's directory (rather than the main checkout's) caused Compose to recreate `db-dev`/`kong`
under the same shared project name (`bloom_v2_dev`), repointing their bind mounts at this worktree's
own `volumes/db/data` — a fresh, unmigrated Postgres, not the one with 4 days of accumulated state
other sessions may have been relying on. Confirmed with the user this was acceptable (a dev-only
stack). The container itself booted cleanly and stayed `Up` for 10+ minutes with a clean, expected
`_connect_with_retry` log (missing `WORKFLOWS_SUPABASE_EMAIL`/`_PASSWORD`, never provisioned on the
fresh schema) — no crash-loop, satisfying this task's actual bar. Re-verifying the RPC integration
suite afterward required replaying all `supabase/migrations/*.sql` by hand against the fresh instance
(the `supabase` CLI `make migrate-local` depends on is unavailable in this environment, matching Phase
2's own precedent) — 235/238 applied cleanly; 3 storage-bucket migrations failed on a missing
`storage.buckets.public` column (the `storage` service's own internal migrations never ran against
this instance) and were skipped as unrelated to this change. No live/disposable Argo cluster credential
was available in this environment, so §8.6's real end-to-end check (seed a run, let the poller observe
a real submitted Workflow) was not performed — stated explicitly, not claimed.

## 7. Documentation

- [x] 7.1 Update `services/workflows/README.md` — every sentence below needs correcting, not just the
      first one (matching Phase 2's own tasks.md §6.1 precedent of enumerating each specific sentence):
  - Document `status_poller.py` alongside the existing "Pipeline dispatch worker" section (a new
    "Pipeline status poller" section, same structure); document the rollup rule briefly (a pointer to
    the `cyl-pipeline-status-polling` spec's "Rollup rule..." requirement for the normative rule
    itself (four status-producing branches plus a rule-0 empty-list guard), and to `design.md` only
    for the separate question of why it's computed in Python — do
    not restate the rule's branches here; this file, `design.md`, and `spec.md` must not all carry
    their own independent copy of the same rule).
  - Document the new `GET /workflows/runs/{id}` route in the endpoints list at the top.
  - The existing grant-list sentence noting the access pattern is "shared by **two processes** now:
    the `workflows` API... and the separate `cyl-pipeline-worker` container" — becomes **three
    processes** once `cyl-status-poller` also authenticates as `bloom_workflows`.
  - The existing `"EXECUTE on enqueue_cyl_pipeline_batch, claim_cyl_pipeline_batch,
    complete_cyl_pipeline_batch, fail_cyl_pipeline_batch"` bullet — append
    `update_cyl_pipeline_run_status`.
  - The closing attribution sentence naming which migration sets up which grants — add a clause for
    this phase's new migration.
  - The Configuration table has **six** `WORKFLOWS_K8S_*` rows total (`TOKEN`/`CA_CERT`/`API_URL`/
    `NAMESPACE`/`TTL_SECONDS`/`ENV_LABEL`), all currently saying `"cyl-pipeline-worker only"` in their
    Notes column. Correct only the **four** this poller actually reads — `TOKEN`/`CA_CERT`/`API_URL`/
    `NAMESPACE` (matching §6.1's compose env block exactly) — to note `cyl-status-poller` also reads
    them (read-only `get`, unlike `cyl-pipeline-worker`'s `create`). Leave `TTL_SECONDS`'s and
    `ENV_LABEL`'s Notes unchanged — both are submission-only (`build_workflow_body`), never read by
    this poller. **Add** a `WORKFLOWS_STATUS_POLL_SECONDS` row (default `15`) to this table — found
    during implementation that `WORKFLOWS_WORKER_POLL_SECONDS` is documented here despite not being
    wired into either compose file's `environment:` block (an earlier draft of this task wrongly
    assumed it was absent from this table too — it is not; only its compose-wiring is absent). Match
    that precedent: document the var here for developer visibility even though §6.1 deliberately
    leaves it unreferenced in compose.
- [x] 7.2 Update `_WIKI/SUPABASE/README.md`: append `update_cyl_pipeline_run_status` to the list of
      `bloom_workflows`-only `SECURITY DEFINER` wrapper functions (alongside
      `enqueue_cyl_pipeline_batch`/`claim_cyl_pipeline_batch`/`complete_cyl_pipeline_batch`/
      `fail_cyl_pipeline_batch`) — matching Phase 2's own §6.2 precedent of documenting every new
      wrapper function there, not just in the service's own README.

## 8. Validation

- [x] 8.1 `openspec validate add-cyl-pipeline-status-polling --strict` passes.
- [x] 8.2 Full `tests/integration/test_cyl_pipeline_status_polling.py` suite passes.
- [x] 8.3 Full `services/workflows/tests/` suite passes, no regressions.
- [x] 8.4 `ruff check` (pinned to the version in `.pre-commit-config.yaml`) + `black --check`
      (pinned version + `--target-version py311`, same file) on every new/changed Python file — re-read
      the actual pinned versions at implementation time rather than reusing Phase 2's own
      already-possibly-stale numbers verbatim; `uvx pip-audit@2.10.0` on `services/workflows` (no new
      dependencies expected).
- [x] 8.5 `tests/unit/test_env_defaults.py::test_all_compose_vars_are_sourced` and
      `::test_prod_staging_key_sets_are_identical` pass with the new `cyl-status-poller` service's env
      block in place. Note the scope of what this actually proves: `test_all_compose_vars_are_sourced`
      parses only `docker-compose.prod.yml` (confirmed by reading the test file) — it confirms
      `WORKFLOWS_STATUS_POLL_SECONDS` was correctly left out of the **prod** compose file per §6.1, not
      silently added. Confirming the same for `docker-compose.dev.yml` relies on 6.2's live boot smoke
      test instead (a wrongly-added, unsourced reference there would surface as a crash on `int("")`
      or similar at container start, not as a unit-test failure).
- [x] 8.6 Live boot/smoke test of `cyl-status-poller` against the local dev stack (see 6.2), and — if a
      real or disposable Argo credential is available in this environment — one real end-to-end check:
      seed a run whose batch was actually submitted, let the poller observe it, confirm the DB status
      progresses as expected. If no live cluster credential is available in this environment, say so
      explicitly rather than claiming this was verified.
