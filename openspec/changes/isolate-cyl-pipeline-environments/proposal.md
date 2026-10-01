# Isolate each environment's cylinder pipeline runs on the shared cluster

## Why

Prod and staging both submit cylinder pipeline Workflows into the one `runai-busch-lab` namespace, and
they share the Supabase credential and the stage directories those Workflows mount. So a prod run
reads and writes staging's database. Giving prod its own credential alone would make that worse: the
two environments' scan ids would then collide in the shared directories (bloom#863).

The detail:

- **The credential.** The vendored Workflow (`services/workflows/vendored/sleap-roots-pipeline.yaml:91-92`)
  mounts `secretName: genericsecret-bloom-staging-pipeline-credentials` into stage-in and write-back.
  `build_workflow_body` (`services/workflows/k8s_client.py:194-245`) applies four overrides
  (`scan-ids`, attribution labels, `ttlStrategy`, `metadata.namespace`) and passes `spec.volumes`
  through unchanged.
- **The stage directories.** The same file hardcodes three `hostPath`s,
  `/hpi/hpi_dev/users/eberrigan/pipeline_orchestration_tests/a4_poc/{input,predictions,traits}`
  (`:73-84`). Every run in every environment writes into them.
- **The scan namespace.** Entries are keyed by `scan_key_for(scan_id) = f"scan_{scan_id}"`
  (`bloomcli/src/bloomctl/cyl/download_for_predict.py:56-58`), and each environment numbers its scans
  independently.
  - Stage-in's resume check, `scan_is_already_staged` (`:208-235`), skips a scan whose sidecar exists,
    parses, carries the same `scan_key`, and has string `image_ids`.
  - The sidecar records `image_ids` and `images_checksum`, which are environment-specific, but nothing
    compares them against the current environment.
- **Why a prod credential alone makes it worse.** Once prod authenticates to prod, a prod run for
  prod's scan 42 is skipped because staging's `scan_42` is already staged. Predict then runs on
  staging's pixels, and prod ingests the result under prod's scan, with blank provenance
  (bloom#703/#864). The two cases can't be told apart afterwards.
- **Upstream already says so.** Its templates call the stage directories "fixed, shared hostPaths that
  every run (and every environment) writes into" (`sleap-roots-trait-extractor-template.yaml:63-65`
  at sleap-roots-pipeline `origin/main` `1192f47`).

