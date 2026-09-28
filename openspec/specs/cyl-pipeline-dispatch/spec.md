# cyl-pipeline-dispatch Specification

## Purpose
TBD - created by archiving change add-cyl-pipeline-dispatch. Update Purpose after archive.
## Requirements
### Requirement: A standalone worker claims batches from the dispatch queue, not the trigger route

Submission SHALL happen in a standalone polling process (`services/workflows/dispatch_worker.py`),
never inline in `POST /workflows/pipeline`'s request/response cycle. The worker SHALL claim at most one
batch at a time via `claim_cyl_pipeline_batch`, which SHALL hide the claimed message from other workers
for a configurable visibility timeout and mark that batch's rows as claimed. An empty queue SHALL
return no batch and the worker SHALL sleep for a configurable poll interval before retrying. A message
redelivered more times than a configurable maximum (a poison message — the worker crashed before it
could report an outcome) SHALL be dead-lettered by the claim step itself, without ever being handed to
the worker's submission logic.

#### Scenario: An empty queue yields no batch

- **WHEN** the worker polls `claim_cyl_pipeline_batch` and no message is available
- **THEN** it receives no batch
- **AND** it sleeps for the configured poll interval before polling again

#### Scenario: A claimed batch is hidden from other workers

- **WHEN** one worker successfully claims a batch
- **THEN** a second, concurrent claim call does not receive the same batch until the first claim's
  visibility timeout elapses

#### Scenario: A poison message is dead-lettered without reaching submission logic

- **WHEN** a message has been redelivered more times than the configured maximum (its previous
  claimants all crashed before completing or failing it)
- **THEN** the claim step dead-letters the message and returns no batch
- **AND** the worker's Argo-submission logic is never invoked for that message

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
- **THEN** the constructed `spec.volumes` matches the vendored canonical file's `spec.volumes` exactly,
  including `images-input-dir`, `predictions-output-dir`, `traits-output-dir`, and `bloom-credentials`
- **AND** submission does not fail with a `volume '<name>' not found in workflow spec` error from the
  Argo controller

### Requirement: Every submitted Workflow carries mandatory attribution labels

