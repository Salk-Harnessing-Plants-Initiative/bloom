## MODIFIED Requirements

### Requirement: A claimed batch is submitted as an Argo `Workflow` CRD via the raw Kubernetes REST API

For each claimed batch that is not refused (see the dispatch-refusal requirement), the worker SHALL construct a `Workflow` object (`apiVersion:
argoproj.io/v1alpha1`, `kind: Workflow`) whose `spec` references the five already-registered
`WorkflowTemplate`s in sequence (`sleap-roots-images-downloader-template` →
`sleap-roots-predictor-template` → `sleap-roots-trait-extractor-template` →
`sleap-roots-write-back-template` → `sleap-roots-exit-gate-template`, each dependent on the
previous) via `templateRef`, parameterized by that batch's own `scan-ids` (not the whole run's),
and SHALL POST that object via `httpx` directly to
`{WORKFLOWS_K8S_API_URL}/apis/argoproj.io/v1alpha1/namespaces/{WORKFLOWS_K8S_NAMESPACE}/workflows` with
`Authorization: Bearer {WORKFLOWS_K8S_TOKEN}` and TLS verification against `WORKFLOWS_K8S_CA_CERT`. The
worker SHALL NOT invoke the `argo` CLI or call the Argo Server (`:8888`). A non-2xx response or a
network-level failure (timeout, connection error, TLS failure) SHALL be treated as a submission failure
for that batch, not retried within the same claim.

The constructed `spec` SHALL include every field the canonical vendored `sleap-roots-pipeline.yaml`
defines — including `spec.volumes`, `spec.entrypoint`, and `spec.serviceAccountName` — not only the DAG
task/`templateRef` structure. The worker SHALL NOT hand-build the `Workflow` body field-by-field in
Python; it SHALL derive it from the vendored canonical source at
`services/workflows/vendored/sleap-roots-pipeline.yaml`, whose pin is recorded in the sibling
`SLEAP_ROOTS_PIPELINE_REF`, applying only the documented dispatch overrides on top of it.

#### Scenario: A successful submission returns the generated Workflow name

- **WHEN** the K8s API server accepts a batch's submission
- **THEN** the response's `metadata.name` (K8s-generated from the submitted `generateName`) is
  captured as that batch's `argo_workflow_name`

#### Scenario: The submitted Workflow's parameters match exactly the claimed batch's scan ids

- **WHEN** a batch of `[12, 47, 9]` scan ids is claimed
- **THEN** the submitted Workflow's `scan-ids` argument contains exactly those three ids, not the
  run's full scan list and not another batch's ids

#### Scenario: The submitted DAG references all five templates in dependency order

- **WHEN** any batch is submitted
- **THEN** the DAG reached from `spec.entrypoint` contains exactly five tasks, named
  `images-downloader`, `predictor`, `trait-extractor`, `write-back` and `exit-gate`
- **AND** each task's `templateRef` names both the matching `WorkflowTemplate`
  (`sleap-roots-<task>-template`) and the matching inner `template` within it, so a task cannot
  invoke the wrong template out of the right `WorkflowTemplate`
- **AND** each task after the first declares exactly the preceding task as its sole dependency, so
  the DAG is linear

#### Scenario: A non-2xx response is a submission failure, not a retry

- **WHEN** the K8s API server responds with a 4xx or 5xx status to a submission POST
- **THEN** the worker treats that batch's submission as failed and does not immediately re-POST it

#### Scenario: A network-level failure is a submission failure

- **WHEN** the POST to the K8s API server times out or the connection fails before any HTTP response
- **THEN** the worker treats that batch's submission as failed, the same as a non-2xx response

#### Scenario: The constructed Workflow has the correct API version, kind, and name field

- **WHEN** any batch is submitted
- **THEN** the constructed object has `apiVersion: argoproj.io/v1alpha1` and `kind: Workflow`
- **AND** it sets `metadata.generateName` (a name prefix for the API server to make unique), not
  `metadata.name` (a caller-chosen exact name, which would collide across repeated submissions)

#### Scenario: The submitted Workflow includes every volume the canonical source defines

