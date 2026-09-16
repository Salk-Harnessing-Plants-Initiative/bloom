# Tasks — vendor-five-task-pipeline-dag

## 0. Change-coordination pre-flight

- [ ] 0.1 **Archive-ordering hazard.** `openspec/changes/fix-argo-workflow-vendoring/` is complete
      (28/28) but unarchived, and its delta MODIFIES the same `cyl-pipeline-dispatch` requirement
      this change does. This change's MODIFIED block already carries that sibling's text forward
      (the `spec.volumes`/`entrypoint`/`serviceAccountName` paragraph and its volumes scenario), so
      nothing is lost if the sibling archives first — but if **this** change archives first, the
      sibling's block will silently revert "five" to "four" and drop the exit-gate scenario.
      `openspec validate --strict` cannot detect this. Confirm before archiving (task 8.4) that
      `fix-argo-workflow-vendoring` has already been archived; if it has not, archive it first.

## 1. Pre-flight (cluster state this change depends on)

- [ ] 1.1 Confirm the five `WorkflowTemplate`s are registered in `runai-busch-lab`, **and that the
      registered gate declares the inputs this DAG supplies**. A name-only `kubectl get` is not
      sufficient: Argo rejects a submission whose arguments do not satisfy the registered
      template's declared `inputs.parameters`, so a name-only check can pass while every dispatch
      fails at submit. Diff the registered object's `inputs.parameters` against the pinned upstream
      template. RunAI/`kubectl`/`argo` live in **WSL**, not Windows:
      ```
      wsl -e bash -c 'export PATH=$HOME/bin:/usr/local/bin:$PATH; export KUBECONFIG=~/.kube/kubeconfig-runai-busch-lab-argo-user.yaml; kubectl get workflowtemplate sleap-roots-exit-gate-template -n runai-busch-lab -o jsonpath="{.spec.templates[*].inputs.parameters[*].name}"'
      ```
      Expect `images-downloader-code predictor-code trait-extractor-code`. **Re-run immediately
      before merge** (task 7.5) — merging is the deploy, and cluster state can change in between.
      Record the actual output in the PR, not the word "verified".
      *Observed 2026-09-16T17:19Z: all five present; gate `creationTimestamp` 2026-09-16T02:39:01Z.*
- [ ] 1.2 **Upstream PR #75 (`record-section7-results`, OPEN) records the §7 results this proposal
      cites** — 7.2 applied, 7.4a run, 7.5 passed, 7.6 and 7.8 measured. They are **not on
      upstream `main`**, where 7.2/7.5/7.6 still read unchecked, so a reviewer checking `main`
      alone will not find them. Track #75; if it changes materially before this merges, re-check
      the claims in `proposal.md`'s Why and Non-Goals against it.
      Still outstanding upstream even after #75: the roadmap's
      "`argo template list -n runai-busch-lab` returns four as of 2026-09-16" (#75 touches only
      `tasks.md`), and upstream tasks 9.1-9.4. Not blockers for this PR.

## 2. TDD red — prove the tests fail against the current four-task DAG

Write every assertion as an **exact set or exact sequence**. Each weaker form below was
demonstrated, by running it, to miss a real mutation.

- [ ] 2.1 Add a `_dag_tasks(body)` helper that resolves the DAG **through `spec.entrypoint`**, not
      `spec.templates[0]`: assert `entrypoint == "pipeline"`, assert exactly one template carries a
      `dag`, and return its tasks. Indexing `[0]` passes even when a second, gateless template is
      appended and the entrypoint repointed at it.
- [ ] 2.2 Rename `test_build_workflow_body_dag_references_all_four_templates_in_order` to
      `..._all_five_templates_in_order`. Assert the `(templateRef.name, templateRef.template)`
      **pairs** as an exact ordered list for all five tasks — asserting only `name` lets a task
      invoke the wrong inner template out of the right `WorkflowTemplate`. Assert the dependency
      chain, and assert the task-name set equals the five expected names (the existing chain check
      compares the file against its own task names, so nothing pins them).