The worker SHALL stamp every submitted Workflow, at minimum, with `submitted-by: bloom-pipeline`,
`pipeline-run-id: <the batch's run id>`, `batch-index: <the batch's index>`, and
`environment: <WORKFLOWS_K8S_ENV_LABEL>` — a submission that would omit these labels SHALL NOT be sent.
This is a hard requirement (2026-08-06 cluster-admin feedback for the first three; the `environment`
label was added on PR review once `WORKFLOWS_K8S_NAMESPACE` was confirmed shared between prod and
staging — see design.md's "environment label" decision), not best-effort: because submission goes
directly to the Kubernetes API rather than through Argo Server or the `argo` CLI, the submitted
`Workflow` object receives none of Argo's automatic `creator` labeling.

#### Scenario: Labels are present on every submission regardless of batch size

- **WHEN** any batch (one scan or many) is submitted
- **THEN** the submitted Workflow's `metadata.labels` includes `submitted-by`, `pipeline-run-id`,
  `batch-index`, and `environment`, with correct values for that specific batch and deployment

### Requirement: Every submitted Workflow carries a `ttlStrategy` for auto-cleanup

The worker SHALL set `spec.ttlStrategy.secondsAfterCompletion` on every submitted Workflow to the value
configured via `WORKFLOWS_K8S_TTL_SECONDS`, so Argo's own controller — not this service — removes
completed Workflows after a bounded window. This is required because the submitting `bloom-pipeline`
identity holds
`create`/`get`/`list`/`watch` on `workflows.argoproj.io` but not `delete` — confirmed against the real
cluster (`kubectl auth can-i delete` denies it, and an actual delete attempt returns `Forbidden`) — so
this service has no other way to keep completed Workflows from accumulating.

#### Scenario: A submitted Workflow specifies a TTL

- **WHEN** any batch is submitted
- **THEN** the submitted Workflow's `spec.ttlStrategy.secondsAfterCompletion` is set to the configured
  value

### Requirement: Submission outcome is recorded before the message is settled

On a successful submission, the worker SHALL call `complete_cyl_pipeline_batch`, which records the
returned `argo_workflow_name` on every scan row in that batch and deletes the queue message. On a
failed submission, the worker SHALL call `fail_cyl_pipeline_batch`, which marks every scan row in that
batch `status = 'failed'` with an error message and dead-letters the queue message. Neither call SHALL
retry the submission itself — a failure is terminal for the claimed message (retry/requeue is
explicitly out of scope for this phase, matching the same deferral bloom PR #469 made for its own
queue).

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
- **THEN** the worker does NOT call `fail_cyl_pipeline_batch` for the claimed batch — a service
  misconfiguration is distinct from a genuine submission attempt that failed, and must not permanently
  fail real scans because of it
- **AND** the claimed message remains unsettled so it becomes reclaimable once the configuration is
  fixed (via the visibility timeout, the same recovery path as any other unsettled claim)

### Requirement: K8s API credentials are read from environment variables and validated eagerly

The worker SHALL read `WORKFLOWS_K8S_TOKEN`, `WORKFLOWS_K8S_CA_CERT` (PEM contents), and
`WORKFLOWS_K8S_API_URL` from the environment, mirroring `supabase_client.py`'s
`WORKFLOWS_SUPABASE_EMAIL`/`WORKFLOWS_SUPABASE_PASSWORD` pattern: module-level reads, a single
all-present check performed before any submission attempt, and a clear, specific error identifying
which variable(s) are missing if the check fails — not a generic connection error surfaced later.
`WORKFLOWS_K8S_NAMESPACE` SHALL default to `runai-busch-lab` if unset.

#### Scenario: Missing credentials fail fast with a specific error

- **WHEN** any of `WORKFLOWS_K8S_TOKEN`/`WORKFLOWS_K8S_CA_CERT`/`WORKFLOWS_K8S_API_URL` is unset
- **THEN** the worker's submission function raises before attempting any network call
- **AND** the error identifies which variable(s) are missing

#### Scenario: Namespace defaults when unset

- **WHEN** `WORKFLOWS_K8S_NAMESPACE` is not set in the environment
- **THEN** submissions target `runai-busch-lab`

#### Scenario: TTL defaults when unset, rather than being eagerly required

- **WHEN** `WORKFLOWS_K8S_TTL_SECONDS` is not set in the environment
- **THEN** submissions use a default of `3600` seconds — unlike the three credential variables, a
  missing TTL does not raise, since a safe default exists

#### Scenario: The CA certificate's escaped newlines are restored before use

- **WHEN** `WORKFLOWS_K8S_CA_CERT` is read from the environment with its PEM newlines stored as
  literal `\n` escape sequences (this repo's env-injection pipeline is line-oriented and cannot carry
  a real multi-line value)
- **THEN** the worker un-escapes them back into real newlines before constructing the TLS verification
  context, so a well-formed PEM certificate reaches `httpx`

### Requirement: The worker finishes an in-flight claim before exiting on shutdown

On receiving `SIGTERM` or `SIGINT`, the worker SHALL finish whatever batch it is currently
submitting — including its `complete_cyl_pipeline_batch`/`fail_cyl_pipeline_batch` call — before
exiting its polling loop, rather than terminating mid-submission. It SHALL NOT claim a new batch after
receiving the signal.

#### Scenario: A signal during submission does not abandon the in-flight batch

- **WHEN** the worker receives `SIGTERM` while a batch's submission (and its subsequent complete/fail
  call) is in progress
- **THEN** that submission and its recorded outcome complete normally before the process exits

#### Scenario: A signal does not start a new claim

- **WHEN** the worker receives `SIGTERM` or `SIGINT` while idle (not mid-submission)
- **THEN** it does not call `claim_cyl_pipeline_batch` again before exiting

### Requirement: Namespace targeting is a single configured value in v1

