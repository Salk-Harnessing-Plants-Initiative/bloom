## 1. Config resolution in `k8s_client.py`

- [ ] 1.1 **Test first** (`services/workflows/tests/test_k8s_client.py`). Add failing tests for three
  new module-level resolvers, imported the same way `ENV_LABEL` is today:
  - **`PIPELINE_HOSTPATH_ROOT`** from `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT`.
    - Accepted: `/hpi/hpi_dev/users/eberrigan/pipeline_orchestration_tests/a4_poc`.
    - Rejected, each resolving to "invalid", never raising at import: unset, `""`, `"   "`, a
      relative path, `/`, a trailing `/`, embedded whitespace, a `/./` segment, a `/../` segment.
  - **`PIPELINE_SECRET_NAME`** from `WORKFLOWS_K8S_PIPELINE_SECRET_NAME`.
    - Accepted: `genericsecret-bloom-staging-pipeline-credentials`.
    - Rejected: unset, blank, uppercase, underscore, a leading `-`, more than 253 characters.
  - **`PIPELINE_DISPATCH_ENABLED`** from `CYL_PIPELINE_TRIGGER_ENABLED`. It is `True` only for exactly
    `"true"`, and `False` for unset, `"false"`, `"TRUE"`, `"1"` and `" true"`. This is the same rule
    as #965's `isPipelineTriggerEnabled`.
- [ ] 1.2 Implement the three resolvers. Follow `_resolve_env_label`'s "never raises at import"
  shape: an invalid value resolves to `None`, so the refusal in §2 is what reports it.
- [ ] 1.3 Run 1.1's tests and confirm they pass.

## 2. `build_workflow_body`: refusal, closed volume set, overrides 5 and 6

- [ ] 2.1 **Test first.** Update the autouse `_configured` fixture (`test_k8s_client.py:94-104`) to set
  the switch on, plus staging's root and secret. Every existing test then keeps exercising today's
  body. Add failing tests:
  - **(a) Switch off.** With `PIPELINE_DISPATCH_ENABLED=False`, `build_workflow_body` raises
    `K8sDispatchRefusedError` with a "turned off" reason. Assert it raises before the vendored file
    is read, by pointing the vendored path at a missing file: refusal, not `K8sConfigError`.
  - **(b) Unconfigured.** Switch on, with the root `None`, the secret `None`, or both: raises
    `K8sDispatchRefusedError` with a "not configured" reason.
  - **(c) Off and unconfigured.** Both at once reports "turned off". The switch is checked first.
  - **(d) Staging values.** Staging's root and secret give `spec.volumes == vendored spec.volumes`
    exactly.
  - **(e) Prod values.** Prod's root and secret give paths
    `/hpi/hpi_dev/users/eberrigan/bloom_cyl_pipeline/prod/{input,predictions,traits}`, the prod
    secret name, and every `hostPath.type` still `Directory`.
  - **(f) Same scan id, two roots.** Scan id `[42]` under two different roots gives two bodies whose
    `images-input-dir` paths differ, and nothing else in the bodies differs except labels.
  - **(g) Closed volume set.** Each of these on a mutated vendored copy raises `K8sConfigError`:
    an extra `hostPath` volume, a second `secret` volume, a missing `traits-output-dir`, and
    `bloom-credentials` turned into a `hostPath`. Use the `_load_vendored_workflow` monkeypatch
    pattern the existing structural-drift tests use.
  - **(h) The whole-body test.** Rename
    `test_build_workflow_body_only_changes_the_four_documented_overrides` (`:757-783`) to "six" and
    extend its `expected` with overrides 5 and 6. Use prod's values, so the diff is non-empty and
    the test can't pass vacuously.
  - **(i) Pins on the vendored file.** Keep
    `test_build_workflow_body_pins_the_fields_no_other_dag_assertion_covers`' literal `a4_poc` and
    staging-secret pins (`:574-589`), but assert them on the **vendored file**, not on the built
    body. They still guard an upstream re-pin, and the body's values now come from config.
  - **(j) Volume pass-through.** Update `volumes_match_the_vendored_file_exactly` (`:727`) to assert
    names, order and types against the vendored file, plus exact equality under staging's values.