- [ ] 2.3 Add `test_build_workflow_body_exit_gate_is_the_only_leaf`. Express the property as
      **"exactly one task is depended on by no other, and it is `exit-gate`"**
      (`names - depended_on == {"exit-gate"}`). Do **not** write "no task lists `exit-gate` in its
      dependencies" — that passes vacuously on the four-task file and also passes when a second
      leaf is added. Also assert exactly one root, and that no task uses `depends` (the expression
      form silently overrides `dependencies`). Docstring must say why: `assessDAGPhase` derives the
      Workflow phase from the leaf.
- [ ] 2.4 Add `test_build_workflow_body_continue_on_is_on_producers_only`. Assert as a **set
      equality**: `{n for n, t in by_name.items() if "continueOn" in t}` equals exactly the three
      producers, and each value `== {"failed": True}` (not `.get()` — `{failed, error}` is a
      different contract the vendored file's own note deliberately rejects). A per-task check does
      not catch `continueOn` on `exit-gate`, which makes the terminal leaf continuable and restores
      the original defect at the last hop.
- [ ] 2.5 Add `test_build_workflow_body_exit_gate_receives_producer_exit_codes`. Look the gate up
      non-optionally (`next(t for t in tasks if t["name"] == "exit-gate")`, not an
      iterate-and-skip loop, which would pass vacuously). Assert the three parameter names and
      their `{{tasks.<name>.exitCode}}` values, **and** cross-check that every referenced task name
      exists in the DAG — renaming a producer while updating only the dependency chain otherwise
      leaves a dangling reference that `dag.go` substitutes with `allowUnresolved=true`.
- [ ] 2.6 Extract the shape assertions into a shared `_assert_five_task_gate_dag(body)` helper, and
      add `test_the_dag_shape_assertions_reject_a_gateless_vendored_file`: build a gateless copy of
      the vendored file in `tmp_path`, point `_VENDORED_WORKFLOW_PATH` at it, and assert the helper
      raises. This makes the guard itself guarded — otherwise the ADDED requirement's
      "consistent-but-wrongly-shaped pair is still caught" property has no standing test, only the
      one-time red at 2.7, which evaporates the moment §3 lands. Add a sibling case stripping
      `continueOn` from the producers.
- [ ] 2.7 Run `cd services/workflows && uv run --frozen --extra test pytest tests/test_k8s_client.py -v`.
      Confirm each new test fails **with an `AssertionError` naming the missing invariant, not a
      `KeyError`/`IndexError` from a lookup line** — an erroring test proves the DAG lacks a task,
      not that the invariant is checked, and can mask a bug in the test itself. Paste the assertion
      line for each, alongside the current baseline (48 passed, 1 skipped — the symlink test skips
      on Windows, runs on CI Linux).

## 3. TDD green — vendor the merged Workflow

- [ ] 3.1 Replace `services/workflows/vendored/sleap-roots-pipeline.yaml` with upstream's file at
      `310aae63c4db4cc1eb608a4d7801031f0061106d`, from
      `https://raw.githubusercontent.com/talmolab/sleap-roots-pipeline/<sha>/sleap-roots-pipeline.yaml`.
      **Byte-exact** — no reformatting, re-indenting, or comment stripping. Use `curl -sSL -o` or
      `Invoke-WebRequest -OutFile`; **never** `Invoke-WebRequest | Out-File` or `Set-Content`, which
      add a UTF-8 BOM, CRLF **and** a trailing newline — three simultaneous ways to break the byte
      check. Expect 10,833 bytes, LF, **no trailing newline** (`.gitattributes` forces
      `*.yaml text eol=lf`, so this is reproducible on Windows and in CI).
- [ ] 3.2 Bump `SLEAP_ROOTS_PIPELINE_REF` from `9df1e52dafb565763de279e9f07e8b52804a1ac3` to
      `310aae63c4db4cc1eb608a4d7801031f0061106d`. **Keep its trailing newline** (it has one today;
      the drift script `.strip()`s it). Note this file has no extension so it falls through to
      `* text=auto`, not the `*.yaml` rule — harmless, since the script strips and regex-validates.
- [ ] 3.3 Verify byte count and absence of a trailing newline (`wc -c`, `tail -c1 | xxd`).
- [ ] 3.4 Re-run 2.7's command; confirm every test now passes.
- [ ] 3.5 **Commit §2 and §3 together as one commit.** 2.7's deliverable is pasted output in the PR
      body, not a committed red state, so there is no reason to leave a red commit on the branch.
      3.1 and 3.2 must be in the same commit regardless — the drift job is gated on
      `services/workflows/vendored` changing and fails on any commit where the YAML and the REF
      disagree.

## 4. Correct the four-template claim wherever it is stated

Grep the **claim**, not the file. sleap-roots-pipeline#62 and #67 each shipped a correction that
left the same statement standing elsewhere. (The first draft of this change made the same mistake:
its grep was `--include=*.py` and missed the README.)

- [ ] 4.1 `services/workflows/k8s_client.py:182` — "the four already-registered WorkflowTemplates"
      → "five". **Change only this one** in that file: `:23` and `:180` say "applies exactly four
      **overrides**" — a different four, which stays four.
- [ ] 4.2 `services/workflows/README.md:131-134` — "references the four already-registered
      `WorkflowTemplate`s:" followed by the four-name arrow chain. Append `exit-gate` to the chain
      and change "four" → "five". `README.md:122` ("four overrides") is the legitimate other four
      and stays.
- [ ] 4.3 Re-grep **repo-wide**, not just `services/workflows/`, and record the decision for every
      hit. Known non-matches to leave alone: `test_k8s_client.py:425`
      (`..._only_changes_the_four_documented_overrides`), `test_k8s_client.py:614` ("the four
      dispatch-added label keys"), `conftest.py:10`, every `test_plate_*` hit, and the
      `openspec/changes/fix-argo-workflow-vendoring/` copies (historical, non-normative).
      `vendored/sleap-roots-pipeline.yaml:37` is fixed by the vendoring itself.
      `docs/issues/issue-2-pipeline-trigger.md:235-292` holds a wholly obsolete inline DAG (still
      has `models-downloader`) — out of scope, noted so it is not mistaken for a missed site.
- [ ] 4.4 The spec deltas already carry the normative prose corrections; they land at archive time.

## 5. Verification

- [ ] 5.1 `cd services/workflows && uv run --frozen --extra test pytest tests/ -v --tb=short`
      (`pr-checks.yml:191`). Expect the suite green.
- [ ] 5.2 Also run the drift-check unit tests, since this is the PR that moves the pin:
      `uv run --extra test pytest tests/unit/test_check_vendored_workflow_drift.py tests/unit/test_pr_checks_workflow_drift_check.py`.
- [ ] 5.3 `python3 scripts/check_vendored_workflow_drift.py` exits 0. **This proves provenance, not
      DAG shape** — it goes green the moment the file and the pin agree, including on a wrongly
      shaped DAG. Do not report it as "the vendoring is done".
- [ ] 5.4 Offline `argo lint` — the shape gate that needs no cluster and no VPN:
      ```
      SHA=310aae63c4db4cc1eb608a4d7801031f0061106d; T=$(mktemp -d)
      for f in sleap-roots-pipeline sleap-roots-{images-downloader,predictor,trait-extractor,write-back,exit-gate}-template; do
        curl -sSf "https://raw.githubusercontent.com/talmolab/sleap-roots-pipeline/$SHA/$f.yaml" -o "$T/$f.yaml"; done
      sed -i '/^  namespace: runai-busch-lab$/d' "$T/sleap-roots-pipeline.yaml"   # the COPY, never the real file
      argo lint --offline "$T"/sleap-roots-*.yaml
      ```
      Expect `✔ no linting errors found!`, exit 0. This proves every `templateRef` resolves and the
      gate's three `{{tasks.<name>.exitCode}}` references pass `validateDAGTaskArgumentDependency`.
      It does **not** evaluate the gate's `{0,3}` allowlist — that lives in the un-vendored template
      and is only ever exercised live. `argo` is WSL-only; see
      `sleap-roots-pipeline/.claude/skills/runai/SKILL.md` §1a and §8.
- [ ] 5.5 `openspec validate vendor-five-task-pipeline-dag --strict` passes.
- [ ] 5.6 `/pre-merge` clean.

## 6. Follow-up issues to file (not fixed here)

- [ ] 6.1 **Prod-dispatched Workflows mount staging credentials.** The vendored file hardcodes
      `secretName: genericsecret-bloom-staging-pipeline-credentials`, and `build_workflow_body`'s
      four overrides do not include it — prod and staging share `runai-busch-lab`, separated only
      by a metadata label. So a production-dispatched run's write-back pod would authenticate
      against **staging** Supabase: its `cyl_pipeline_run_scans` rows would never match, and its
      envelopes would resolve `image_ids` against staging's `cyl_images`. Pre-existing and not
      triggered by this change, but `staging → main` promotion is what makes prod runs execute
      through to write-back. **File before merge; it must block that promotion.**
- [ ] 6.2 **Run identity is absent from every scientist-facing read path.** `pipeline_run_id` is
      NULL on pipeline-written sources, so run-pinned reads resolve to an empty set rather than an
      error. A small migration (`nullif` on the existing `coalesce`, falling back to
      `p_argo_workflow_name`) would close it. File; needed before `staging → main`, not before this
      merge. Related: bloom#703.

## 7. PR

- [ ] 7.1 Single PR **targeting `staging`** (not `main`), branch `feat/vendor-five-task-dag`,
      bundling the OpenSpec proposal and the implementation.
- [ ] 7.2 PR body states the deploy semantics: merging to `staging` **is** the deploy; prod and
      staging share `runai-busch-lab`; `staging` → `main` is the separate production cutover.
- [ ] 7.3 PR body names the known limitations shipping with this, so none is later rediscovered as
      a regression caused by it: bloom#857 (`complete` with `failed_count > 0`), bloom#859 +
      sleap-roots-pipeline#71/#63 (the manifest latch — note it can also produce a **`failed` run
      that wrote correct data**, the more dangerous direction for an automated consumer that
      re-dispatches on `failed`), **sleap-roots-pipeline#76** (re-delivery is idempotent on the
      skip path but fails at write-back on the recompute path — so clearing or losing a
      `predictions/` directory while Bloom still holds blobs for those keys breaks the next run
      over those scans), bloom#703 (run attribution), sleap-roots-pipeline#70,
      **sleap-roots-pipeline#72** (the gate image is tag-pinned with `IfNotPresent`, and the gate is
      the only leaf, so anything stopping that pod fails or hangs *every* workflow),
      **sleap-roots-predict#44** (a raw-forwarded manifest misattributes predict failures to
      trait-extraction), and **task 6.1's credential issue**.