The worker SHALL submit every batch to the single namespace configured via `WORKFLOWS_K8S_NAMESPACE`,
regardless of which scans/experiment/wave a batch's run originated from — no `lab`/`project`/
namespace-ownership column exists on `cyl_scans`, `cyl_experiments`, or `cyl_waves` to resolve a
per-request namespace from. Per-request or per-lab namespace selection is explicitly out of scope for
this phase.

#### Scenario: All batches target the same namespace regardless of origin

- **WHEN** batches from runs targeting different experiments are submitted
- **THEN** every submission targets the same configured namespace

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
   wholesale replacement of `metadata.labels`. If the vendored file already defines any of these four
   keys itself, the worker SHALL treat it as a configuration error rather than silently letting the
   dispatch-added value win.
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

### Requirement: CI verifies the vendored Workflow source has not drifted from the pinned upstream commit

A CI job SHALL fetch `sleap-roots-pipeline`'s `sleap-roots-pipeline.yaml` from
`https://raw.githubusercontent.com/talmolab/sleap-roots-pipeline/<SHA>/sleap-roots-pipeline.yaml`,
where `<SHA>` is the commit recorded in `services/workflows/vendored/SLEAP_ROOTS_PIPELINE_REF`, and
diff it byte-for-byte against `services/workflows/vendored/sleap-roots-pipeline.yaml`. Any mismatch
SHALL fail the pull request. This SHALL be the only network fetch of upstream content anywhere in this
mechanism — the running service and its container build SHALL NOT fetch this file at build or runtime.
The job SHALL distinguish, in its failure output, three cases that require different human responses
and SHALL NOT produce the same failure message: a transient failed upstream fetch (the check could not
run — re-run the job), the pinned commit no longer resolving upstream at all (an HTTP 404 that persists
across every attempt — the pin itself is invalid and needs re-pinning, not a re-run), and a genuine
content mismatch (real drift). The job SHALL retry a transient fetch failure at least once before
treating it as such. An HTTP 404 SHALL be given the same retry chance as any other fetch failure before
being treated as the pin no longer resolving upstream — a 404 immediately after a commit is pushed can
be CDN propagation lag, indistinguishable from a genuinely dangling pin without giving it a chance to
resolve; only a 404 that persists across the full retry budget SHALL be reported as the pin no longer
resolving. The job SHALL run under an explicit time limit, and SHALL use a distinct exit code for each
of the four outcomes (match, drift, transient fetch failure, pin no longer resolving), not just distinct
messages, so automation keying off exit status alone can still tell them apart. This job's path-scoping
SHALL be implemented as a condition on this job alone (e.g. a job-level `if:`), never as a change to
`pr-checks.yml`'s shared top-level trigger — a top-level path filter would scope every other job in the
file, not just this one.

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

- **WHEN** the pinned commit returns HTTP 404 from `raw.githubusercontent.com` on every attempt
  including its retry (e.g. its branch was deleted after merge and the commit was garbage-collected)
- **THEN** the job fails
- **AND** its failure message identifies this as the pin no longer resolving upstream, requiring a
  re-pin — not "transient, re-run this job," which would never resolve the actual problem
- **AND** its exit code is distinct from both the generic transient-fetch-failure code and the
  content-drift code

#### Scenario: A single HTTP 404 recovers on retry and does not fail the job

- **WHEN** the pinned commit returns HTTP 404 on the first attempt but a retried attempt succeeds and
  its content matches the vendored copy (CDN propagation lag right after the commit was pushed, not a
  genuinely dangling pin)
- **THEN** the job passes — a single 404, recovered by the retry, is not treated as either a dangling
  pin or a content drift

#### Scenario: A fetch that fails once but succeeds on retry does not fail the job

- **WHEN** the CI job's first fetch attempt fails but a retried attempt succeeds and its content
  matches the vendored copy
- **THEN** the job passes — a single transient failure, recovered by the retry, is not itself treated
  as either a fetch failure or a drift

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
  `exit-gate`, which is what makes the reference resolvable. `argo lint` rejects both a
  non-ancestor reference and a reference to a task that does not exist (measured with v3.6.5:
  renaming a producer while leaving the gate's `{{tasks.<old>.exitCode}}` stale gives
  `failed to resolve`, exit 1) — but this repository does not run `argo lint` in CI, and the raw
  Kubernetes API accepts the submission regardless, so the assertion is this pipeline's only
  automatic guard against it

