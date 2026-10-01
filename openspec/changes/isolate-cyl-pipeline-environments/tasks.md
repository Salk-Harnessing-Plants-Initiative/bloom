Each group is one commit, and CI is green after each. Commit messages and the PR body must not use
"close(s/d)", "fix(es/ed)" or "resolve(s/d)" directly before an issue number, in any form, including
negated sentences.

- `auto-close-issues-on-staging.yml` scans the PR title and body.
- Squash bodies are built from commit messages (`COMMIT_MESSAGES`) and reach `main` through a merge
  commit at promotion.

Use "Part of #863" / "Part of #983". Test files reference `k8s_client.K8sDispatchRefusedError` as a
module attribute inside test bodies, never via a top-level `from k8s_client import` of a name that
doesn't exist yet. That way a red test fails on its own and doesn't break collection of the whole
file.

## 1. Resolvers and the refusal error (`k8s_client.py`)

- [x] 1.1 **Test first** (`services/workflows/tests/test_k8s_client.py`). Call the private resolvers
  after `monkeypatch.setenv`/`delenv`, as `test_env_label_defaults_to_dev_when_unset` (`:369-373`)
  does. Never `importlib.reload(k8s_client)`, because that rebinds exception classes other modules
  import by name.
  - **`_resolve_pipeline_hostpath_root()`** returns a `(value, reason)` pair, or an equivalent.
    - Accepted: the staging root and the prod root, both on Windows and on Linux.
    - Rejected, with a reason, never raising: unset, `""`, `"   "`, `"hpi/x"`, `"/"`, `"/hpi/x/"`,
      `"/hpi//x"`, `"/hpi/x y"`, `"/hpi/x\n"`, `"/hpi/x\t"`, `"/hpi/./x"`, `"/hpi/../x"`, `"/hpi/.."`,
      `"/hpi/."`.
  - **`_resolve_pipeline_secret_name()`**.
    - Accepted: the staging and prod names, `"a.b"`, and a 253-character valid name.
    - Rejected: unset, blank, `"Bloom"`, `"bloom_x"`, `"-a"`, `"a-"`, `".a"`, `"a."`, `"a..b"`,
      `"name\n"`, and a 254-character name.
  - **`_resolve_pipeline_dispatch_enabled()`**: `True` only for `"true"`. `False` for unset,
    `"false"`, `"TRUE"`, `"1"`, `" true"`, `"true "` and `"true\n"`.
  - **No validator uses `Path` or `os.path`.** Assert the staging root is accepted with
    `monkeypatch.setattr(os, "sep", "\\")`, or simply rely on the Windows run. A test docstring
    records that `Path('/hpi/x').is_absolute()` is `False` on Windows.
  - **Import never raises.** `subprocess.run([sys.executable, "-c", "import k8s_client"], cwd=<service dir>, env={…invalid root and secret…, "PATH": …})`
    returns 0.
  - **The exception's place in the hierarchy.** `K8sDispatchRefusedError("off").reason == "off"`.
    `issubclass(K8sDispatchRefusedError, K8sConfigError)` and
    `issubclass(K8sDispatchRefusedError, K8sSubmissionError)` are both `False`.
- [x] 1.2 Implement the three resolvers, using string checks on `/`-split segments (no `Path`,
  `PurePosixPath` or `os.path`) and `re.fullmatch`.
  Bind them to module globals `PIPELINE_HOSTPATH_ROOT`, `PIPELINE_SECRET_NAME` and
  `PIPELINE_DISPATCH_ENABLED`, plus the invalid-reason globals, following `_resolve_ttl_seconds`'
  never-raise shape. Define `K8sDispatchRefusedError(reason)`. Nothing raises it yet.
- [x] 1.3 Run `cd services/workflows && uv run --frozen --extra test pytest tests/ -v` and confirm it
  passes. Record the red evidence from 1.1 (counts) in the commit body.

## 2. The worker settles a refusal at once (`dispatch_worker.py`)