- [ ] 2.2 Implement:
  - `K8sDispatchRefusedError`, a sibling of `K8sConfigError`, not a subclass of
    `K8sSubmissionError`;
  - the refusal check at the top of `build_workflow_body`;
  - the closed-volume-set check;
  - overrides 5 and 6, keyed by a module constant
    `_STAGE_SUBDIRS = {"images-input-dir": "input", "predictions-output-dir": "predictions", "traits-output-dir": "traits"}`.

  Update the module docstring (`:23-31`) and `build_workflow_body`'s docstring to list six
  overrides and say why 5 and 6 exist (bloom#863).
- [ ] 2.3 Run `uv run --frozen --extra test pytest tests/test_k8s_client.py` in `services/workflows`
  and confirm it passes. Record the counts.

## 3. `dispatch_worker.process_one`: fail a refused batch at once

- [ ] 3.1 **Test first** (`services/workflows/tests/test_dispatch_worker.py`). Add failing tests:
  - **(a) Switch off.** A claimed batch makes `build_workflow_body` raise `K8sDispatchRefusedError`
    ("off"). Then `fail_batch` is called exactly once, with that batch's run id, index, msg id, scan
    ids and the curated "turned off" message. `submit_workflow` is not called, and `process_one`
    returns `True`.
  - **(b) Unconfigured.** The same, with the "not configured" message.
  - **(c) No leaked config.** Neither message contains `WORKFLOWS_K8S_`, `CYL_PIPELINE_`, `/hpi`
    or `genericsecret`.
  - **(d) The fail RPC errors.** If the `fail_batch` RPC itself raises, the worker logs and returns
    `True`, leaving the claim for redelivery. This mirrors the `K8sSubmissionError` branch
    (`dispatch_worker.py:103-124`).
  - **(e) Regression.** The existing
    `test_process_one_does_not_fail_batch_when_build_workflow_body_raises_k8sconfigerror`
    (`:130-164`) still passes unchanged: structural drift stays unsettled.
- [ ] 3.2 Implement an `except K8sDispatchRefusedError` branch in `process_one`, ahead of the
  `K8sConfigError` branch. Update the module docstring's Env section.
- [ ] 3.3 Run `uv run --frozen --extra test pytest tests/test_dispatch_worker.py tests/test_k8s_client.py`
  and confirm it passes.

## 4. Environment defaults and compose

- [ ] 4.1 **Test first** (`tests/unit/test_env_defaults.py`). Add failing tests:
  - **(a)** `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT` and `WORKFLOWS_K8S_PIPELINE_SECRET_NAME` join
    `test_env_disambiguating_values_differ`'s list, with a comment citing bloom#863.
  - **(b)** A new test: the two roots are not a path prefix of each other, compared on
    `PurePosixPath.parts`, not on string prefixes.
  - **(c)** A new test pinning the literal values per environment:
    - staging: the `a4_poc` root and `genericsecret-bloom-staging-pipeline-credentials`;
    - prod: `/hpi/hpi_dev/users/eberrigan/bloom_cyl_pipeline/prod` and
      `genericsecret-bloom-prod-pipeline-credentials`.

    Assert staging's root and secret equal the vendored file's own values, parsed from the YAML, so
    staging's body is provably unchanged.
  - **(d)** A new test: `CYL_PIPELINE_TRIGGER_ENABLED` is `true` in staging and `false` in prod, and
    `cyl-pipeline-worker`'s `environment` in `docker-compose.prod.yml` passes all three keys
    through as `${KEY}`.
    - If #965 has merged first, extend its `test_pipeline_trigger_is_on_in_staging_and_off_in_prod`
      instead of duplicating it.
  - **(e)** In `docker-compose.dev.yml`, `cyl-pipeline-worker` defaults the root and secret to blank
    (`${KEY:-}`) and the switch to `true`.
  - The existing `test_all_compose_vars_are_sourced`, `test_prod_staging_key_sets_are_identical` and
    `tests/unit/test_rnaseq_worker_container.py::test_the_worker_has_the_cyl_workers_environment`
    must also stay green.
- [ ] 4.2 Implement:
  - Add the three keys to `.env.prod.defaults` and `.env.staging.defaults` in the
    `cyl-pipeline-worker` block. Comment them with bloom#863, and say prod's switch stays `false`
    until §6.
  - Add the three keys to `cyl-pipeline-worker` and `rnaseq-worker` in both compose files. Their
    environments must stay identical, per `test_rnaseq_worker_container.py:43-52`. Note in a comment
    that `rnaseq-worker` ignores them.
  - Write files as LF bytes, since `test_no_crlf_line_endings` checks for this.
- [ ] 4.3 Run `py -m pytest tests/unit/test_env_defaults.py tests/unit/test_rnaseq_worker_container.py`
  and confirm everything passes except the 10 known `test_validator_*` cases. Those fail only on this
  Windows machine (bash exit 127), and CI runs them on Linux; confirm they pass in CI.
- [ ] 4.4 Run `scripts/validate_env.sh` logic in CI, through `validate-env-defaults` and
  `verify-env-parity` on the PR, and confirm both are green.

## 5. Documentation

- [ ] 5.1 Update `services/workflows/README.md`:
  - **"Pipeline dispatch worker" (`:258-315`):** six overrides; per-environment root and secret; the
    refusal behaviour and its two messages.
  - **"Provisioning (per environment)" (`:481-501`):** a step for the RunAI secret and the three
    directories, as hand-made preconditions that otherwise leave pods `Pending`.
  - **"Configuration" table (`:503-527`):** three rows.
- [ ] 5.2 Fix stale text: grep the repo for other "four overrides" / "exactly four" claims about
  `build_workflow_body`, such as comments, READMEs and `docs/`, and update each one.
  - Grep for the claim, not the file: `git grep -n -i "four overrides\|exactly four\|four documented"`.
  - Leave archived OpenSpec changes alone.
- [ ] 5.3 Draft for the author's approval (no push without go-ahead) the `talmolab/sleap-roots-pipeline`
  doc updates:
  - `docs/cluster-identities.md:155-161`: the hardcoded-secret paragraph now explains that Bloom
    overrides it at dispatch; prod's secret and directories become hand-made preconditions.
  - `docs/bloom-integration/roadmap.md`:
    - the `credential` row (`:299`), fixing its dead link to
      `salk-bloom/docs/credentials/bloom-workflows-a4-pipeline.md`, which exists only on unmerged
      bloom PR #549;
    - a status-log entry, and the `:539-542` "Production promotion is still NOT next" blocker text.
  - Ask the author whether the link should point at PR #549, or whether #549 should merge first.
- [ ] 5.4 Ask the author whether the vendored file's own comments, which describe the hardcoded secret
  and paths, should get an upstream clarification PR noting "Bloom overrides these at dispatch". If so,
  that needs a re-vendor and a pin bump, because the vendored copy must stay byte-identical.
  Default: no. The comments stay true for a direct `argo submit`.

## 6. Deploy verification and prod provisioning

Gated: each step needs the author's go-ahead. Do not archive until done (bloom#780).

- [ ] 6.1 The PR body says "Part of #863" and "Part of #983", not "Closes", so merging doesn't
  auto-close either issue before this section is done.
- [ ] 6.2 After the staging deploy, ask before running anything:
  - check `kubectl get pods -n runai-busch-lab` for GPU contention first;
  - run a staging pipeline run on one TEST-E2E scan in experiment 12880747 (bloom#985: real
    experiments lack bytes);
  - confirm the submitted Workflow's volumes equal today's `a4_poc` paths and staging secret
    (`kubectl get wf <name> -o jsonpath='{.spec.volumes}'`), and that the run completes.
- [ ] 6.3 Prod provisioning, done by the author, with step-by-step walkthroughs given at the time:
  - **(a) Prod Supabase Auth account** on bloom.salk.edu, flagged `is_workflows: true`, following
    PR #549's recipe (`docs/credentials/bloom-workflows-a4-pipeline.md` at `94329240`). Verify the
    prod DB has that recipe's grants first, read-only.
  - **(b) RunAI Generic secret** named `bloom-prod-pipeline-credentials`. RunAI prefixes it
    `genericsecret-`. It is Project-scoped to busch-lab and holds a `credentials.txt` dotenv with
    `BLOOM_API_URL`, `BLOOM_ANON_KEY`, `BLOOM_EMAIL` and `BLOOM_PASSWORD`.
  - **(c) The three directories**
    `/hpi/hpi_dev/users/eberrigan/bloom_cyl_pipeline/prod/{input,predictions,traits}`, created
    with the same ownership and permissions as the `a4_poc` ones.
  - **(d) Read-only confirmation** that the secret exists
    (`kubectl get secret genericsecret-bloom-prod-pipeline-credentials -n runai-busch-lab`, if RBAC
    allows) and that the directories exist.
- [ ] 6.4 After staging→main promotion, with prod's switch still `false`, and with the author's
  go-ahead: queue one prod run and confirm its batch fails at once with "Pipeline dispatch is turned
  off in this environment", and that no Workflow labelled `environment=prod` appears for it.
- [ ] 6.5 Open a one-line PR flipping `.env.prod.defaults` `CYL_PIPELINE_TRIGGER_ENABLED=true`, after
  6.3. Update the test pin in 4.1(d) in the same PR.
- [ ] 6.6 The acceptance run, with the author's go-ahead and a GPU check first. Pick a numeric scan id
  that both a staging run (6.2 or earlier) and a prod run process. Confirm:
  - the prod Workflow's paths are under `bloom_cyl_pipeline/prod` and it mounts the prod secret;
  - prod's stage-in staged, not skipped, that scan (bloomctl's per-scan `ok` vs `skipped`, from
    the images-downloader log);
  - the traits landed in prod's DB under prod's scan;
  - staging's rows for that id are unchanged (read-only checks on both DBs; filter Workflows by
    `environment=`).
- [ ] 6.7 With the author's approval, post a comment on #863 and #983 with 6.2–6.6's evidence and
  close both. Push the 5.3 doc updates upstream. Update memory.
- [ ] 6.8 Archive (`/openspec:archive isolate-cyl-pipeline-environments`). First grep the other
  unarchived changes for MODIFIED deltas on the same two `cyl-pipeline-dispatch` requirement
  headings, and raise any to the live text (archive-ordering hazard).