### Requirement: A committed comparator verifies the registered templates satisfy the vendored Workflow's contract

A committed comparator SHALL verify that the `WorkflowTemplate`s registered in the dispatch namespace
satisfy the contract declared by `services/workflows/vendored/sleap-roots-pipeline.yaml`. The five
templates the submitted `Workflow` resolves by `templateRef` live only in the cluster and are applied
by hand, separately from merging, so nothing else establishes that the contract holds.

The comparator SHALL derive every expectation from the vendored `Workflow` itself — the artifact this
repo owns and dispatches from — and SHALL NOT fetch upstream content of any kind. It SHALL resolve the
DAG through `spec.entrypoint` rather than indexing `spec.templates[0]`, so appending a template and
repointing the entrypoint cannot leave it inspecting the wrong one.

For each task in the resolved DAG, the comparator SHALL assert all of the following, and SHALL report
any failure as a contract violation naming the specific template and field:

- the `templateRef.name` `WorkflowTemplate` exists in the namespace;
- that template declares an inner template whose `name` equals the referenced `templateRef.template`;
- every parameter the task passes in `arguments.parameters` is declared in that inner template's
  `inputs.parameters`;
- every entry in that inner template's `inputs.parameters` that carries none of `default`, `value`
  or `valueFrom` is supplied by that task's own `arguments.parameters`. A workflow-level
  `spec.arguments.parameters` entry SHALL NOT be treated as supplying it: those bind to the
  entrypoint template's inputs and are otherwise available only for `{{workflow.parameters.*}}`
  substitution, so a template reached by `templateRef` whose required input merely shares a name
  with a global is still unresolvable at run time;
- every `volumeMounts[].name` on that inner template is declared in the `Workflow`'s `spec.volumes`,
  counting every container-shaped member of the template — `container`, `script`, `initContainers`
  and `sidecars` alike, since reaching only into `container` would make this assertion and the
  image assertions vacuous on an equally valid template shape;
- every `{{workflow.parameters.<name>}}` the inner template references is declared in the `Workflow`'s
  `spec.arguments.parameters`.

Each of those six is independently capable of producing the failure this comparator exists to catch: a
`Workflow` the Kubernetes API server accepts at submit — because `templateRef` resolution is the Argo
controller's job and the dispatch worker POSTs to the raw K8s API — which the controller then errors,
marking every scan in the batch failed with no requeue and no dead letter.

The comparator SHALL assert that the set of `templateRef` `(name, template)` pairs it discovered equals
a committed expected set, rather than comparing the discovered count against itself. Comparing a parse
result against a number derived from the same parse is a tautology that can never fail, which is the
failure mode this requirement exists to outlaw. Discovery yielding anything other than the expected set
SHALL be reported as a failed check, not as a satisfied contract and not as a contract violation.

Container image tags and digests SHALL NOT be compared against any value recorded in this repository,
and a tag or digest differing from a recorded pin SHALL NOT affect the verdict. The registered templates
are owned by `sleap-roots-pipeline` and advance independently of this repo's vendored pin by design;
treating their image pins as this repo's invariant reports drift on a correct cluster after every
legitimate upstream template update, and an operator who learns to wave that through will wave a real
contract violation through with it.

The comparator SHALL nonetheless assert image-reference self-consistency, which requires no recorded
pin and cannot be disturbed by a legitimate upstream bump because both sides of the comparison move
together: where a container declares exactly one container-digest environment variable with a literal
`value`, and that container's `image` is digest-pinned, that variable's value SHALL equal the image's
digest. Those values are recorded as the provenance of the trait rows the pipeline writes, so a
disagreement durably attributes trait data to an image that did not produce it. The three
qualifications are load-bearing and SHALL NOT be dropped: a `valueFrom` digest is not knowable here,
an image without a digest offers nothing to compare against, and two such variables on one container
mean it is recording some other image's digest for provenance chaining — each would otherwise report
a violation that no change to this repository could clear. Image references that the comparator does
not assert SHALL still be printed, on every path including the violation path, for the operator's
record.