- [x] 2.1 **Test first** (`services/workflows/tests/test_dispatch_worker.py`). Mock
  `build_workflow_body` as the existing tests do.
  - **(a) Off.** Raising `k8s_client.K8sDispatchRefusedError("off")` leads to:
    - `fail_batch` called exactly once, with that batch's run id, index, msg id, scan ids and
      `error == "Pipeline dispatch is turned off in this environment"`;
    - `submit_workflow` not called;
    - `process_one` returning `True`.
  - **(b) Unconfigured.** The same with `"unconfigured"`, and
    `error == "Pipeline dispatch is not configured in this environment"`.
  - **(c) No leaked detail.** Raise the error with a detail-laden message, e.g.
    `"WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT invalid: /hpi/../x genericsecret-x"`. Then:
    - the recorded `error` contains none of `WORKFLOWS_K8S_`, `CYL_PIPELINE_`, `/hpi`, `a4_poc`,
      `bloom_cyl_pipeline`, `genericsecret`;
    - a `caplog` record at WARNING or above contains the run id, the batch id and the detail.
  - **(d) The fail RPC errors.** `fail_batch` raising leads to a `caplog` ERROR record, `True`
    returned, and no further settle. This mirrors the existing fail-RPC-errors test.
  - **(e) Regression.** `test_process_one_does_not_fail_batch_when_build_workflow_body_raises_k8sconfigerror`
    (`:130-164`) passes unchanged.
  - **(f) No Kubernetes call, end to end.** Use the real `build_workflow_body`, with
    `k8s_client.PIPELINE_DISPATCH_ENABLED=False` set by monkeypatch, and `k8s_client.httpx.Client`
    patched to record calls. Assert `fail_batch` gets the "off" message and no client is created.
    This test passes only after §4, so it is written in §4 (4.1(m)), not here.
- [x] 2.2 Implement an `except K8sDispatchRefusedError` branch in `process_one`, ahead of
  `K8sConfigError`. It maps `reason` to the two constants, logs the detail, and calls `fail_batch`,
  tolerating a failing RPC. Update the module docstring's Env section and `process_one`'s docstring
  (`:63-65`).
- [x] 2.3 Run the whole `services/workflows` suite and confirm it passes.

## 3. Per-environment config

This group is one atomic commit; every partial state is red. Write the files as LF bytes.

- [x] 3.1 **Test first.**
  - **(a) `tests/unit/test_env_defaults.py`.** `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT` and
    `WORKFLOWS_K8S_PIPELINE_SECRET_NAME` join `test_env_disambiguating_values_differ`'s list, with a
    comment citing bloom#863.
  - **(b)** A new test: neither root is equal to or an ancestor of the other, compared with
    `PurePosixPath(...).parts`.
  - **(c)** A new test pinning the literals:
    - staging: the `a4_poc` root and `genericsecret-bloom-staging-pipeline-credentials`;
    - prod: `/hpi/hpi_dev/users/eberrigan/bloom_cyl_pipeline/prod` and
      `genericsecret-bloom-prod-pipeline-credentials`.

    It also asserts staging's root and secret equal the vendored file's own values, parsed with
    `yaml.safe_load`.
  - **(d)** Extend #965's `test_pipeline_trigger_is_on_in_staging_and_off_in_prod` (`:533`): parse the
    compose file with `yaml.safe_load`, as `test_rnaseq_worker_container.py:22-25` does, and assert
    `cyl-pipeline-worker` receives all three keys as `${KEY}`. Update its docstring: the switch now
    gates the worker too, and prod stays off until it is provisioned (§6).
  - **(e)** `docker-compose.dev.yml`'s `cyl-pipeline-worker` defaults the root and secret to blank
    (`${KEY:-}`) and the switch to `true`.
  - **(f)** Add to `services/workflows/tests/test_k8s_client.py`: for each of `.env.staging.defaults`
    and `.env.prod.defaults`, read from the repo root, the committed root and secret pass the §1
    resolvers. A typo in prod's value then fails CI, not the §6.5 flip.
- [x] 3.2 Implement:
  - **Defaults files.** Add **two** keys to each of `.env.staging.defaults` and `.env.prod.defaults`,
    directly after `WORKFLOWS_K8S_ENV_LABEL=` in the `cyl-pipeline-worker` block, not at the end of
    the file. Rewrite #965's existing `CYL_PIPELINE_TRIGGER_ENABLED` comment in place so it names
    both readers. It keeps one definition per file, which `test_no_duplicate_keys_in_defaults`
    checks. Prod's comment says it stays `false` until §6.3 is done.
  - **Compose.** Add the three keys to `cyl-pipeline-worker` **and** `rnaseq-worker` in both compose
    files, after `WORKFLOWS_K8S_ENV_LABEL`. The parity test (`test_rnaseq_worker_container.py:43-52`)
    requires both. A comment on `rnaseq-worker` says it ignores them.
  - **Compose comments.** In `docker-compose.prod.yml:291-294` and `:322-327`, and
    `docker-compose.dev.yml:149-152` and `:175-180` (line numbers as of `618cbeb8`): "only TOKEN/CA_CERT/API_URL/NAMESPACE are needed;
    TTL_SECONDS, ENV_LABEL and the PIPELINE_* pair are submission-only". The two new keys have no
    code default. Update bloom-web's switch comment (`docker-compose.prod.yml:122-123`).
  - **`.env.dev.example:68-76`.** Add a comment saying both new keys must be set, to a dev-only tree
    and never to staging's, before a dev stack can dispatch.
