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
- **THEN** the constructed Workflow's DAG tasks reference, in order,
  `sleap-roots-images-downloader-template`, `sleap-roots-predictor-template`,
  `sleap-roots-trait-extractor-template`, `sleap-roots-write-back-template`, and
  `sleap-roots-exit-gate-template`
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

## ADDED Requirements

### Requirement: The submitted DAG preserves the partial-success exit-gate contract

The three producer tasks (`images-downloader`, `predictor`, `trait-extractor`) SHALL each carry
`continueOn: {failed: true}`, and `write-back` SHALL NOT. The terminal `exit-gate` task SHALL be
the DAG's only leaf — no other task may depend on it — and SHALL receive the three producers' exit
codes as the parameters `images-downloader-code`, `predictor-code` and `trait-extractor-code`,
each valued `{{tasks.<task-name>.exitCode}}`.

These properties are load-bearing, not stylistic. Argo's `continueOn` keys on a node's *phase*, not
its exit code (argo-workflows#6396), so `continueOn` without a terminal gate would report a genuine
crash as `Succeeded`. Argo's `assessDAGPhase` derives the Workflow phase from the DAG's leaf, so a
producer that were also a leaf would surface its own `Failed` phase and defeat `continueOn`
entirely. The worker SHALL NOT reconstruct these by hand: they are properties of the vendored
canonical Workflow, and this requirement exists so that a hand-edit which silently drops one is
caught by the dispatch service's own tests rather than only in production.

#### Scenario: Producers continue past a partial-success exit; write-back does not

- **WHEN** any batch is submitted
- **THEN** the `images-downloader`, `predictor` and `trait-extractor` tasks each declare
  `continueOn: {failed: true}`
- **AND** the `write-back` task declares no `continueOn`, so a write-back failure omits the gate,
  which inherits `Failed` and fails the Workflow

#### Scenario: The exit gate is the DAG's only leaf

- **WHEN** any batch is submitted
- **THEN** no task in the DAG lists `exit-gate` among its `dependencies`
- **AND** `exit-gate` depends on `write-back`, making it the single terminal node from which the
  Workflow's phase is derived

#### Scenario: The exit gate receives every producer's real exit code

- **WHEN** any batch is submitted
- **THEN** the `exit-gate` task passes exactly the parameters `images-downloader-code`,
  `predictor-code` and `trait-extractor-code`
- **AND** each is valued `{{tasks.<task-name>.exitCode}}` for its corresponding producer, so a
  dropped or miswired parameter cannot reach the gate as an unresolved literal

### Requirement: The vendored Workflow's DAG shape is asserted independently of the byte-level drift check

The dispatch service's test suite SHALL assert the submitted DAG's shape — template references,
dependency chain, leaf identity, `continueOn` placement, and the gate's parameters — independently
of the repository's byte-level CI drift check, and a green drift check SHALL NOT be treated as
evidence that the DAG is correct.

`scripts/check_vendored_workflow_drift.py` compares the vendored file's bytes against the upstream
file at the pinned commit, and therefore validates *provenance only*. It reports success whenever
the vendored copy and `SLEAP_ROOTS_PIPELINE_REF` agree with each other, including when both have
been changed together to a DAG of the wrong shape.

#### Scenario: A consistent but wrongly-shaped vendored pair is still caught

- **WHEN** the vendored Workflow and its pinned SHA are updated together to a DAG that omits the
  exit gate, so the byte-level drift check passes
- **THEN** the dispatch service's DAG-shape tests fail, because they assert the five template
  references, the linear dependency chain, and the gate invariants against the constructed
  Workflow body rather than against the pin