This assertion is conditional on the environment variable being present, and its **absence is not
checked**. A template that is digest-pinned but declares no digest variable, or that drops both,
SHALL therefore pass — which is the shape of the defect that produced empty provenance in every
result envelope before it was fixed upstream. Recorded as a known limit of this requirement rather
than left for a reader to infer from the wording.

The comparator SHALL use exit code `0` for a satisfied contract, `1` for a contract violation, and `2`
for a check that could not be completed, and SHALL NOT report any one of those three as another. It
SHALL report every violation it finds rather than stopping at the first, so one run tells an operator
everything that is wrong. Where a run finds both a contract violation and a condition it could not
check, the could-not-check code SHALL win, because a partial check SHALL NOT be reportable as a
complete verdict.

The following SHALL each be reported as a check that could not be completed, never as a contract
violation and never as success: the cluster being unreachable; a vendored `Workflow` that is missing,
fails to parse, or lacks the expected entrypoint-and-DAG structure; a `kubectl` invocation failing for
any reason other than the named object not existing; and `kubectl` returning success with output that
is not a `WorkflowTemplate`. Only the named object genuinely not existing SHALL be reported as a
missing template.

The comparator's assertions SHALL be covered by tests that run without cluster access and without
network access, so its logic is verified in CI even though the comparator itself requires a live
cluster and therefore cannot run there.

#### Scenario: Newer image pins on a structurally conforming cluster report success

- **WHEN** every registered `WorkflowTemplate` named by the vendored `Workflow` satisfies all six
  contract assertions
- **AND** one or more of those templates carries a container image tag or digest that differs from
  anything this repo records, because the templates were legitimately updated upstream since this repo
  last vendored the `Workflow`
- **THEN** the comparator reports the contract satisfied and exits `0`
- **AND** it prints the live image references it observed

#### Scenario: A workflow-level parameter that no template declares as an input is not a violation

- **WHEN** the vendored `Workflow` declares a parameter in `spec.arguments.parameters` that a resolved
  inner template does not declare in its own `inputs.parameters`
- **THEN** the comparator reports the contract satisfied and exits `0` — a workflow-level global is not
  a per-task argument, and conflating the two would report a violation on a correct cluster

#### Scenario: A referenced WorkflowTemplate that is not registered is a contract violation

- **WHEN** the vendored `Workflow` names a `WorkflowTemplate` that the cluster confirms does not exist
- **THEN** the comparator exits `1` and its output names the missing `WorkflowTemplate`

#### Scenario: A wrong inner template name is a contract violation

- **WHEN** a referenced `WorkflowTemplate` exists but declares no inner template whose `name` equals
  the `templateRef.template` the vendored `Workflow` references
- **THEN** the comparator exits `1` and its output names both the `WorkflowTemplate` and the missing
  inner template

#### Scenario: A parameter the Workflow passes but the template does not declare is a contract violation

- **WHEN** the vendored `Workflow` passes a parameter to a task whose resolved inner template does not
  declare that name in its `inputs.parameters`
- **THEN** the comparator exits `1` and its output names the undeclared parameter

#### Scenario: A required template input that the Workflow does not supply is a contract violation

- **WHEN** a resolved inner template declares an `inputs.parameters` entry with neither a `default` nor
  a `value`, and neither the calling task's `arguments.parameters` nor the `Workflow`'s
  `spec.arguments.parameters` supplies it
- **THEN** the comparator exits `1` and its output names the unsupplied parameter
- **AND** this holds even though the submission would be accepted by the API server, because the Argo
  controller fails to resolve the parameter only after the run has started

#### Scenario: A volume mount naming a volume the Workflow does not declare is a contract violation

- **WHEN** a resolved inner template mounts a `volumeMounts[].name` that the vendored `Workflow` does
  not declare in `spec.volumes`