- **WHEN** any batch is submitted
- **THEN** the constructed `spec.volumes` carries the same volume names, in the same order and with the
  same volume types, as the vendored canonical file's `spec.volumes` — `images-input-dir`,
  `predictions-output-dir`, `traits-output-dir`, and `bloom-credentials`
- **AND** submission does not fail with a `volume '<name>' not found in workflow spec` error from the
  Argo controller

### Requirement: Submission outcome is recorded before the message is settled

On a successful submission, the worker SHALL call `complete_cyl_pipeline_batch`, which records the
returned `argo_workflow_name` on every scan row in that batch and deletes the queue message. On a
failed submission, the worker SHALL call `fail_cyl_pipeline_batch`, which marks every scan row in that
batch `status = 'failed'` with an error message and dead-letters the queue message. Neither call SHALL
retry the submission itself — a failure is terminal for the claimed message (retry/requeue is
explicitly out of scope for this phase, matching the same deferral bloom PR #469 made for its own
queue).

A batch refused before any submission attempt (see the dispatch-refusal requirement) SHALL also be
settled through `fail_cyl_pipeline_batch`, with that requirement's curated message, on the same
claim. The unsettled treatment below applies only to a missing or invalid K8s API credential and to
a structurally drifted vendored Workflow source, not to a refusal.

#### Scenario: A successful submission records the workflow name on every scan in the batch

- **WHEN** a batch of 3 scans is submitted successfully
- **THEN** all 3 corresponding `cyl_pipeline_run_scans` rows have `argo_workflow_name` set to the
  submitted Workflow's generated name

#### Scenario: A failed submission marks the batch's scans failed, not silently dropped

- **WHEN** a batch's submission fails (non-2xx or network error)
- **THEN** every scan row in that batch has `status = 'failed'` and a non-null `error_message`
- **AND** the queue message is dead-lettered, not left to redeliver indefinitely

#### Scenario: A failure's error_message is a curated message, not raw exception text

- **WHEN** a submission fails with a real HTTP response body or a raw `httpx` network exception
- **THEN** the `error_message` recorded for that batch's scans is a fixed, generic message (e.g.
  "Argo Workflow submission failed") — it does NOT contain the K8s API server URL, the response body,
  or any other internal detail from the underlying failure, which is logged server-side only

#### Scenario: A missing/invalid K8s credential does not mark the batch as a failed submission

- **WHEN** the worker cannot even attempt a submission because a required K8s credential
  (`WORKFLOWS_K8S_TOKEN`/`_CA_CERT`/`_API_URL`) is missing or invalid
- **THEN** the worker does NOT call `fail_cyl_pipeline_batch` for the claimed batch — a missing K8s
  API credential is distinct from a genuine submission attempt that failed, and must not permanently
  fail real scans because of it
- **AND** the claimed message remains unsettled so it becomes reclaimable once the configuration is
  fixed (via the visibility timeout, the same recovery path as any other unsettled claim)

#### Scenario: A refused batch is settled as failed, not left unsettled

- **WHEN** the worker claims a batch and `build_workflow_body` refuses it
- **THEN** the worker calls `fail_cyl_pipeline_batch` for that batch on the same claim, with the
  refusal's curated message, and makes no Kubernetes API request

### Requirement: The submitted Workflow's `spec` is loaded from a vendored canonical source, not hand-reconstructed

`build_workflow_body` SHALL load the `Workflow` shape from a vendored copy of `sleap-roots-pipeline`'s
canonical `sleap-roots-pipeline.yaml` (`services/workflows/vendored/sleap-roots-pipeline.yaml`), parse
it, and apply exactly six overrides to the parsed structure before returning it. It SHALL do so only
after the dispatch-refusal check has passed, and SHALL NOT read the vendored file for a refused
batch:

1. `spec.arguments.parameters[0].value` — set to the claimed batch's comma-joined `scan-ids`. Before
   overwriting, the worker SHALL assert `spec.arguments.parameters[0].name == "scan-ids"`; if this
   assertion fails, the worker SHALL treat it as a configuration error (raised before any network
   call), not proceed with a mis-targeted override.
