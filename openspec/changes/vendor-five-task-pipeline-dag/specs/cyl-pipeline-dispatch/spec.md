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
Python; it SHALL derive it from the vendored canonical source, per the loading-and-override mechanism
defined in the `cyl-pipeline-dispatch` requirement covering the vendored canonical source.

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
- **THEN** the constructed `spec.volumes` matches the vendored canonical file's `spec.volumes` exactly,
  including `images-input-dir`, `predictions-output-dir`, `traits-output-dir`, and `bloom-credentials`
- **AND** submission does not fail with a `volume '<name>' not found in workflow spec` error from the
  Argo controller

## ADDED Requirements

### Requirement: The submitted DAG preserves the partial-success exit-gate wiring

The three producer tasks (`images-downloader`, `predictor`, `trait-extractor`) SHALL each carry
`continueOn: {failed: true}`, and no other task SHALL carry `continueOn` — in particular not
`write-back` and not the terminal `exit-gate`. The `exit-gate` task SHALL be the DAG's only leaf —
no other task may depend on it — and SHALL receive the three producers' exit codes as the
parameters `images-downloader-code`, `predictor-code` and `trait-extractor-code`, each valued
`{{tasks.<task-name>.exitCode}}`.

These properties are load-bearing, not stylistic. Argo's `continueOn` keys on a node's *phase*, not
its exit code (argo-workflows#6396), so `continueOn` without a terminal gate would report a genuine
crash as `Succeeded`. Argo's `assessDAGPhase` derives the Workflow phase from the DAG's leaves, so a
producer that were also a leaf would surface its own `Failed` phase and defeat `continueOn`
entirely — and a `continueOn` on the gate itself would make the leaf continuable, restoring the same
defect at the last hop. The worker SHALL NOT reconstruct these by hand: they are properties of the
vendored canonical Workflow, and this requirement exists so that a hand-edit which silently drops or
misplaces one is caught by the dispatch service's own tests rather than only in production.

**Scope note.** The gate's `{0,3}` allowlist — the semantics that decide *which* exit codes pass —
lives in the `sleap-roots-exit-gate-template` object registered in the cluster, which this
repository neither vendors nor drift-checks. What this requirement constrains is the DAG-side
*wiring* that feeds that allowlist. The allowlist itself is only ever exercised live.

**Verification note.** `scripts/check_vendored_workflow_drift.py` validates provenance only — it
reports success whenever the vendored copy and `SLEAP_ROOTS_PIPELINE_REF` agree with each other,
including when both were changed together to a DAG of the wrong shape. A green drift check is
therefore not evidence that any of the properties above hold.

#### Scenario: Producers continue past a partial-success exit; nothing else does

- **WHEN** any batch is submitted
- **THEN** the `images-downloader`, `predictor` and `trait-extractor` tasks each declare exactly
  `continueOn: {failed: true}`
- **AND** no other task in the DAG declares `continueOn` at all — so `write-back`'s failure omits
  the gate, which inherits `Failed` and fails the Workflow, and the terminal gate cannot itself be
  made continuable

#### Scenario: The exit gate is the DAG's only leaf

- **WHEN** any batch is submitted
- **THEN** exactly one task in the DAG is depended on by no other task, and that task is `exit-gate`
- **AND** `exit-gate` depends on `write-back`, making it the single terminal node from which the
  Workflow's phase is derived

#### Scenario: The exit gate receives every producer's real exit code

- **WHEN** any batch is submitted
- **THEN** the `exit-gate` task passes exactly the parameters `images-downloader-code`,
  `predictor-code` and `trait-extractor-code`
- **AND** each is valued `{{tasks.<task-name>.exitCode}}` for its corresponding producer, so a
  dropped or miswired parameter cannot reach the gate as an unresolved literal
- **AND** every producer the gate references is a task that exists in the DAG and is an ancestor of
  `exit-gate`, which is what makes the reference resolvable at submit time — Argo's
  `validateDAGTaskArgumentDependency` rejects a non-ancestor reference, but cannot see a reference
  to a task that was renamed out from under it