**Nothing stops a prod batch from reaching Argo today.** Prod has run `cyl-pipeline-worker`, which has
no compose profile, with prod cluster credentials since before this change. PR #965 added
`CYL_PIPELINE_TRIGGER_ENABLED` (`false` in prod), but only bloom-web reads it. The Workflows service's
`POST /workflows/pipeline` stays open to any signed-in member (bloom#983).

Every queued batch reaches Argo through the dispatch worker. `enqueue_cyl_pipeline_batch` is
`bloom_workflows`-only, and no trigger enqueues on insert
(`supabase/migrations/20260730120000_create_cyl_pipeline_runs.sql:204-231`).

## What Changes

- **`build_workflow_body` overrides each environment's isolation boundary.** It adds two dispatch
  overrides, alongside the existing four:
  - the three stage `hostPath.path`s become `<root>/input`, `<root>/predictions` and `<root>/traits`,
    where `<root>` is the new `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT`;
  - `bloom-credentials`' `secretName` becomes the new `WORKFLOWS_K8S_PIPELINE_SECRET_NAME`.

  Neither has a code default.
- **The vendored volume set is closed.** It must be exactly four volumes, of any type: the three stage
  `hostPath`s and the one `secret`. Anything else is a configuration error, so a volume added
  upstream can't reach the cluster un-isolated.
- **The dispatch worker refuses to submit for an environment that is off or unconfigured.**
  - It reads the switch #965 introduced, `CYL_PIPELINE_TRIGGER_ENABLED`, which is on only for
    exactly `true`. The worker reads it at start-up; bloom-web reads it per request.
  - When the switch is off, or the root or secret is missing or malformed, the worker fails the
    batch **at once** through `fail_cyl_pipeline_batch`. It records one of two fixed messages ("turned
    off" or "not configured"), logs the detail server-side, and sends nothing to Argo.
  - Today's `K8sConfigError` path behaves differently: it leaves the claim unsettled, and the batch is
    dead-lettered as a "poison message" after about five minutes.
  - This is #983's option 1, changed to fail the batch at once rather than leave it queued. It
    replaces #983.
- **Per-environment config.** The values below live in `.env.{staging,prod}.defaults` and are pinned
  by tests:

  | Environment | Root | Secret | Switch |
  |---|---|---|---|
  | Staging | unchanged `…/pipeline_orchestration_tests/a4_poc` | unchanged `genericsecret-bloom-staging-pipeline-credentials` | `true` (unchanged) |
  | Prod | new `/hpi/hpi_dev/users/eberrigan/bloom_cyl_pipeline/prod` | new `genericsecret-bloom-prod-pipeline-credentials` | `false` (unchanged) |
  | Dev | blank | blank | `true` |

  - Prod's values name the intended resources from day one, because `scripts/validate_env.sh` rejects
    a blank value for any compose-referenced key.
  - Dev's blank root and secret make a dev stack holding real cluster credentials refuse with "not
    configured", instead of writing into staging's tree.
- **BREAKING (operators).** `cyl-pipeline-worker` now needs the two new keys. Any environment
  without valid values fails every batch it claims.
- **Docs.**
  - The `services/workflows/README.md` overrides, provisioning and Configuration sections.
  - The `k8s_client.py` and `dispatch_worker.py` docstrings, and the compose comments.
  - The texts #965 shipped saying the switch "covers bloom-web only" or that prod is off "until
    bloom#863 is fixed".
  - Drafted upstream doc updates, for the author's approval.
- **Out of scope.**
  - No change to `scan_key`, bloomctl, predict, the trait extractor, contracts, SQL or the upstream
    Workflow. `SLEAP_ROOTS_PIPELINE_REF` doesn't move, and the vendored file stays byte-identical to
    upstream at the pin.
  - Submissions made outside Bloom (`argo submit` with a cluster identity) aren't covered by this
    gate.

## Impact

- **Affected specs.**
  - `cyl-pipeline-dispatch`: three MODIFIED requirements. These are the Argo-submission requirement
    (its opening sentence and volumes scenario), the vendored-source override list, and "Submission
    outcome is recorded", which gains refusal as a third outcome. Four ADDED requirements: isolation,
    validation, the closed volume set, and refusal.
  - `cyl-pipeline-ui`, from unarchived `add-cyl-pipeline-ui`: one sentence corrected in place, with no
    new delta.
- **Affected code, all in `services/` and the repo root.**
  - `services/workflows/k8s_client.py` and `services/workflows/dispatch_worker.py`, with their tests.
  - `.env.prod.defaults`, `.env.staging.defaults`, `docker-compose.prod.yml` and
    `docker-compose.dev.yml` (`cyl-pipeline-worker`, plus `rnaseq-worker` by the parity test).
  - `tests/unit/test_env_defaults.py`.
  - `services/workflows/README.md`.
  - `web/lib/cyl-pipeline/trigger-enabled.ts`, docstring only.
- **Behaviour change.**
  - Staging's submitted body is unchanged as a parsed structure, because its root and secret equal
    the vendored values (pinned by a test).
  - Dev runs, and runs in any environment with bad config, now fail at once instead of idling
    unsettled and then being dead-lettered.
- **Exposure window.** Prod keeps today's behaviour, able to dispatch with the staging secret, until
  this change is promoted to `main`. Whether to stop prod's `cyl-pipeline-worker` before then is an
  operator decision, recorded in `tasks.md` §6.
- **Operator actions, by the author, gated in `tasks.md` §6.** Prod stays off until they're done:
  - a least-privilege prod Supabase Auth account (`is_workflows: true`);
  - a RunAI Generic secret, `bloom-prod-pipeline-credentials`;
  - the three prod directories;
  - a migration and contract parity check on prod;
  - two promotions, with a one-line flip PR between them;
  - a verification pair of runs over the same numeric scan id.
- **Issues.** The PR is "Part of" bloom#863 and bloom#983, not "Closes". Both are closed by hand once
  §6's evidence exists (bloom#780).