2. `metadata.labels` — `submitted-by`, `pipeline-run-id`, `batch-index`, `environment` merged into
   whatever labels the vendored file already carries (it currently sets `project: busch-lab`), not a
   wholesale replacement of `metadata.labels`. If the vendored file already defines any of these four
   keys itself, the worker SHALL treat it as a configuration error rather than silently letting the
   dispatch-added value win.
3. `spec.ttlStrategy` — added only here, never present in the vendored file itself.
4. `metadata.namespace` — forced to the configured `WORKFLOWS_K8S_NAMESPACE`, overwriting whatever the
   vendored file sets (it currently hardcodes `runai-busch-lab`). This keeps namespace single-sourced
   with the value already used to build the submission URL — the Kubernetes API rejects a submission
   whose body namespace disagrees with the URL's namespace segment, so leaving the vendored value in
   place would create a second, independent source of truth that could silently diverge.
5. The `hostPath.path` of `images-input-dir`, `predictions-output-dir` and `traits-output-dir` — set to
   `<root>/input`, `<root>/predictions` and `<root>/traits` respectively, where `<root>` is the
   configured `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT`. Each volume's `hostPath.type` is left as the
   vendored file sets it. Volumes are matched by name, not by position.
6. The `secret.secretName` of `bloom-credentials` — set to the configured
   `WORKFLOWS_K8S_PIPELINE_SECRET_NAME`.

No field of the vendored structure other than these six SHALL be modified before submission.

#### Scenario: The vendored file's entrypoint, serviceAccountName and volume set pass through unmodified

- **WHEN** `build_workflow_body` constructs a Workflow for any batch
- **THEN** `spec.entrypoint` and `spec.serviceAccountName` in the returned structure are identical to
  the vendored file's values
- **AND** `spec.volumes` is identical to the vendored file's except for the three `hostPath.path`
  values and the one `secret.secretName` named in overrides 5 and 6

#### Scenario: No field outside the six documented overrides is modified

- **WHEN** `build_workflow_body` constructs a Workflow for any batch with prod's root and secret
- **THEN** the returned structure is identical to the vendored file's parsed structure with only
  `spec.arguments.parameters[0].value`, `metadata.labels`, `spec.ttlStrategy`, `metadata.namespace`,
  the three stage volumes' `hostPath.path` and `bloom-credentials`' `secret.secretName` changed —
  verified by comparing the full structure, not by spot-checking individual fields

#### Scenario: The vendored file's own labels are preserved, not dropped

- **WHEN** `build_workflow_body` constructs a Workflow for any batch
- **THEN** the returned `metadata.labels` includes `project: busch-lab` (from the vendored file) in
  addition to the four dispatch-added labels — the override merges rather than replaces

#### Scenario: A vendored file that defines one of the four dispatch label keys is a configuration error

- **WHEN** the vendored file's `metadata.labels` already defines one of `submitted-by`,
  `pipeline-run-id`, `batch-index`, or `environment`
- **THEN** `build_workflow_body` raises a configuration error before submitting anything, rather than
  silently letting the dispatch-added value overwrite it with no signal

#### Scenario: The submitted namespace always matches the configured namespace, never the vendored file's

- **WHEN** `build_workflow_body` constructs a Workflow for any batch
- **THEN** the returned `metadata.namespace` equals the configured `WORKFLOWS_K8S_NAMESPACE`, regardless
  of what value the vendored file sets

#### Scenario: A missing or unparseable vendored file is a configuration error, not a runtime surprise

- **WHEN** `services/workflows/vendored/sleap-roots-pipeline.yaml` is missing or fails to parse as YAML
- **THEN** `build_workflow_body` raises a configuration error before any network call, the same
  treatment as a missing `WORKFLOWS_K8S_TOKEN`/`_CA_CERT`/`_API_URL`

#### Scenario: A structurally-wrong-but-valid vendored file is also a configuration error

- **WHEN** `services/workflows/vendored/sleap-roots-pipeline.yaml` parses as valid YAML but lacks the
  expected `spec`/`metadata` structure (e.g. it parses to a list, or a mapping missing `spec` entirely)
- **THEN** `build_workflow_body` raises a configuration error before any network call, rather than
  letting a raw `KeyError`/`TypeError` escape from its own field lookups

