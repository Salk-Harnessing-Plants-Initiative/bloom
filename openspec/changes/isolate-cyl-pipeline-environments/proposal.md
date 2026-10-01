# Isolate each environment's cylinder pipeline runs on the shared cluster

## Why

Prod and staging both submit cylinder pipeline Workflows into the one `runai-busch-lab` namespace, and
they share more than the namespace (bloom#863).

- **The credential.** The vendored Workflow (`services/workflows/vendored/sleap-roots-pipeline.yaml:91-92`)
  mounts `secretName: genericsecret-bloom-staging-pipeline-credentials` into stage-in and write-back.
  `build_workflow_body` (`services/workflows/k8s_client.py:194-245`) applies four overrides
  (`scan-ids`, attribution labels, `ttlStrategy`, `metadata.namespace`) and passes `spec.volumes`
  through unchanged. So a prod-dispatched run reads its images from, and writes its traits to,
  **staging** Supabase.
- **The stage directories.** The same file hardcodes three `hostPath`s,
  `/hpi/hpi_dev/users/eberrigan/pipeline_orchestration_tests/a4_poc/{input,predictions,traits}`
  (`:73-84`). Every run in every environment writes into them.
- **The scan namespace.** Each directory entry is keyed by `scan_key_for(scan_id) = f"scan_{scan_id}"`
  (`bloomcli/src/bloomctl/cyl/download_for_predict.py:56-58`), and each environment numbers its scans
  independently. Stage-in's resume check, `scan_is_already_staged` (`:221-235`), compares only the
  sidecar's presence, its `scan_key` and the type of its `image_ids`. No environment or database is
  recorded anywhere it could compare.

Giving prod its own credential alone would make this worse. Once prod authenticates to prod, a prod
run for prod's scan 42 is skipped at stage-in because staging's `scan_42` is already staged. Predict
then runs on staging's pixels, and prod ingests the traits under prod's scan, with blank provenance
(bloom#703/#864). That is indistinguishable after the fact. Upstream's own templates say the shared
stage directories are written by "every run (and every environment)"
(`sleap-roots-trait-extractor-template.yaml:63-65`).

Nothing stops a prod run today:

- `cyl-pipeline-worker` has no compose profile in `docker-compose.prod.yml`, and both deploys start it.
- PR #965's `CYL_PIPELINE_TRIGGER_ENABLED` switch reaches bloom-web only, and isn't on `staging` yet.
- `POST /workflows/pipeline` stays reachable for any signed-in member (bloom#983).
- `bloom_admin` can insert runs directly.

The one choke point every run passes through is the dispatch worker.

## What Changes

- **`build_workflow_body` overrides each environment's isolation boundary.** It adds two dispatch
  overrides, alongside the existing four:
  - the three stage `hostPath.path`s become `<root>/input`, `<root>/predictions` and `<root>/traits`,
    where `<root>` is the new `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT`;
  - `bloom-credentials`' `secretName` becomes the new `WORKFLOWS_K8S_PIPELINE_SECRET_NAME`.

  Neither has a code default, so no environment can fall back to staging's values. Volume names,
  `type: Directory` and every other field still pass through. A vendored file that gains another
  `hostPath` or `secret` volume, or loses one of the four, is a configuration error, so a new volume
  can't slip through un-isolated.
- **The dispatch worker refuses to submit unless the environment is switched on and configured.** It
  reads `CYL_PIPELINE_TRIGGER_ENABLED`, the switch bloom-web already uses in PR #965, with the same
  semantics: on only for exactly `true`.
  - When the switch is off, or the root or secret is missing or malformed, the worker fails the
    batch **at once** through `fail_cyl_pipeline_batch`, with a fixed, curated reason. Nothing
    reaches Argo.
  - This differs on purpose from today's `K8sConfigError` path, which leaves the claim unsettled and
    is dead-lettered as a "poison message" only after more than `WORKFLOWS_DISPATCH_MAX_READS`
    deliveries.
  - This is #983's option 1, so this change closes bloom#983.
- **Per-environment config.**

  | Environment | Root | Secret | Switch |
  |---|---|---|---|
  | Staging | unchanged `…/pipeline_orchestration_tests/a4_poc` | unchanged `genericsecret-bloom-staging-pipeline-credentials` | `true` |
  | Prod | new `/hpi/hpi_dev/users/eberrigan/bloom_cyl_pipeline/prod` | new `genericsecret-bloom-prod-pipeline-credentials` | `false` |
  | Dev | blank by default, so a dev stack with real cluster credentials refuses rather than writing into staging's tree | blank by default | as in compose |

  Prod's values name the intended resources from day one, because `scripts/validate_env.sh` rejects
  a blank value for any compose-referenced key. Prod stays off through the switch until an operator
  provisions those resources. Tests pin that prod's and staging's root and secret differ.
- **Docs.** The `services/workflows/README.md` overrides and Configuration sections, the
  `k8s_client.py` docstrings, and the operator walkthroughs in `tasks.md`.
- **Out of scope.** No change to `scan_key`, bloomctl, predict, the trait extractor, contracts, SQL or
  the upstream Workflow. `SLEAP_ROOTS_PIPELINE_REF` doesn't move, and the vendored file stays
  byte-identical to upstream at the pin.

## Impact

- **Affected specs:** `cyl-pipeline-dispatch`. Two MODIFIED requirements: the vendored-source override
  list, and the submitted-volumes scenario. Two ADDED requirements: per-environment isolation, and
  dispatch refusal.
- **Affected code:**
  - `services/workflows/k8s_client.py`, `services/workflows/dispatch_worker.py` and their tests;
  - `.env.prod.defaults`, `.env.staging.defaults`, `docker-compose.prod.yml` and
    `docker-compose.dev.yml` (`cyl-pipeline-worker` and, by the parity test, `rnaseq-worker`);
  - `tests/unit/test_env_defaults.py`;
  - `services/workflows/README.md`.
- **Operator actions, by the author.** These come after merge, and prod stays off until they're done:
  - a prod Supabase Auth account (`is_workflows: true`);
  - a RunAI Generic secret, `bloom-prod-pipeline-credentials`;
  - the three prod directories on the NFS;
  - a one-line follow-up PR flipping prod's switch;
  - a verification pair of runs over the same numeric scan id.
- **Interaction with PR #965 (`add-cyl-pipeline-ui`).**
  - Both add `CYL_PIPELINE_TRIGGER_ENABLED` to the two defaults files. Whichever merges second
    resolves a trivial conflict.
  - #965's `design.md:109` says the switch "covers bloom-web only" and that #983 tracks the service
    gate. Whichever merges second corrects that sentence.
  - Their spec deltas are in `cyl-pipeline-ui`, `cyl-pipeline-trigger` and `cyl-pipeline-runs`, not
    `cyl-pipeline-dispatch`, so there's no archive-ordering collision.
- **Behaviour change for existing staging runs:** none. Staging's root and secret equal today's
  vendored values, so staging's submitted body is byte-for-byte what it is now. Already-processed
  scans keep being skipped.
- Closes bloom#863 and bloom#983, once the deploy-verification tail in `tasks.md` §6 is done, not on
  merge (see bloom#780).
