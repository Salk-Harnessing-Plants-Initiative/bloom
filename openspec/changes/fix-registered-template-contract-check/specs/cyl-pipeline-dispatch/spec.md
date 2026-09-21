## ADDED Requirements

### Requirement: A committed comparator verifies the registered templates satisfy the vendored Workflow's contract

A committed comparator SHALL verify that the `WorkflowTemplate`s registered in the dispatch namespace
satisfy the contract declared by `services/workflows/vendored/sleap-roots-pipeline.yaml`, and SHALL be
runnable before any change that depends on that contract holding. The five templates the submitted
`Workflow` resolves by `templateRef` live only in the cluster and are applied by hand, separately from
merging, so nothing else establishes that the contract holds.

The comparator SHALL derive every expectation from the vendored `Workflow` itself — the artifact this
repo owns and dispatches from — and SHALL NOT fetch upstream content of any kind. For each
`templateRef` reached from the vendored `Workflow`'s DAG, it SHALL assert that the named
`WorkflowTemplate` exists in the namespace, that the template declares an inner template whose `name`
equals the referenced `template`, and that every parameter the `Workflow` passes to that task is
declared in that inner template's `inputs.parameters`. Any of these failing SHALL be reported as a
contract violation with a distinct exit code.

Container image tags, image digests and environment variables SHALL be printed for the operator's
record but SHALL NOT affect the comparator's verdict or exit code. The registered templates are owned
by `sleap-roots-pipeline` and advance independently of this repo's pin by design; treating their image
pins as this repo's invariant would report drift on a correct cluster after every legitimate upstream
template update, and an operator who learns to wave that through will wave a real contract violation
through with it.

The comparator SHALL distinguish "the contract is violated" from "the contract could not be checked"
by exit code, and SHALL NOT report either one as the other. Inability to reach the cluster SHALL be
probed before any per-template assertion, so an unreachable cluster is never reported as a missing or
non-conforming template. The comparator SHALL also refuse to report success when it cannot establish
what to check: discovering no `templateRef`s in the vendored `Workflow`, or fewer than that `Workflow`
declares, SHALL be reported as a failed check rather than as a satisfied contract, on the grounds that
a check whose failure mode is "everything is fine" is worse than no check at all.

The comparator's assertions SHALL be covered by tests that run without cluster access, so the logic is
verified in CI even though the comparator itself requires a live cluster and therefore cannot run
there.

#### Scenario: Newer image pins on a structurally conforming cluster report success

- **WHEN** every registered `WorkflowTemplate` named by the vendored `Workflow` exists, declares the
  referenced inner template, and declares every parameter passed to it
- **AND** one or more of those templates carries a container image tag, image digest or digest
  environment variable that differs from anything this repo records — because the templates were
  legitimately updated upstream since this repo last vendored the `Workflow`
- **THEN** the comparator reports the contract satisfied and exits zero
- **AND** it prints the live image references it observed

#### Scenario: A referenced WorkflowTemplate that is not registered is a contract violation

- **WHEN** the vendored `Workflow` names a `WorkflowTemplate` that does not exist in the namespace
- **THEN** the comparator reports a contract violation and exits with its contract-violation code
- **AND** its output names the missing `WorkflowTemplate`

#### Scenario: A wrong inner template name is a contract violation

- **WHEN** a referenced `WorkflowTemplate` exists but declares no inner template whose `name` equals
  the `template` the vendored `Workflow` references — the defect that cannot fail at submit, because
  the API server accepts the `Workflow` and only the Argo controller resolves `templateRef`s
- **THEN** the comparator reports a contract violation and exits with its contract-violation code
- **AND** its output names both the `WorkflowTemplate` and the inner template that is missing

#### Scenario: A parameter the Workflow passes but the template does not declare is a contract violation

- **WHEN** the vendored `Workflow` passes a parameter to a task whose resolved inner template does not
  declare that name in its `inputs.parameters`
- **THEN** the comparator reports a contract violation and exits with its contract-violation code
- **AND** its output names the undeclared parameter

#### Scenario: An unreachable cluster is reported as a failed check, not as drift

- **WHEN** the cluster cannot be reached at all — VPN down, expired credentials, or wrong kubeconfig
- **THEN** the comparator reports that the check could not run, with an exit code distinct from its
  contract-violation code
- **AND** it does not report any `WorkflowTemplate` as missing or non-conforming

#### Scenario: Discovering nothing to check is a failed check, not a satisfied contract

- **WHEN** parsing the vendored `Workflow` yields no `templateRef`s, or fewer than that `Workflow`
  declares — for example because its structure changed or the file failed to parse as expected
- **THEN** the comparator reports that the check could not run and does not report success
- **AND** its exit code is distinct from the code it uses for a satisfied contract