- [x] 3.3 Run `uv run --extra test pytest tests/unit/` (the whole directory, as CI does) and the
  `services/workflows` suite, and confirm both pass.
  - The 10 `test_validator_*` cases fail only on this Windows machine, because bash gets a backslash
    path.
  - Run `test_validator_accepts_real_defaults_plus_fake_secrets[prod|staging]` locally anyway, from
    WSL or with a forward-slash path. It is the test enforcing "prod's values can't be blank".

## 4. `build_workflow_body`: the gate, the closed volume set, overrides 5 and 6

This group is one commit: the fixture update and the gate must land together.

- [x] 4.1 **Test first** (`test_k8s_client.py`).
  - **Fixture.** The autouse `_configured` fixture (`:94-104`) also sets
    `PIPELINE_DISPATCH_ENABLED=True` plus staging's root and secret. Every existing test then keeps
    building today's body.
  - **(a) Off.** With the switch off and `_VENDORED_WORKFLOW_PATH` pointed at
    `tmp_path / "missing.yaml"`: `K8sDispatchRefusedError` with `reason == "off"`, not
    `K8sConfigError`.
  - **(b) Unconfigured.** The same with the switch on and the root, the secret, or both `None`:
    `reason == "unconfigured"`. Also a case with an unconfigured root plus a label-collision vendored
    copy, which still raises the refusal.
  - **(c) Off and unconfigured.** Both at once gives `reason == "off"`.
  - **(d) Staging values.** Staging's values give `body["spec"]["volumes"] == vendored["spec"]["volumes"]`.
  - **(e) Prod values.** Prod's values give paths
    `/hpi/hpi_dev/users/eberrigan/bloom_cyl_pipeline/prod/{input,predictions,traits}` and the prod
    secret. `type` stays `Directory`, and no path or secret equals the vendored value.
  - **(f) Two environments.** Scan id `[42]`, with the same run, batch and `ENV_LABEL`, built under
    staging's and prod's values. With those four fields set to `None` in both bodies, the bodies are
    equal. Each path is under its own root.
  - **(g) Two runs, one environment.** Two run ids under one config give identical `spec.volumes`.
  - **(h) Closed set.** Each of these raises `K8sConfigError`. Write a mutated `copy.deepcopy(vendored_workflow)`
    to `tmp_path` and monkeypatch `k8s_client._VENDORED_WORKFLOW_PATH`, as `:878-957` do:
    - a fifth `hostPath`, `secret`, `nfs`, `projected` or `emptyDir` volume;
    - each of the four volumes missing;
    - a renamed stage volume;
    - a duplicated name;
    - `bloom-credentials` as a `hostPath`;
    - a stage `hostPath` without `path`;
    - `spec.volumes` absent, a string, or containing a non-dict.
  - **(i) Reordered volumes.** A reordered vendored copy gets overrides by name, and the order is
    preserved.
  - **(j) The whole-body test.** Rename `test_build_workflow_body_only_changes_the_four_documented_overrides`
    (`:757-783`) to "six", build with prod's values so the diff is non-empty, extend `expected` with
    overrides 5 and 6, and update the comment naming it (`:745-751`).
  - **(k) The pins test.** In `test_build_workflow_body_pins_the_fields_no_other_dag_assertion_covers`,
    assert the literal `a4_poc` and staging-secret pins (`:573-589`) on the **vendored file** rather
    than on the built body, and update the comment at `:570-573`.
  - **(l) The volumes test.** `volumes_match_the_vendored_file_exactly` (`:727`) asserts names, order
    and types against the vendored file, plus exact equality under staging's values.
  - **(m) End to end (2.1(f)).** In `test_dispatch_worker.py`, with the real `build_workflow_body`,
    `k8s_client.PIPELINE_DISPATCH_ENABLED=False` and `k8s_client.httpx.Client` patched to record:
    `fail_batch` gets the "off" message and no client is created.
- [x] 4.2 Implement:
  - the gate at the top of `build_workflow_body`;
  - the closed-set check;
  - `_STAGE_SUBDIRS`;
  - overrides 5 and 6, applied in place by name.

  Update the module docstring (`:13-31`: list six overrides; say the PIPELINE_* pair has no default
  and causes a refusal), `build_workflow_body`'s docstring, and `K8sConfigError`'s docstring
  (`:96-97`).