#### Scenario: A symlinked vendored file is a configuration error

- **WHEN** `services/workflows/vendored/sleap-roots-pipeline.yaml` is a symlink rather than a regular
  file
- **THEN** `build_workflow_body` raises a configuration error before reading its content — a symlink
  swap could point a future edit somewhere the CI drift-check's path-scoped comparison would never
  notice

#### Scenario: A present-but-wrong-shaped scan-ids parameter is caught defensively, not just a missing one

- **WHEN** the vendored file's `spec.arguments.parameters` is present but is not a list of mappings
  (e.g. a string, or a list whose first element isn't a mapping)
- **THEN** `build_workflow_body` raises a configuration error before any network call, rather than
  letting a raw `AttributeError`/`TypeError` escape from its own field lookups

#### Scenario: A structurally-drifted scan-ids parameter is caught defensively

- **WHEN** the vendored file's `spec.arguments.parameters[0]` is not named `scan-ids` (e.g. a future
  canonical-file change reordered the parameters list)
- **THEN** `build_workflow_body` raises a configuration error before overwriting the wrong parameter's
  value or submitting a Workflow with an unset `scan-ids`

#### Scenario: A refused batch never reads the vendored file

- **WHEN** dispatch is refused (switched off, or unconfigured) and the vendored file is missing
- **THEN** `build_workflow_body` raises the dispatch-refused error, not a configuration error about
  the vendored file

## ADDED Requirements

### Requirement: Each environment's Workflows mount that environment's own credential and stage directories

Every Workflow the worker submits SHALL mount the Supabase credential Secret named by the dispatching environment's `WORKFLOWS_K8S_PIPELINE_SECRET_NAME`, and SHALL place its three stage volumes under the dispatching environment's `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT`, so that environments sharing the `runai-busch-lab` namespace never share a credential or a stage directory through Bloom's dispatch.

