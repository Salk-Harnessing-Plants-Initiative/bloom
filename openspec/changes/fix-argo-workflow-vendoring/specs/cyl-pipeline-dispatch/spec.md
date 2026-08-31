## MODIFIED Requirements

### Requirement: A claimed batch is submitted as an Argo `Workflow` CRD via the raw Kubernetes REST API

For each claimed batch, the worker SHALL construct a `Workflow` object (`apiVersion:
argoproj.io/v1alpha1`, `kind: Workflow`) whose `spec` references the four already-registered
`WorkflowTemplate`s in sequence (`sleap-roots-images-downloader-template` →
`sleap-roots-predictor-template` → `sleap-roots-trait-extractor-template` →
`sleap-roots-write-back-template`, each dependent on the previous) via `templateRef`, parameterized by
that batch's own `scan-ids` (not the whole run's), and SHALL POST that object via `httpx` directly to
`{WORKFLOWS_K8S_API_URL}/apis/argoproj.io/v1alpha1/namespaces/{WORKFLOWS_K8S_NAMESPACE}/workflows` with
`Authorization: Bearer {WORKFLOWS_K8S_TOKEN}` and TLS verification against `WORKFLOWS_K8S_CA_CERT`. The
worker SHALL NOT invoke the `argo` CLI or call the Argo Server (`:8888`). A non-2xx response or a
network-level failure (timeout, connection error, TLS failure) SHALL be treated as a submission failure
for that batch, not retried within the same claim.

The constructed `spec` SHALL include every field the canonical vendored `sleap-roots-pipeline.yaml`
defines — including `spec.volumes`, `spec.entrypoint`, and `spec.serviceAccountName` — not only the DAG
task/`templateRef` structure. The worker SHALL NOT hand-build the `Workflow` body field-by-field in
Python; it SHALL derive it from the vendored canonical source, per the loading-and-override mechanism
defined in the next requirement in this same delta.

#### Scenario: A successful submission returns the generated Workflow name

- **WHEN** the K8s API server accepts a batch's submission
- **THEN** the response's `metadata.name` (K8s-generated from the submitted `generateName`) is
  captured as that batch's `argo_workflow_name`

#### Scenario: The submitted Workflow's parameters match exactly the claimed batch's scan ids

- **WHEN** a batch of `[12, 47, 9]` scan ids is claimed
- **THEN** the submitted Workflow's `scan-ids` argument contains exactly those three ids, not the
  run's full scan list and not another batch's ids

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
- **THEN** the constructed `spec.volumes` matches the vendored canonical file's `spec.volumes` exactly,
  including `images-input-dir`, `predictions-output-dir`, `traits-output-dir`, and `bloom-credentials`
- **AND** submission does not fail with a `volume '<name>' not found in workflow spec` error from the
  Argo controller

## ADDED Requirements

### Requirement: The submitted Workflow's `spec` is loaded from a vendored canonical source, not hand-reconstructed

`build_workflow_body` SHALL load the `Workflow` shape from a vendored copy of `sleap-roots-pipeline`'s
canonical `sleap-roots-pipeline.yaml` (`services/workflows/vendored/sleap-roots-pipeline.yaml`), parse
it, and apply exactly four overrides to the parsed structure before returning it:

1. `spec.arguments.parameters[0].value` — set to the claimed batch's comma-joined `scan-ids`. Before
   overwriting, the worker SHALL assert `spec.arguments.parameters[0].name == "scan-ids"`; if this
   assertion fails, the worker SHALL treat it as a configuration error (raised before any network
   call), not proceed with a mis-targeted override.
2. `metadata.labels` — `submitted-by`, `pipeline-run-id`, `batch-index`, `environment` merged into
   whatever labels the vendored file already carries (it currently sets `project: busch-lab`), not a
   wholesale replacement of `metadata.labels`.
3. `spec.ttlStrategy` — added only here, never present in the vendored file itself.
4. `metadata.namespace` — forced to the configured `WORKFLOWS_K8S_NAMESPACE`, overwriting whatever the
   vendored file sets (it currently hardcodes `runai-busch-lab`). This keeps namespace single-sourced
   with the value already used to build the submission URL — the Kubernetes API rejects a submission
   whose body namespace disagrees with the URL's namespace segment, so leaving the vendored value in
   place would create a second, independent source of truth that could silently diverge.

No field of the vendored structure other than these four SHALL be modified before submission.

#### Scenario: The vendored file's volumes, entrypoint, and serviceAccountName pass through unmodified

- **WHEN** `build_workflow_body` constructs a Workflow for any batch
- **THEN** `spec.volumes`, `spec.entrypoint`, and `spec.serviceAccountName` in the returned structure
  are identical to the vendored file's values

