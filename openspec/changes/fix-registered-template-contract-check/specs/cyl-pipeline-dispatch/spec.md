## ADDED Requirements

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
