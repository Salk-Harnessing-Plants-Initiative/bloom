## 0. Pre-flight

- [ ] 0.1 Record the defect live, before changing anything, so the fix has a baseline to be measured
      against. Run the current comparator and the upstream one against the same namespace minutes
      apart, and paste both verdicts into this file under task 3.1. Expected baseline (observed
      2026-09-21): bloom's reports DRIFT ×5 exit 1, upstream's reports IN SYNC ×5 exit 0.
      ```
      wsl -e bash -c 'export PATH=$HOME/bin:/usr/local/bin:$PATH; export KUBECONFIG=~/.kube/kubeconfig-runai-busch-lab-argo-user.yaml; cd /mnt/c/repos/salk-bloom && python3 scripts/check_registered_templates.py'
      wsl -e bash -c 'cd /mnt/c/repos/sleap-roots-pipeline && bash scripts/check_cluster_drift.sh'
      ```

## 1. RED — tests first, all failing against the current script

Written before any change to `scripts/check_registered_templates.py`. New file
`tests/unit/test_check_registered_templates.py`, following
`test_check_vendored_workflow_drift.py`'s conventions: load the script via
`importlib.util.spec_from_file_location`, and monkeypatch rather than touch the network or a cluster.
Here that means stubbing `subprocess.run` so no test needs `kubectl`, and building fixture
`Workflow`/`WorkflowTemplate` YAML in `tmp_path`.

- [ ] 1.1 Fixtures: a minimal vendored `Workflow` declaring N `templateRef`s (name + inner template +
      passed parameters), and a stub `kubectl` returning caller-supplied `WorkflowTemplate` YAML per
      object name, plus a reachability-probe response. Assert the fixtures themselves are wired by
      writing 1.2 against them first.
- [ ] 1.2 **The bloom#879 regression test, and the centrepiece of this change.** A cluster that
      satisfies the contract but whose templates carry entirely different image tags, image digests
      and digest env vars from anything the repo records SHALL exit 0. Fails today: the current script
      diffs the whole `spec` and reports DRIFT. Derived from the five real diffs observed 2026-09-21
      (`bloomctl:sha-0614889` → `sha-28034f6`, the two `@sha256:` digest pins, and
      `SRP_PREDICT_CONTAINER_DIGEST`/`SRT_TRAITS_CONTAINER_DIGEST`), so the test reproduces the actual
      false positive rather than an invented one.
- [ ] 1.3 A referenced `WorkflowTemplate` absent from the namespace SHALL exit with the
      contract-violation code and name the missing object.
- [ ] 1.4 A `WorkflowTemplate` that exists but declares no inner template matching the referenced
      `template:` SHALL exit with the contract-violation code and name both. This is the defect that
      cannot fail at submit, so it is the assertion the gate exists for.
- [ ] 1.5 A parameter the vendored `Workflow` passes that the resolved inner template does not declare
      in `inputs.parameters` SHALL exit with the contract-violation code and name the parameter.
- [ ] 1.6 An unreachable cluster SHALL exit with the could-not-check code, distinct from the
      contract-violation code, and SHALL NOT report any template as missing. Fails today: `_fetch_live`
      returns `None` on any non-zero `kubectl` exit, so this currently reports `NOT REGISTERED` +
      exit 1 — a fabricated violation. Separate bug from bloom#879; regression-test it here.
- [ ] 1.7 Discovery yielding zero `templateRef`s SHALL exit with the could-not-check code and SHALL
      NOT report success — the `CHECK FAILED` principle carried forward from upstream's comparator.
- [ ] 1.8 Discovery yielding fewer `templateRef`s than the vendored `Workflow` declares SHALL likewise
      exit could-not-check, naming the expected and actual counts.
- [ ] 1.9 The informational image-pin output SHALL be emitted on the success path too, so an operator
      running a clean check still gets the pins for their record.
- [ ] 1.10 Confirm all of 1.2–1.9 fail for the intended reason before writing any implementation —
      paste the failure summary into this file. A test that fails for the wrong reason is not RED.

## 2. GREEN — rewrite the comparator

