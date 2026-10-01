## MODIFIED Requirements

### Requirement: A claimed batch is submitted as an Argo `Workflow` CRD via the raw Kubernetes REST API

For each claimed batch, the worker SHALL construct a `Workflow` object (`apiVersion:
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
- **AND** each volume differs from the vendored file only in the per-environment fields the
  isolation requirement below overrides (the three `hostPath.path` values and `bloom-credentials`'
  `secret.secretName`)
- **AND** submission does not fail with a `volume '<name>' not found in workflow spec` error from the
  Argo controller

### Requirement: The submitted Workflow's `spec` is loaded from a vendored canonical source, not hand-reconstructed

`build_workflow_body` SHALL load the `Workflow` shape from a vendored copy of `sleap-roots-pipeline`'s
canonical `sleap-roots-pipeline.yaml` (`services/workflows/vendored/sleap-roots-pipeline.yaml`), parse
it, and apply exactly six overrides to the parsed structure before returning it:

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
   configured `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT` (see the per-environment isolation requirement).
   Each volume's `hostPath.type` is left as the vendored file sets it.
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

- **WHEN** `build_workflow_body` constructs a Workflow for any batch
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


## ADDED Requirements

### Requirement: Each environment's Workflows mount that environment's own credential and stage directories

Every submitted Workflow SHALL mount the Supabase credential Secret named by the dispatching environment's `WORKFLOWS_K8S_PIPELINE_SECRET_NAME`, and SHALL place its three stage volumes under the dispatching environment's `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT`, so that environments sharing the `runai-busch-lab` namespace never share a credential or a stage directory. Neither value SHALL have a code default — an environment that sets neither cannot fall back to another environment's values.

Within one environment, every run SHALL use the same root, so the cluster-side skip-if-done dedup that
depends on shared stage directories (sleap-roots-pipeline #37, #71) keeps working across runs.

The configured root SHALL be a non-empty absolute POSIX path that is not `/`, does not end in `/`,
contains no whitespace, and has no `.` or `..` segment. The configured secret name SHALL be a valid
Kubernetes object name (an RFC 1123 subdomain of at most 253 characters).

`build_workflow_body` SHALL treat the vendored file's volume set as a closed contract: exactly three
`hostPath` volumes, named `images-input-dir`, `predictions-output-dir` and `traits-output-dir`, and
exactly one `secret` volume, named `bloom-credentials`. Any other `hostPath` or `secret` volume, or a
missing one, SHALL be a configuration error raised before any network call, so a volume added
upstream cannot reach submission still pointing at a shared, un-isolated path.

The committed environment defaults SHALL give prod and staging different roots, neither nested
inside the other, and different secret names. Staging's values SHALL equal the vendored file's
current values, so staging's submitted body is unchanged by this requirement.

#### Scenario: A staging dispatch mounts staging's credential and directories

- **WHEN** the configured root is `/hpi/hpi_dev/users/eberrigan/pipeline_orchestration_tests/a4_poc`
  and the configured secret is `genericsecret-bloom-staging-pipeline-credentials`
- **THEN** the submitted Workflow's `images-input-dir`, `predictions-output-dir` and
  `traits-output-dir` paths are that root's `input`, `predictions` and `traits` sub-directories
- **AND** `bloom-credentials` names `genericsecret-bloom-staging-pipeline-credentials`
- **AND** the submitted `spec.volumes` equals the vendored file's `spec.volumes` exactly

#### Scenario: A prod dispatch mounts prod's credential and directories

- **WHEN** the configured root is `/hpi/hpi_dev/users/eberrigan/bloom_cyl_pipeline/prod` and the
  configured secret is `genericsecret-bloom-prod-pipeline-credentials`
- **THEN** the three stage paths are under `/hpi/hpi_dev/users/eberrigan/bloom_cyl_pipeline/prod`
- **AND** `bloom-credentials` names `genericsecret-bloom-prod-pipeline-credentials`
- **AND** no submitted path or secret name equals staging's

#### Scenario: The same numeric scan id in two environments is staged in two different places

- **WHEN** prod and staging each dispatch a batch containing scan id `42`
- **THEN** each Workflow's `images-input-dir` resolves under its own environment's root, so stage-in's
  `<input>/scan_42/` resume check in one environment can never find the other environment's files

#### Scenario: A vendored file with an extra or missing stage volume is a configuration error

- **WHEN** the vendored file declares a fourth `hostPath` volume, a second `secret` volume, or lacks one
  of the four expected volumes
- **THEN** `build_workflow_body` raises a configuration error before any network call

#### Scenario: The committed defaults keep prod and staging apart

- **WHEN** `.env.prod.defaults` and `.env.staging.defaults` are compared
- **THEN** their `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT` values differ and neither is a path prefix of
  the other
- **AND** their `WORKFLOWS_K8S_PIPELINE_SECRET_NAME` values differ

### Requirement: The dispatch worker refuses to submit for an environment that is switched off or unconfigured

`build_workflow_body` SHALL refuse to construct a Workflow — raising a dispatch-refused error before reading or modifying the vendored body — unless `CYL_PIPELINE_TRIGGER_ENABLED` is exactly `true` and both `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT` and `WORKFLOWS_K8S_PIPELINE_SECRET_NAME` are present and valid. The switch SHALL be checked first, so an environment that is both off and unconfigured reports that it is off.

On a dispatch-refused error, the worker SHALL call `fail_cyl_pipeline_batch` for the claimed batch
immediately, recording a fixed, curated `error_message` that distinguishes "turned off" from "not
configured" without naming variables or values (those are logged server-side only), and SHALL NOT
call the Kubernetes API. Unlike a missing K8s API credential, a refusal is a deliberate state, not a
transient misconfiguration, so the claim SHALL NOT be left unsettled for redelivery.

Because every pipeline run reaches Argo only through this worker, this refusal SHALL apply to runs
created by any path: the web trigger, a direct `POST /workflows/pipeline`, or a direct insert into the
run tables.

#### Scenario: A switched-off environment fails the batch without contacting the cluster

- **WHEN** `CYL_PIPELINE_TRIGGER_ENABLED` is unset, `false`, or `TRUE`, and the worker claims a batch
- **THEN** `fail_cyl_pipeline_batch` is called once for that batch with the "turned off" message
- **AND** no request is sent to the Kubernetes API

#### Scenario: A switched-on but unconfigured environment fails the batch

- **WHEN** the switch is `true` but the root or the secret name is missing, blank, or invalid
- **THEN** `fail_cyl_pipeline_batch` is called once for that batch with the "not configured" message
- **AND** no request is sent to the Kubernetes API

#### Scenario: A refusal's error message carries no configuration detail

- **WHEN** a batch is refused for either reason
- **THEN** the recorded `error_message` contains neither a variable name nor a configured path or
  secret name

#### Scenario: A refusal is not left for redelivery

- **WHEN** a batch is refused
- **THEN** the worker settles it through `fail_cyl_pipeline_batch` on that same claim, rather than
  leaving it to be dead-lettered later as a poison message

#### Scenario: Prod ships switched off

- **WHEN** `.env.prod.defaults` and `.env.staging.defaults` are read
- **THEN** prod's `CYL_PIPELINE_TRIGGER_ENABLED` is `false` and staging's is `true`
- **AND** `cyl-pipeline-worker` in `docker-compose.prod.yml` receives `CYL_PIPELINE_TRIGGER_ENABLED`,
  `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT` and `WORKFLOWS_K8S_PIPELINE_SECRET_NAME`
