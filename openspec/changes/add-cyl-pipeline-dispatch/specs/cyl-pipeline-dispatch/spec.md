## ADDED Requirements

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

### Requirement: Every submitted Workflow carries mandatory attribution labels

The worker SHALL stamp every submitted Workflow, at minimum, with `submitted-by: bloom-pipeline`,
`pipeline-run-id: <the batch's run id>`, and `batch-index: <the batch's index>` — a submission that
would omit these labels SHALL NOT be sent. This is a hard requirement (2026-08-06 cluster-admin
feedback), not best-effort: because submission goes directly to the Kubernetes API rather than through
Argo Server or the `argo` CLI, the submitted `Workflow` object receives none of Argo's automatic
`creator` labeling.

#### Scenario: Labels are present on every submission regardless of batch size

- **WHEN** any batch (one scan or many) is submitted
- **THEN** the submitted Workflow's `metadata.labels` includes `submitted-by`, `pipeline-run-id`, and
  `batch-index`, with correct values for that specific batch

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