- [ ] 7.4 Run `/review-pr`.
- [ ] 7.5 Re-run task 1.1 immediately before merge and paste the output.
- [ ] 7.6 **Rollback plan, in the PR body.** If dispatch begins failing after merge, revert this PR
      on `staging` (restoring the four-task file and the old pin) and redeploy — then reconcile the
      affected batches by hand. A failed submission marks every scan row in the batch `failed` and
      dead-letters the queue message, with requeue out of scope, so those rows do **not** recover on
      their own once the cluster is healthy.

## 8. Post-merge — blocks archiving

Upstream **§7.4** (the poison-scan scenario) is blocked on this PR: `cyl_pipeline_runs`/
`cyl_pipeline_run_scans` rows are created by Bloom's `POST /workflows/pipeline` route at enumerate
time, so a hand `argo submit` creates nothing for those assertions to attach to, and dispatching
through Bloom before this lands would exercise the old four-task DAG.

**Do not archive until 8.1-8.3 are done.** Record what was *observed*, not what was expected, and
record it after the run rather than at merge (bloom#708 task 14.9 precedent).

- [ ] 8.1 **Capture pre-dispatch state — without this the run proves nothing.**
      (a) Assert `scan_12894751` is **not** already staged under the shared `a4_poc/input` path: a
      staged sidecar makes `stage_one_scan` return `skipped`, which counts as usable and exits `0`,
      so the poison scan silently stops being poison.
      (b) Record existing `source_id` and `created_at` for `12894745`/`12894746`. `idempotency_key`
      excludes volatile provenance, so a re-run with the same models/params re-delivers the same key
      and the RPC short-circuits to a no-op — "`cyl_trait_sources` reflects the two good scans" is
      otherwise satisfiable entirely by rows a *previous* run wrote.
      (c) Copy `run_manifest.json` from all three `a4_poc` directories, so an already-armed latch is
      distinguishable from a failure this run caused.
- [ ] 8.2 Dispatch through Bloom on staging over the shared `a4_poc` paths: poison scan `12894751`
      (verified to fail: `bloomctl cyl download-for-predict 12894751 <tmp> -p pipeline-staging`
      → "1 of 1 frames failed to download … no sidecar written") plus `12894745` and `12894746`.
      **Record the scan ids and workflow name here** — the dispatch path stamps a `ttlStrategy`, so
      the Workflow object is garbage-collected and is not a durable record of its own inputs. That
      is exactly how the 2026-09-01 run's inputs were lost.
      *On sleap-roots-pipeline#76:* not a hazard for this run. Re-delivery is idempotent on the
      **skip** path and broken only on the **recompute** path, and on the persistent `a4_poc` paths
      predict skips — `srp-t77-redeliver-t82vr` re-delivered this exact shared batch with all 24
      `.slp` blobs byte-identical and write-back `Succeeded`. #76 needs local artifacts to have gone
      missing while Bloom still holds blobs for the same key. If this run *does* fail at write-back
      with "refusing to overwrite", that is #76 and not a #56 regression — 8.1(c)'s pre-state is
      what tells the two apart.
- [ ] 8.3 **Capture the exit codes, not only the DB state.** Before TTL GC, record each producer
      node's `exitCode` and the gate's decision
      (`kubectl get wf <name> -n runai-busch-lab -o jsonpath=...`). Expect `{3,0,0}`. For a change
      whose one-line summary is "exit code 3 is now consumed", a DB-only oracle never observes an
      exit code at all — and a gate miswired to read `images-downloader.exitCode` three times would
      pass a DB-only check cleanly. Then assert: `done_count`/`failed_count` populated from real
      per-scan status (possible only because PR #774 landed), a per-scan `failed` row for the poison
      scan, and **changed** `source_id`/`created_at` for the good scans versus 8.1(b).
- [ ] 8.4 **Negative control.** Dispatch a second batch through Bloom that drives a producer to an
      exit code outside `{0,3}` (a crash, not per-scan isolation). Upstream §7.5 proved this
      hand-submitted (`srp-t75-crash-4qd66`, gate `{1,1,1}` → `Failed`), but it has never run
      through Bloom's dispatch route. Assert Workflow `Failed` and run status `failed`. Without it,
      a gate that always exits `0` passes everything above. Note §7.5 used
      `scan-ids=not-an-int`, which Bloom's trigger route may reject before dispatch — if no such
      case can be constructed from the dispatch path, record *why* here rather than leaving it
      unsaid.
- [ ] 8.5 Record the observed run status. Expect **`complete` with `failed_count > 0`** — that is
      the documented bloom#857 behaviour, now written into the `cyl-pipeline-runs` and
      `cyl-pipeline-status-polling` deltas, not a failure of this change. If it reads `failed`,
      check 8.1(c) first: an already-armed manifest latch produces a `failed` run that nonetheless
      wrote correct data.
- [ ] 8.6 Close **bloom#772** with the observed evidence. PR #830 deliberately used "Related to",
      not "Fixes", because the CLI change alone did not fix the live symptom; 8.2's poison-scan run
      is that symptom's actual fix. Comment on **sleap-roots-pipeline#56** with the result too — it
      auto-closed on PR #60's merge with its own §7 acceptance criteria unrun, a live instance of
      the bloom#780 pattern.
- [ ] 8.7 Only then: confirm task 0.1's archive ordering, verify every item above is `- [x]`, and
      run `/cleanup-merged` → `openspec archive vendor-five-task-pipeline-dag --yes`.