- **THEN** the comparator exits `1` and its output names the undeclared volume

#### Scenario: A workflow-parameter reference the Workflow does not declare is a contract violation

- **WHEN** a resolved inner template references `{{workflow.parameters.<name>}}` for a name the
  vendored `Workflow` does not declare in `spec.arguments.parameters`
- **THEN** the comparator exits `1` and its output names the unresolvable reference

#### Scenario: A container-digest environment variable disagreeing with its own image is a contract violation

- **WHEN** an inner template declares a container-digest environment variable naming its own image, and
  that variable's value is not the digest on that container's `image`
- **THEN** the comparator exits `1` and its output names both the environment variable's value and the
  image digest it disagrees with — trait rows produced under that template would record an image that
  did not produce them

#### Scenario: An unreachable cluster is reported as a failed check, not as drift

- **WHEN** the cluster cannot be reached at all — VPN down, expired credentials, or a wrong kubeconfig
- **THEN** the comparator exits `2`
- **AND** it does not report any `WorkflowTemplate` as missing or non-conforming

#### Scenario: A kubectl failure other than a missing object is a failed check, not a violation

- **WHEN** a per-template `kubectl` invocation fails for a reason other than the named object not
  existing — a transient API error, a permission denial, `kubectl` absent from `PATH`, or a timeout —
  or succeeds while returning output that is not a `WorkflowTemplate`
- **THEN** the comparator exits `2`
- **AND** it does not report that template as missing

#### Scenario: A missing or unparseable vendored Workflow is a failed check

- **WHEN** the vendored `Workflow` is absent, fails to parse as YAML, parses to something other than a
  mapping, or lacks the expected entrypoint-and-DAG structure
- **THEN** the comparator exits `2` — not `1`, which would report a configuration error as a cluster
  contract violation

#### Scenario: Discovering a templateRef set other than the expected one is a failed check

- **WHEN** resolving the vendored `Workflow`'s DAG yields a set of `templateRef` `(name, template)`
  pairs that does not equal the committed expected set
- **THEN** the comparator exits `2` and names the expected and discovered sets
- **AND** it does not report success, on the grounds that a check whose failure mode is "everything is
  fine" is worse than no check at all

#### Scenario: A could-not-check condition outranks a contract violation in the same run

- **WHEN** one run finds both a genuine contract violation and a condition it could not check
- **THEN** the comparator exits `2`, not `1`
- **AND** its output reports both, so the operator sees the violation as well as the incomplete check

#### Scenario: The comparator fetches no upstream content on any path

- **WHEN** the comparator runs to completion on any path — satisfied, violated, or could-not-check
- **THEN** it issues no outbound request other than the `kubectl` calls to the configured cluster,
  and in particular fetches nothing from the upstream repository and reads no pinned commit,
  because every expectation is derived from the vendored `Workflow` in this repository

### Requirement: A recorded verification of the registered templates names what was run and observed

A record asserting that the registered templates were verified SHALL name the comparator invoked, the
namespace checked, the date, and the exit code observed. A verdict recorded without those cannot be
reproduced or attributed, and SHALL NOT be treated as evidence that the contract held.

This exists because a verification was recorded that could not have happened: a task record dated
2026-09-21 asserted that this repo's comparator reported all five templates in sync with the pin and
exited `0`, on a day when the pin had not moved since it was set and the templates had diverged from it
four days earlier, so that comparator could only have reported drift. Two comparators answer different
questions about the same five objects, and a record naming neither is indistinguishable from a record
of the wrong one.

#### Scenario: A verification record that names its comparator, namespace, date and exit code is usable

- **WHEN** a task record asserts the registered templates were verified
- **AND** it names the comparator invoked, the namespace, the date, and the observed exit code
- **THEN** a later reader can re-run exactly that invocation and compare results

#### Scenario: A verification record missing its provenance is not evidence

- **WHEN** a record asserts the templates were verified but does not name which comparator produced the
  verdict
- **THEN** the record SHALL NOT be relied on as evidence the contract held, because two comparators
  with different sources of truth return different verdicts on the same cluster at the same moment

