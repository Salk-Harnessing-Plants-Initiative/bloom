# Tasks — vendor-five-task-pipeline-dag

## 1. Pre-flight (cluster state this change depends on)

- [ ] 1.1 Confirm all five `WorkflowTemplate`s — crucially `sleap-roots-exit-gate-template` — are
      registered in `runai-busch-lab`. **This is the hard ordering constraint**: ship a vendored
      five-task DAG without the gate template registered and every dispatch fails at submit, a
      simultaneous prod + staging outage. Verified present 2026-09-16 (gate age 14h); **re-run
      immediately before merge**, since merging is the deploy and cluster state can change in
      between. RunAI/`kubectl`/`argo` live in **WSL**, not Windows:
      `wsl -e bash -c 'export PATH=$HOME/bin:/usr/local/bin:$PATH; export KUBECONFIG=~/.kube/kubeconfig-runai-busch-lab-argo-user.yaml; kubectl get workflowtemplates -n runai-busch-lab'`
      Record the actual output in the PR, not "verified".

## 2. TDD red — prove the test fails against the current four-task DAG

- [ ] 2.1 In `services/workflows/tests/test_k8s_client.py`, rename
      `test_build_workflow_body_dag_references_all_four_templates_in_order` to
      `..._all_five_templates_in_order`; append `sleap-roots-exit-gate-template` to the expected
      `names_in_order` and add `assert deps[4] == [tasks[3]["name"]]`.
- [ ] 2.2 Add `test_build_workflow_body_exit_gate_is_the_only_leaf` — assert no task lists
      `exit-gate` in its `dependencies`, and that `exit-gate` depends on `write-back`. Docstring
      must say *why*: `assessDAGPhase` takes the Workflow phase from the leaf, so a producer that
      is also a leaf surfaces its own `Failed` phase and defeats `continueOn` entirely.
- [ ] 2.3 Add `test_build_workflow_body_continue_on_is_on_producers_only` — assert
      `continueOn == {"failed": True}` on `images-downloader`/`predictor`/`trait-extractor`, and
      that `write-back` has no `continueOn` key at all (assert absence, not falsiness).
- [ ] 2.4 Add `test_build_workflow_body_exit_gate_receives_producer_exit_codes` — assert the gate's
      `arguments.parameters` are exactly `images-downloader-code`, `predictor-code`,
      `trait-extractor-code`, each valued `{{tasks.<name>.exitCode}}` for its producer.
- [ ] 2.5 Run `cd services/workflows && uv run --frozen --extra test pytest tests/test_k8s_client.py -v`
      and **confirm all four tests fail** against the still-four-task vendored file. Paste the
      failure output into the PR. A test that passes here is testing nothing.

## 3. TDD green — vendor the merged Workflow

- [ ] 3.1 Replace `services/workflows/vendored/sleap-roots-pipeline.yaml` with upstream's file at
      `310aae63c4db4cc1eb608a4d7801031f0061106d`, fetched from
      `https://raw.githubusercontent.com/talmolab/sleap-roots-pipeline/<sha>/sleap-roots-pipeline.yaml`.
      **Byte-exact** — no reformatting, re-indenting, or comment stripping. Expect 10,833 bytes, LF
      line endings, and **no trailing newline** (`.gitattributes` forces `*.yaml text eol=lf`, so
      this is reproducible on Windows and in CI).
- [ ] 3.2 Bump `services/workflows/vendored/SLEAP_ROOTS_PIPELINE_REF` from
      `9df1e52dafb565763de279e9f07e8b52804a1ac3` to `310aae63c4db4cc1eb608a4d7801031f0061106d`.
- [ ] 3.3 Verify the byte count and absence of a trailing newline after the write
      (`wc -c`, `tail -c1 | xxd`) — a stray editor-added newline breaks the drift check in a way
      that reads as upstream drift.
- [ ] 3.4 Re-run the tests from 2.5 and confirm all four now pass.

## 4. Correct the four-template claim wherever it is stated

Grep the **claim**, not the file — sleap-roots-pipeline#62/#67/#68 each shipped a correction that
left the same statement standing elsewhere.

- [ ] 4.1 `services/workflows/k8s_client.py:182` — "the four already-registered WorkflowTemplates"
      → "five". **Change only this one.** `k8s_client.py:23` and `:180` say "applies exactly four
      **overrides**" — a different four, which stays four.