Neither value SHALL have a code default, so an environment that sets neither cannot fall back to
another environment's values. Within one environment, every run SHALL use the same root, so the
cluster-side skip-if-done dedup that depends on shared stage directories (sleap-roots-pipeline #37,
#71) keeps working across runs.

The committed environment defaults SHALL give prod and staging different secret names and different
roots, neither of which is an ancestor of, or equal to, the other. Staging's committed root and secret
SHALL equal the vendored file's own `hostPath`s (minus their sub-directory) and `secretName`. A re-pin
that moves those values therefore fails a test, and does not silently move staging off the
directories its dedup cache lives in.

Workflows submitted outside Bloom's dispatch worker (for example a manual `argo submit`) are not
covered by this requirement.

#### Scenario: A staging dispatch mounts staging's credential and directories

- **WHEN** the configured root is `/hpi/hpi_dev/users/eberrigan/pipeline_orchestration_tests/a4_poc`
  and the configured secret is `genericsecret-bloom-staging-pipeline-credentials`
- **THEN** the submitted `spec.volumes` equals the vendored file's `spec.volumes` exactly

#### Scenario: A prod dispatch mounts prod's credential and directories

- **WHEN** the configured root is `/hpi/hpi_dev/users/eberrigan/bloom_cyl_pipeline/prod` and the
  configured secret is `genericsecret-bloom-prod-pipeline-credentials`
- **THEN** the three stage paths are that root's `input`, `predictions` and `traits` sub-directories,
  each still `type: Directory`
- **AND** `bloom-credentials` names `genericsecret-bloom-prod-pipeline-credentials`
- **AND** none of the submitted paths or the secret name equals the vendored (staging) value

#### Scenario: The same numeric scan id in two environments is staged in two different places

- **WHEN** a batch containing scan id `42`, with the same run id, batch index and environment label,
  is built once under staging's root and secret and once under prod's
- **THEN** the two bodies are identical except for the three `hostPath.path` values and the
  `secretName`
- **AND** each path lies under its own environment's root, so stage-in's `<input>/scan_42/` resume
  check in one environment can never find the other environment's files

#### Scenario: Every run in one environment shares that environment's directories

- **WHEN** two batches from different runs are built under the same configuration
- **THEN** their `spec.volumes` are identical

#### Scenario: The committed defaults keep prod and staging apart

- **WHEN** `.env.prod.defaults` and `.env.staging.defaults` are compared
- **THEN** their `WORKFLOWS_K8S_PIPELINE_SECRET_NAME` values differ
- **AND** their `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT` values differ, and neither is an ancestor of the
  other when compared segment by segment
- **AND** both environments' root and secret pass the validation rules
- **AND** staging's root and secret equal the values the vendored file itself declares

### Requirement: The per-environment root and secret are validated before use

The worker SHALL accept a `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT` only if it is a non-empty absolute POSIX path that is not `/`, has no trailing `/`, no empty segment, no `.` or `..` segment (including a final one), and no whitespace or control character, judged identically on every operating system.

It SHALL accept a `WORKFLOWS_K8S_PIPELINE_SECRET_NAME` only if the whole value is a valid
Kubernetes object name: an RFC 1123 subdomain of at most 253 characters. A value with a trailing
newline is invalid.

Resolving either value SHALL never raise at import. An invalid value SHALL be treated exactly like a
missing one, causing the "not configured" refusal, and the reason it is invalid SHALL be available
for the refusal's server-side log.

#### Scenario: Both environments' committed roots are accepted on every platform

- **WHEN** the staging root and the prod root are validated on Windows and on Linux
- **THEN** both are accepted on both

#### Scenario: Malformed roots are rejected

- **WHEN** the root is unset, blank, relative, `/`, ends in `/`, contains `//`, contains whitespace or a
  newline, or has a `.` or `..` segment anywhere including last
- **THEN** it resolves as not configured

#### Scenario: Malformed secret names are rejected

- **WHEN** the secret name is unset, blank, contains an uppercase letter or `_`, starts or ends with
  `-` or `.`, contains `..`, ends with a newline, or is 254 characters long
- **THEN** it resolves as not configured
- **AND** a 253-character valid name and a dotted name such as `a.b` are accepted

#### Scenario: An invalid value never breaks import

- **WHEN** the workflows service is imported with an invalid root and secret in its environment
- **THEN** the import succeeds

### Requirement: The vendored Workflow's volume set is a closed contract

`build_workflow_body` SHALL require the vendored file's `spec.volumes` to be a list of exactly four mappings with unique names: `images-input-dir`, `predictions-output-dir` and `traits-output-dir`, each with a `hostPath` that is exactly a `path` and `type: Directory`, and `bloom-credentials`, with a `secret` that is exactly a `secretName`. Any other volume of any type, a missing, renamed, duplicated or differently typed one, or a `spec.volumes` that is not a list of mappings, SHALL be a configuration error raised before any network call.

This way, no volume added upstream can reach submission still pointing at shared, un-isolated
storage.

#### Scenario: An extra volume of any type is a configuration error

- **WHEN** the vendored file declares a fifth volume, whether `hostPath`, `secret`, `nfs`,
  `persistentVolumeClaim`, `projected` or `emptyDir`
- **THEN** `build_workflow_body` raises a configuration error before any network call

#### Scenario: A missing, renamed, duplicated or retyped volume is a configuration error

- **WHEN** the vendored file lacks one of the four volumes, renames one, repeats a name, declares
  `bloom-credentials` as a `hostPath`, or declares a stage volume whose `hostPath` has no `path`
- **THEN** `build_workflow_body` raises a configuration error, not a raw `KeyError` or `TypeError`

#### Scenario: A stage volume that could create its directory, or an optional Secret, is a configuration error

- **WHEN** a stage volume's `hostPath.type` is anything but `Directory` (for example
  `DirectoryOrCreate`, or absent), a stage volume is a `persistentVolumeClaim` or carries a second
  volume source, or `bloom-credentials`' `secret` carries anything besides `secretName` (for
  example `optional: true` or `items`)
- **THEN** `build_workflow_body` raises a configuration error before any network call, since each
  would let a pod start against storage or a credential that wasn't provisioned for it rather
  than sitting `Pending`

#### Scenario: A wrongly shaped volume list is a configuration error

- **WHEN** `spec.volumes` is absent, is not a list, or contains a non-mapping element
- **THEN** `build_workflow_body` raises a configuration error