#### Scenario: No field outside the four documented overrides is modified

- **WHEN** `build_workflow_body` constructs a Workflow for any batch
- **THEN** the returned structure is identical to the vendored file's parsed structure with only
  `spec.arguments.parameters[0].value`, `metadata.labels`, `spec.ttlStrategy`, and `metadata.namespace`
  changed — verified by comparing the full structure, not by spot-checking individual fields

#### Scenario: The vendored file's own labels are preserved, not dropped

- **WHEN** `build_workflow_body` constructs a Workflow for any batch
- **THEN** the returned `metadata.labels` includes `project: busch-lab` (from the vendored file) in
  addition to the four dispatch-added labels — the override merges rather than replaces

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

#### Scenario: A structurally-drifted scan-ids parameter is caught defensively

- **WHEN** the vendored file's `spec.arguments.parameters[0]` is not named `scan-ids` (e.g. a future
  canonical-file change reordered the parameters list)
- **THEN** `build_workflow_body` raises a configuration error before overwriting the wrong parameter's
  value or submitting a Workflow with an unset `scan-ids`

### Requirement: CI verifies the vendored Workflow source has not drifted from the pinned upstream commit

A CI job SHALL fetch `sleap-roots-pipeline`'s `sleap-roots-pipeline.yaml` from
`https://raw.githubusercontent.com/talmolab/sleap-roots-pipeline/<SHA>/sleap-roots-pipeline.yaml`,
where `<SHA>` is the commit recorded in `services/workflows/vendored/SLEAP_ROOTS_PIPELINE_REF`, and
diff it byte-for-byte against `services/workflows/vendored/sleap-roots-pipeline.yaml`. Any mismatch
SHALL fail the pull request. This SHALL be the only network fetch of upstream content anywhere in this
mechanism — the running service and its container build SHALL NOT fetch this file at build or runtime.
The job SHALL distinguish, in its failure output, three cases that require different human responses
and SHALL NOT produce the same failure message: a transient failed upstream fetch (the check could not
run — re-run the job), the pinned commit no longer resolving upstream at all (an HTTP 404 — the pin
itself is invalid and needs re-pinning, not a re-run), and a genuine content mismatch (real drift). The
job SHALL retry a transient fetch failure at least once before treating it as such, but SHALL NOT retry
a 404 — retrying a commit that doesn't exist upstream cannot succeed. The job SHALL run under an
explicit time limit. This job's path-scoping SHALL be implemented as a condition on this job alone (e.g.
a job-level `if:`), never as a change to `pr-checks.yml`'s shared top-level trigger — a top-level path
filter would scope every other job in the file, not just this one.

#### Scenario: A PR whose vendored copy matches the pinned upstream commit passes

- **WHEN** a pull request's `services/workflows/vendored/sleap-roots-pipeline.yaml` is byte-for-byte
  identical to the pinned commit's `sleap-roots-pipeline.yaml` in `sleap-roots-pipeline`
- **THEN** the drift-check CI job passes

#### Scenario: A PR whose vendored copy has drifted from the pinned upstream commit fails loudly

- **WHEN** a pull request's vendored copy differs from the pinned commit's canonical file — whether
  because the vendored copy was hand-edited without bumping the pin, or the pin was bumped without
  updating the vendored copy to match
- **THEN** the drift-check CI job fails, blocking merge
- **AND** its failure message identifies this as a content mismatch, not a fetch failure

#### Scenario: A transient upstream fetch failure is distinguishable from a genuine drift

- **WHEN** the CI job's fetch of the canonical file fails for a reason unrelated to content (network
  error, transient GitHub unavailability) on every attempt including its retry
- **THEN** the job fails
- **AND** its failure message identifies this as a fetch failure, not a content mismatch — a reviewer
  reading the failure does not need to inspect the script's source to tell the two cases apart

#### Scenario: A pinned commit that no longer resolves upstream is distinguishable from a transient failure

- **WHEN** the pinned commit returns HTTP 404 from `raw.githubusercontent.com` (e.g. its branch was
  deleted after merge and the commit was garbage-collected)
- **THEN** the job fails without retrying
- **AND** its failure message identifies this as the pin no longer resolving upstream, requiring a
  re-pin — not "transient, re-run this job," which would never resolve the actual problem

#### Scenario: A fetch that fails once but succeeds on retry does not fail the job

- **WHEN** the CI job's first fetch attempt fails but a retried attempt succeeds and its content
  matches the vendored copy
- **THEN** the job passes — a single transient failure, recovered by the retry, is not itself treated
  as either a fetch failure or a drift