- [ ] 4.2 Confirm by grep that no other four-template claim survives in `services/workflows/`.
      Known non-matches to leave alone: `test_k8s_client.py:425`
      (`..._only_changes_the_four_documented_overrides`), `test_k8s_client.py:614` ("the four
      dispatch-added label keys"), `conftest.py:10`, and every `test_plate_*` hit.
- [ ] 4.3 The spec delta already updates `openspec/specs/cyl-pipeline-dispatch/spec.md`'s normative
      prose (lines 39-42 name all four templates). No separate edit — it lands at archive time.

## 5. Verification

- [ ] 5.1 Full service suite green:
      `cd services/workflows && uv run --frozen --extra test pytest tests/ -v --tb=short` (the
      command CI runs, `pr-checks.yml:191`). Confirm the two DAG tests that compare against the
      vendored file — `..._preserves_dag_structure_from_vendored_file` and
      `..._only_changes_the_four_documented_overrides` — still pass. They follow the vendored file
      automatically, so they are a check that nothing *else* moved, not a DAG-shape guard.
- [ ] 5.2 `python3 scripts/check_vendored_workflow_drift.py` exits 0. **This proves provenance, not
      DAG shape** — it goes green the moment the file and the pin agree, including on a wrongly
      shaped DAG. Do not report it as "the vendoring is done".
- [ ] 5.3 Offline `argo lint` — the real shape gate, and it needs no cluster and no VPN. Copy the
      vendored Workflow plus all five templates at the pinned SHA to a temp dir, delete the
      `  namespace: runai-busch-lab` line from the **copy** (never the real file), then
      `argo lint --offline "$T"/sleap-roots-*.yaml`. Expect `✔ no linting errors found!`, exit 0.
      This is what proves every `templateRef` resolves and the gate's three
      `{{tasks.<name>.exitCode}}` references pass Argo's `validateDAGTaskArgumentDependency`.
      Recipe and WSL invocation: `sleap-roots-pipeline/.claude/skills/runai/SKILL.md` §8.
- [ ] 5.4 `openspec validate vendor-five-task-pipeline-dag --strict` passes.
- [ ] 5.5 `/pre-merge` clean (lint + suite + self-review + OpenSpec validation).

## 6. PR

- [ ] 6.1 Single PR **targeting `staging`** (not `main`), branch `feat/vendor-five-task-dag`,
      bundling the OpenSpec proposal and the implementation. Not a proposal-only PR.
- [ ] 6.2 PR body states the deploy semantics explicitly: merging to `staging` **is** the deploy;
      prod and staging share the `runai-busch-lab` namespace; `staging` → `main` promotion is the
      separate production cutover, and until it happens production keeps dispatching the four-task
      DAG.
- [ ] 6.3 PR body names the known limitations shipping with this — bloom#857, bloom#859,
      bloom#703, sleap-roots-pipeline#70/#71 — so they are not later rediscovered as regressions
      caused by this change.
- [ ] 6.4 Run `/review-pr`.
- [ ] 6.5 Re-run task 1.1 immediately before merge and paste the output.

## 7. Post-merge — blocks archiving

§7.4b of `sleap-roots-pipeline`'s `add-partial-success-exit-gate` change is blocked on this PR:
`cyl_pipeline_runs`/`cyl_pipeline_run_scans` rows are created by Bloom's `POST /workflows/pipeline`
route at enumerate time, so a hand `argo submit` creates nothing for those assertions to attach to,
and dispatching through Bloom before this lands would exercise the old four-task DAG.

**Do not archive this change until 7.1-7.3 are done.** Record what was *observed*, not what was
expected, and record it after the run rather than at merge (bloom#708 task 14.9 precedent).

- [ ] 7.1 Dispatch a real batch through Bloom on staging containing the poison scan `12894751`
      (empirically verified to fail staging: `bloomctl cyl download-for-predict 12894751 <tmp> -p pipeline-staging`
      → "1 of 1 frames failed to download … no sidecar written") plus good scans `12894745` and
      `12894746`. **Record the scan ids and the workflow name in this file** — the dispatch path
      stamps a `ttlStrategy`, so the Workflow object is garbage-collected and its
      `spec.arguments.parameters` is not a durable record of the inputs. That is exactly how the
      2026-09-01 poison-scan run's inputs were lost.
- [ ] 7.2 Assert on the resulting run: `done_count` and `failed_count` are populated with real
      per-scan status (possible only because bloom#774 landed — before it `done_count` was never
      populated by anything), the poison scan has a per-scan `failed` row, and `cyl_trait_sources`
      reflects the two good scans.
- [ ] 7.3 Record the observed run status. Expect **`complete` with `failed_count > 0`**, not
      `partial` — bloom#857 makes `partial` unreachable. If the run reads `complete`, that is the
      documented known limitation, not a failure of this change. Note in the roadmap (item A4, in
      `talmolab/sleap-roots-pipeline` `docs/bloom-integration/roadmap.md`) that the acceptance
      column's `partial` oracle remains unreachable — annotate in place rather than rewriting.
- [ ] 7.4 Only then: `/cleanup-merged` → `openspec archive vendor-five-task-pipeline-dag --yes`,
      after confirming every item above is `- [x]`.