#### Scenario: Reordered volumes are overridden by name

- **WHEN** the vendored file lists the same four volumes in a different order
- **THEN** each stage volume still receives its own sub-directory, and the order is preserved

### Requirement: The dispatch worker refuses to submit for an environment that is switched off or unconfigured

`build_workflow_body` SHALL raise a dispatch-refused error, before reading the vendored file, unless `CYL_PIPELINE_TRIGGER_ENABLED` is exactly `true` and both `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT` and `WORKFLOWS_K8S_PIPELINE_SECRET_NAME` resolved as valid.

The switch SHALL be checked first, so an environment that is both off and unconfigured reports that
it is off. The error SHALL carry its cause ("off" or "unconfigured") as a value, not only as text.

On a dispatch-refused error, the worker SHALL:

- log at WARNING or above the run id, batch id and the cause, naming the variable(s) involved and
  why each is invalid;
- call `fail_cyl_pipeline_batch` for the claimed batch on the same claim, with one of two fixed
  messages, "Pipeline dispatch is turned off in this environment" or "Pipeline dispatch is not
  configured in this environment", which contain no variable name, path or secret name;
- not call the Kubernetes API.

If the `fail_cyl_pipeline_batch` call itself errors, the worker SHALL log it and leave the claim for
redelivery, as on the submission-failure path.

Because every batch on the `cyl_pipeline_dispatch` queue reaches Argo only through this worker, the
refusal SHALL apply to every batch the worker claims, however its run was created: the web trigger,
or a direct `POST /workflows/pipeline`.

`cyl-pipeline-worker` SHALL receive `CYL_PIPELINE_TRIGGER_ENABLED`,
`WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT` and `WORKFLOWS_K8S_PIPELINE_SECRET_NAME` in every compose file.
In the dev compose file, the root and secret SHALL default to blank.

#### Scenario: A switched-off environment fails the batch without contacting the cluster

- **WHEN** `CYL_PIPELINE_TRIGGER_ENABLED` is unset, `false`, `TRUE`, `true ` or `true\n`, and the
  worker claims a batch
- **THEN** `fail_cyl_pipeline_batch` is called once for that batch with exactly "Pipeline dispatch is
  turned off in this environment"
- **AND** no request is sent to the Kubernetes API

#### Scenario: A switched-on but unconfigured environment fails the batch

- **WHEN** the switch is `true` but the root or the secret name is missing, blank, or invalid
- **THEN** `fail_cyl_pipeline_batch` is called once for that batch with exactly "Pipeline dispatch is
  not configured in this environment"
- **AND** no request is sent to the Kubernetes API

#### Scenario: Off and unconfigured reports off

- **WHEN** the switch is off and the root is also missing
- **THEN** the cause is "off"

#### Scenario: A refusal's recorded message carries no configuration detail, and its log does

- **WHEN** a batch is refused because the root is invalid
- **THEN** the recorded `error_message` contains no variable name, path or secret name
- **AND** a WARNING-or-above log record names `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT`, the run id and
  the batch id

#### Scenario: A failed refusal RPC leaves the claim for redelivery

- **WHEN** a batch is refused and the `fail_cyl_pipeline_batch` call raises
- **THEN** the worker logs the error and returns without settling the claim

#### Scenario: Switching an environment off fails the batches already queued there

- **WHEN** an environment's switch is changed to off while batches are queued
- **THEN** each of those batches fails with the "turned off" message as the worker claims it

#### Scenario: A dev stack without per-environment values refuses

- **WHEN** the dev compose stack runs with real cluster credentials but without the root and secret
  set
- **THEN** every batch it claims fails with the "not configured" message, and nothing is written to
  any environment's stage directories

#### Scenario: The worker receives the switch and the per-environment values

- **WHEN** `docker-compose.prod.yml` and `docker-compose.dev.yml` are parsed
- **THEN** `cyl-pipeline-worker` receives `CYL_PIPELINE_TRIGGER_ENABLED`,
  `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT` and `WORKFLOWS_K8S_PIPELINE_SECRET_NAME`
- **AND** staging's committed `CYL_PIPELINE_TRIGGER_ENABLED` is `true`
