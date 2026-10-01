# Design: isolate-cyl-pipeline-environments

## Context

Prod and staging share one Argo namespace (`runai-busch-lab`) and one deploy file
(`docker-compose.prod.yml`, differentiated by `.env.*.defaults`). They also share three things that
must be per-environment: the Supabase credential mounted into the pipeline, the three stage
directories, and, through those directories, the `scan_<id>` keyspace. `WORKFLOWS_K8S_ENV_LABEL`
already differs per environment, but it is only a label (`k8s_client.py:224-229`). The scoping
decisions below were made with the author on 2026-09-30.

## Goals / Non-Goals

**Goals**

- A prod-dispatched Workflow mounts prod's credential and writes only under prod's stage root. The
  same holds for staging.
- Within one environment the stage directories stay shared across runs, because skip-if-done dedup
  depends on that (srp#37, srp#71).
- Nothing reaches Argo from an environment that isn't switched on and fully configured, whichever way
  the run was created.

**Non-Goals**

- Changing `scan_key`, bloomctl, predict, the trait extractor, contracts, SQL, or the upstream
  Workflow.
- A runtime marker that detects a misconfigured root (decision D5 below).
- Argo concurrency limits (srp#98) and run-id provenance (bloom#864).

## Decisions

### D1. Bloom overrides the volumes at dispatch, not upstream

`build_workflow_body` rewrites the three `hostPath.path` values and `bloom-credentials.secret.secretName`
in the parsed body, the same pattern `metadata.namespace` already uses.

Alternatives considered:

- **Parameterise the paths upstream and re-vendor.** This needs an upstream PR. Upstream's
  `scripts/check_manifests.py` would also fail it: it rejects any hostPath whose `path` lacks the
  literal `/pipeline_orchestration_tests/a4_poc/`. And it's unverified whether Argo substitutes
  `{{workflow.parameters.*}}` inside `spec.volumes`.
- **Put the environment in `scan_key`.** `scan_key` is part of
  `compute_idempotency_key`'s payload (sleap-roots-contracts `identity.py`), and of predict's own
  identity key. Every existing key would change, giving a one-time GPU recompute, duplicate
  `cyl_trait_sources` rows, and new storage object paths. It would also need changes across
  bloomctl, predict and traits (`_SCAN_KEY_FORBIDDEN` bans `.` and `:`), and it still wouldn't
  isolate the credential.

Overriding at dispatch leaves the vendored file byte-identical to upstream at the pin, so
`check_vendored_workflow_drift.py` stays green, and so does upstream's own checker. The cost is that
the vendored file's literal paths and secret no longer describe what Bloom submits. Its header
already says the dispatcher layers overrides on top, and `services/workflows/README.md` and the spec
carry the authoritative list.

### D2. One root per environment; the dispatcher appends fixed sub-directories

`WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT` holds one absolute path. The dispatcher maps:

| Volume | Path |
|---|---|
| `images-input-dir` | `<root>/input` |
| `predictions-output-dir` | `<root>/predictions` |
| `traits-output-dir` | `<root>/traits` |

These names match today's `a4_poc` layout, so staging's root is
`/hpi/hpi_dev/users/eberrigan/pipeline_orchestration_tests/a4_poc` and its submitted paths are
unchanged.

One root, rather than three variables, means an environment can't end up with its three directories
split across trees. That split would break the predictions→traits hand-off silently.

The volume→sub-directory map lives in `k8s_client.py` as a constant. A vendored file whose
`hostPath` volume set isn't exactly those three names raises `K8sConfigError`, the existing
structural-drift class. That covers a renamed volume, an added volume, and one moved to a different
volume type. An upstream addition therefore can't pass through pointing at the shared `a4_poc` tree.
The same holds for `secret` volumes: exactly one, named `bloom-credentials`.

Prod's root is `/hpi/hpi_dev/users/eberrigan/bloom_cyl_pipeline/prod`. The author chose it: under
their user directory, but not under `pipeline_orchestration_tests`, which would be misleading. It
leaves room for a later `bloom_cyl_pipeline/staging`. It sits on the same `/hpi/hpi_dev` NFS mount
every GPU node already has (vendored file `:51-52`).

### D3. Validation

The root and secret are read once at import, like the other `WORKFLOWS_K8S_*` values.

- **Root.** It must be a non-empty absolute POSIX path. It must not be `/`, end in `/`, contain
  whitespace, or contain a `.` or `..` segment.
- **Secret name.** It must be a valid Kubernetes object name: an RFC 1123 subdomain, at most 253
  characters.

A value that fails either check is treated like a missing one (D4). A pod whose `hostPath` or
`Secret` doesn't exist sits `Pending`, not `Failed` (srp `docs/cluster-identities.md`). A bad value
that reached Argo would therefore hang the run, not fail it. Rejecting the obvious malformations
before submission is cheap; existence on the cluster is still the operator's precondition (§6).

### D4. Refusal fails the batch at once, with a curated message

A new exception, `K8sDispatchRefusedError`, is raised by `build_workflow_body` before it touches the
body. It is raised when:

- `CYL_PIPELINE_TRIGGER_ENABLED` is not exactly `true`;
- the root is missing or invalid; or
- the secret name is missing or invalid.

Raising it in `build_workflow_body`, not in the worker loop, means every caller of the body builder
gets the gate.

`dispatch_worker.process_one` catches it in its own branch and calls `fail_batch` with a fixed
message per cause:

- `"Pipeline dispatch is turned off in this environment"`
- `"Pipeline dispatch is not configured in this environment"`

The variable names, and the cause in detail, are logged server-side only. This follows the existing
curated-message rule ("A failure's error_message is a curated message"). The batch's scans become
`failed`, the message is archived, and the run settles through `fail_cyl_pipeline_batch`'s existing
rollup.

This deliberately departs from the `K8sConfigError` treatment, which leaves the claim unsettled.
Unsettled redelivers every `WORKFLOWS_DISPATCH_VT_SECONDS` (60) and is dead-lettered once
`read_ct > p_max_reads` (5) with "dead-lettered after N deliveries (poison message)". For a switch
that is off by design, that is about six minutes of churn and then a misleading reason.

It is not a subclass of `K8sSubmissionError`, whose handler records the generic
"Argo Workflow submission failed". A refusal is not a submission attempt, and the distinction should
survive in `error_message`.

Order of checks:

1. The switch.
2. The config.
3. The existing structural checks (`K8sConfigError`).

So an environment that is off reports "turned off" even if it is also unconfigured. Missing cluster
credentials (`WORKFLOWS_K8S_TOKEN` etc.) keep their current unsettled `K8sConfigError` behaviour,
raised later by `submit_workflow`. This change doesn't alter that requirement.

**This closes bloom#983.** Every run reaches Argo only through this worker. That covers runs made
through the web, through `POST /workflows/pipeline` directly, and by a `bloom_admin` insert. The
cost #983 named holds: a prod run is still *created* and then fails within one poll, rather than
being refused at the HTTP layer. The author accepted this.

### D5. The stage-in skip is isolated by construction, not by a new comparison

bloom#863's comment asks that `scan_is_already_staged` "compare something that distinguishes
environments". With per-environment roots, stage-in only ever reads its own environment's
`<root>/input/scan_<id>/`. Prod's `scan_42` and staging's `scan_42` are then different files, so no
comparison could match across environments.

The remaining exposure is misconfiguration: prod's root pointed at staging's tree. That's covered
by tests:

- `test_env_disambiguating_values_differ` gains both keys;
- a new test asserts the prod and staging roots are not nested in each other.

It is not covered at runtime. A hand-edit of `.env.prod` on the deploy host would bypass both. The
author chose to accept that, and to skip a bloomctl-side marker file. That marker would need a
bloomctl release, an upstream image re-pin and an `argo template update` affecting both
environments.

### D6. Reusing `CYL_PIPELINE_TRIGGER_ENABLED` for the worker

The worker reads the same switch bloom-web reads (PR #965), with the same semantics (exactly `true`).
"Go live" for an environment is then one value, flipped after provisioning.

The variable isn't on `staging` yet. This change adds it to both defaults files and to the worker
compose blocks, and #965 adds it to bloom-web's block. The defaults-file lines will conflict
trivially.

The compose parity test (`tests/unit/test_rnaseq_worker_container.py:43-52`) requires
`rnaseq-worker`'s environment to equal `cyl-pipeline-worker`'s. So `rnaseq-worker` also receives the
three variables. It doesn't read them, and the RNA-seq dispatcher builds its own body
(`rnaseq_workflows.py`).

Dev compose defaults:

- the switch defaults to `true`, matching #965's dev default;
- the root and secret default to blank, so a dev stack refuses with "not configured" unless a
  developer sets both deliberately.

Dev's database numbers scans independently too. Defaulting dev to staging's tree would recreate
this bug.

## Risks / Trade-offs

- **The vendored file no longer describes what is submitted.** Mitigated by the spec, the README,
  and a test that asserts the submitted paths and secret per environment.
- **Pending, not Failed, if an operator flips prod on before provisioning.** Validation (D3) can't
  prove existence. The §6 order (provision → verify → flip) is the mitigation. The flip is its own
  PR, so it can't ride in with code.
- **A host-side hand-edit of the env file bypasses the tests (D5).** Accepted.
- **#965 ordering.** Covered above. Neither PR's tests depend on the other's being merged.
- **`argo template update` is shared.** It still affects both environments' templates. This change
  doesn't touch templates.

## Migration Plan

1. Merge to `staging`. The staging deploy picks up root, secret and switch values equal to today's
   behaviour, so no staging-visible change.
2. A staging run on a TEST-E2E scan (experiment 12880747; real experiments lack image bytes,
   bloom#985) confirms the submitted body carries staging's root and secret and still completes.
3. The author provisions prod (§6).
4. On staging→main promotion, prod deploys with its switch `false`. A prod run fails at once with
   "turned off", which is verified.
5. A one-line PR flips prod's switch. A prod run over a scan id staging has also processed confirms
   both outcomes stay in their own trees and databases.

Rollback: revert the PR. Staging is unaffected either way. Prod returns to the pre-change state:
dispatch possible, staging secret.