- [ ] 2.1 Parse the vendored `Workflow` and extract the contract: for each DAG task, the
      `templateRef.name`, `templateRef.template`, and the names under the task's
      `arguments.parameters`. No network, no `SLEAP_ROOTS_PIPELINE_REF` read.
- [ ] 2.2 Probe cluster reachability once (`kubectl get workflowtemplates -n <ns>`) before any
      per-template work; on failure exit could-not-check without asserting anything.
- [ ] 2.3 Assert the expected-count guard from 1.7/1.8 before believing any comparison.
- [ ] 2.4 Per `templateRef`: assert existence, inner-template name, and declared parameters; collect
      all violations rather than stopping at the first, so one run tells an operator everything wrong.
- [ ] 2.5 Print image/digest/env information for every resolved inner template, on every path, with no
      effect on the verdict.
- [ ] 2.6 Delete `RAW_URL`, `REF_FILE`, `_fetch_pinned`, `_strip_server_defaults` and `_DEFAULTED_EMPTY`
      — dead once the blob diff is gone. Keep the 0/1/2 exit-code contract so the archived task 1.1
      invocation and any operator muscle memory still hold.
- [ ] 2.7 Rewrite the module docstring: state that the source of truth is the vendored `Workflow`, that
      image pins are informational and why, and point to upstream's `check_cluster_drift.sh` for the
      staleness question this comparator deliberately does not answer.
- [ ] 2.8 All of 1.2–1.9 green. Paste the passing summary here.

## 3. Live verification — the proof the crying wolf stopped

- [ ] 3.1 Re-run the rewritten comparator against `runai-busch-lab` and record the **observed**
      output verbatim, including exit code. Expected exit 0. Record what was observed even if it
      disagrees with that expectation, and leave this task unticked with the reasoning written out if
      it does.
- [ ] 3.2 Record the 0.1 baseline alongside it, so the before/after sits in one place.
- [ ] 3.3 Negative control: temporarily point a fixture copy of the vendored `Workflow` at a
      nonexistent inner template name and confirm the live comparator exits 1 and names it. A check
      only trusted once seen to fail on a real defect. Use a copy; do not edit the vendored file.

## 4. Correct the records this defect already corrupted

- [ ] 4.1 `openspec/changes/fix-cyl-redelivery-blob-collision/tasks.md` task 9.6 claims
      "verified independently 2026-09-21 via `scripts/check_registered_templates.py`: all five
      registered templates report IN SYNC with the pin, exit 0". Not reproducible — the pin has not
      moved since #866 set it. Rewrite it to record what was actually observed and by which
      comparator, keeping the task's substantive conclusion (the cluster does carry the bumped pins)
      and citing bloom#879. Do not silently delete the claim; show the correction.
- [ ] 4.2 Annotate archived
      `openspec/changes/archive/2026-09-21-vendor-five-task-pipeline-dag/tasks.md` task 1.1 with a
      single line recording that its rationale about pinned-upstream comparison was superseded by
      bloom#879, and that the invocation itself is unchanged. Leave the historical text intact — the
      archive is a record of what was believed at the time.

## 5. Validation and merge

- [ ] 5.1 `openspec validate fix-registered-template-contract-check --strict` passes.
- [ ] 5.2 Confirm no other unarchived change carries a `### Requirement:` heading matching this
      change's delta. Verified at authoring time — no unarchived change touches
      `cyl-pipeline-dispatch` and this delta is ADDED, not MODIFIED — so the archive-ordering hazard
      does not apply. Re-confirm immediately before merge, since other changes land meanwhile.
- [ ] 5.3 `/review-pr` (5-lens adversarial). Fix what it finds, including in this change's own
      reasoning.
- [ ] 5.4 `/pre-merge` green: lint + full suite + OpenSpec validation.
- [ ] 5.5 PR into **`staging`**, bundling proposal + implementation, closing bloom#879.
- [ ] 5.6 Post the live evidence to bloom#879 (authorized): both comparators' output side by side and
      the misattributed 9.6 record.
- [ ] 5.7 Record the two-comparators-opposite-verdicts observation in `sleap-roots-pipeline`
      `docs/bloom-integration/roadmap.md` item A4 — a separate PR in the sibling repo, after the
      observation is real rather than at merge time.