- [x] 4.3 Run the whole `services/workflows` suite and `tests/unit/`, and confirm both pass.

## 5. Documentation and verification

- [x] 5.1 Update `services/workflows/README.md`:
  - **`:258-315`:** six overrides, and the per-environment mapping rule
    (`<root>/{input,predictions,traits}`). Point to the defaults files rather than restating the
    values. Add refusal and its two messages, and step 3 at `:289-290` (refusal is an outcome).
  - **`:241`:** `rnaseq-worker` receives these keys, ignores them, and is not gated.
  - **Provisioning (`:481-501`):** the RunAI secret and the three directories as hand-made
    preconditions that otherwise leave pods `Pending`. The cluster account is distinct from the
    service's own `is_workflows` user in steps 1–2.
  - **Configuration (`:503-527`):** three rows, noting both workers receive them.
- [x] 5.2 Grep for the claim:
  `git grep -n -i -E "four overrides|exactly four|four documented overrides|volumes.*unmodified|pass(es)? through unmodified|until bloom#863|bloom-web only|covers bloom-web" -- ':!openspec/changes/archive'`.
  Update each hit about `build_workflow_body` or the switch. Hits about the four dispatch **labels**
  (live spec `:284`/`:286`, `test_k8s_client.py:946`) are about labels and stay. Known sites from
  #965:
  - `web/lib/cyl-pipeline/trigger-enabled.ts:7-12`;
  - `openspec/changes/add-cyl-pipeline-ui/design.md:109`;
  - `openspec/changes/add-cyl-pipeline-ui/specs/cyl-pipeline-ui/spec.md:37`. Correct it in place to
    "`false` in prod until prod's pipeline credential and stage directories are provisioned
    (bloom#863); the dispatch worker reads the same switch". That change is unarchived, so editing
    its delta adds no archive collision.
- [x] 5.3 Draft, for the author's approval, the `talmolab/sleap-roots-pipeline` doc updates. Don't
  push without a go-ahead. **Drafted** against upstream `origin/main` `367c771` (2026-10-01); kept
  outside the repo until approved:
  - **`docs/cluster-identities.md`:** `:155-161` (Bloom overrides the secret at dispatch), `:179-180`
    ("distinguished only by an environment label"), `:254-257` (Bloom's path is different again)
    and `:259-275` (three directories and one Secret per environment; per-environment directories
    are the fix, per-run ones still aren't).
  - **The `scripts/check_manifests.py:230-235` comment.** Bloom no longer takes `spec.volumes`
    verbatim, so a scratch path redirects only a manual `argo submit`.
  - **`docs/bloom-integration/roadmap.md`:** the `credential` row (`:299`), whose link to
    `salk-bloom/docs/credentials/bloom-workflows-a4-pipeline.md` exists only on unmerged bloom PR
    #549 (ask the author: link the PR, or merge #549 first); the `:539-542` blocker text; a
    status-log entry.
  - Not drafted: `scripts/runai_run_pipeline.sh:20-22` and `README.md:404`, which review cited.
    At `367c771` that script path doesn't exist and that README line is Argo DAG background.
- [x] 5.4 Ask the author whether to file an upstream follow-up issue. It would trim the vendored
  Workflow's CROSS-REPO VENDORING NOTICE (`:6-22`), whose override list is already stale (it omits
  `metadata.namespace`), to point at Bloom's README, and fix `:87`'s `talmo-lab` vs `busch-lab`. Do
  it with the next re-pin, not as one forced now. **Author: file it.** Filed 2026-10-01 as
  talmolab/sleap-roots-pipeline#104.
- [x] 5.5 Verification:
  - `uvx ruff@0.9.9 check services/workflows && uvx ruff@0.9.9 format --check services/workflows && uvx black@26.3.1 --check services/workflows`
    (versions from `.pre-commit-config.yaml`; CI doesn't lint `services/workflows`);
  - `pre-commit run --files <changed files>`;
  - `openspec validate isolate-cyl-pipeline-environments --strict`;
  - on the PR, confirm `validate-env-defaults`, `python-audit` and `vendored-workflow-drift-check`
    are green. The drift check is triggered by `k8s_client.py` and fetches upstream over the
    network.
  - **Done 2026-10-01:** `ruff check services/workflows` clean, and `ruff format --check` clean on
    the four `services/workflows` files changed. `test_env_defaults.py` keeps ruff-format drift
    that predates this change. `pre-commit` isn't installed here, so it wasn't run. `--strict`
    passes. PR #988 CI: 34 passed, 1 skipped, including `validate-env-defaults`, `python-audit`
    and `vendored-workflow-drift-check`.
- [x] 5.6 Run `/pre-merge`, then `/pr-description`. Open the PR against `staging`. The body says
  "Part of #863" and "Part of #983", and states the exposure window and the two-promotion plan. Opened as #988.

## 6. Deploy verification and prod provisioning

Gated: each step needs the author's go-ahead. Do not archive until done (bloom#780).

- [x] 6.0 Ask the author whether to stop prod's `cyl-pipeline-worker` until promotion 1. Prod can
  dispatch with the staging secret until then. **Author, 2026-10-01: no, leave it running.**
- [ ] 6.1 After the staging deploy:
  - check `kubectl get pods -n runai-busch-lab` for GPU contention;
  - run a staging pipeline run on one TEST-E2E scan in experiment 12880747;
  - confirm the Workflow's `.spec.volumes` equals today's `a4_poc` paths and the staging secret
    (filter by `environment=staging`), and that the run completes.
- [ ] 6.2 With the author's go-ahead, set the dev stack's switch on, with no root or secret, and
  queue a run. Confirm it fails with "not configured" and that no Workflow is created.
- [ ] 6.3 Prod provisioning, done by the author, with step-by-step walkthroughs given at the time:
  - **(a) Prod Supabase Auth account** on bloom.salk.edu, flagged `is_workflows: true`, following
    PR #549's recipe (`git show 94329240:docs/credentials/bloom-workflows-a4-pipeline.md`).
    - Only the recipe's grants.
    - Separate from the Workflows service's own `is_workflows` user.
    - Record that the credential is readable by any holder of the namespace's shared identities.
  - **(b) RunAI Generic secret** named `bloom-prod-pipeline-credentials`, which RunAI prefixes
    `genericsecret-`. Project-scoped to busch-lab, holding `credentials.txt` with `BLOOM_API_URL`,
    `BLOOM_ANON_KEY`, `BLOOM_EMAIL` and `BLOOM_PASSWORD`. Verify it in the RunAI console Credentials
    list: neither `bloom-pipeline` nor `argo-user` can read Secrets (srp `cluster-identities.md:39`,
    `:146-148`).
  - **(c) The three directories** `/hpi/hpi_dev/users/eberrigan/bloom_cyl_pipeline/prod/{input,predictions,traits}`,
    with the `a4_poc` dirs' ownership and mode. Check `readlink -f` on each: none may resolve under
    `pipeline_orchestration_tests`.
  - **(d) A read-only parity check** that prod's DB is at staging's migration head for the cyl
    write-back, recipe and contract migrations, and accepts the contract version the shared
    templates emit (compare bloom#685).
  - **(e) Optional, with a go-ahead.** A one-off Workflow, filtered and labelled `environment=prod`,
    no GPU, mounting the prod secret and directories, running `test -s` on the credential and
    `test -d` on each directory.
- [ ] 6.4 **Promotion 1** (staging→main), with prod's switch `false`. With the author's go-ahead,
  start one prod run with a direct `POST /workflows/pipeline` under a member JWT; prod's web proxy
  answers 503. Confirm it fails at once with "Pipeline dispatch is turned off in this environment"
  and that no `environment=prod` Workflow appears. Record the leftover failed run.
- [ ] 6.5 After 6.3 **and** 6.4: a one-line PR (Part of #863) flipping `.env.prod.defaults`'s
  `CYL_PIPELINE_TRIGGER_ENABLED=true`, with the 3.1(d) pin updated in the same commit. On `staging`
  it is inert.
- [ ] 6.6 **Promotion 2** lands the flip. Then the acceptance pair of runs, with a GPU check and a
  go-ahead first: a numeric scan id that a staging run has processed and a prod run now processes.
  Confirm:
  - prod's Workflow paths are under `bloom_cyl_pipeline/prod`, and it mounts the prod secret;
  - prod's stage-in logged that scan as staged, not skipped;
  - the traits landed in prod's DB under prod's scan;
  - staging's rows for that id are unchanged (read-only checks; filter by `environment=`).
- [ ] 6.7 With the author's approval, post #863's and #983's evidence and close both by hand. Push
  5.3's upstream docs. Update memory.
- [ ] 6.8 Archive (`/openspec:archive isolate-cyl-pipeline-environments`). First grep unarchived
  changes for MODIFIED deltas on the three `cyl-pipeline-dispatch` headings this change modifies,
  and raise any to the live text.
