# Tasks — vendor-five-task-pipeline-dag

## 0. Change-coordination pre-flight

- [x] 0.1 **Archive-ordering hazard — TWO unarchived siblings MODIFY requirements this change also
      MODIFIES.** OpenSpec's MODIFIED replaces the whole requirement block, so whichever archives
      second silently destroys the other's edits, and `openspec validate --strict` cannot see it.
      1. `fix-argo-workflow-vendoring` (28/28, unarchived) — same `cyl-pipeline-dispatch`
         requirement. This change's MODIFIED block already carries its text forward (the
         `spec.volumes`/`entrypoint`/`serviceAccountName` paragraph and its volumes scenario).
      2. `fix-cyl-pipeline-run-scan-status` (64/72, unarchived, **but its migrations are already on
         `staging`**) — same `cyl_pipeline_runs table` requirement. This change's MODIFIED block now
         carries its text forward too (the poller-maintained `done_count`/`failed_count` prose and
         its `done_count and failed_count reflect real per-scan outcomes` scenario). This one has a
         **semantic** dependency, not just a textual one: this change's central instruction —
         "consumers SHALL treat `failed_count`/`done_count` as the authoritative signal" — is only
         *true* because that sibling's poller work exists.
      Confirm before archiving (task 8.7) that both have already been archived; if either has not,
      archive it first.

      **RESOLVED 2026-09-21, one each way — read this before archiving the sibling.**
      1. `fix-argo-workflow-vendoring` — **archived first**, as required (PR #878,
         `2026-09-17-fix-argo-workflow-vendoring`). Verified after that archive: the live
         `cyl-pipeline-dispatch` spec gained its `spec.volumes` clause and still read "four
         already-registered", which this change then takes to five. Correct order, no loss.
      2. `fix-cyl-pipeline-run-scan-status` — **still unarchived and cannot be yet**: its own 7.1
         (`database.types.ts` regen) is blocked on a fully-migrated environment, its 8.2
         (full-success batch) has not been run, and 15.1-15.3 remain. Archiving this change first
         is nonetheless safe *for content*, because this change's `cyl_pipeline_runs` MODIFIED
         block already carries that sibling's text forward verbatim — the poller-maintained
         `done_count`/`failed_count` prose and its "reflect real per-scan outcomes" scenario.
         ⚠️ **But the hazard now points the other way.** That sibling still holds a `MODIFIED` on
         the same requirement whose text predates this change. Archiving it as-is would replace
         the block wholesale and silently drop this change's four-bounds paragraph and its three
         new scenarios. **Its delta must be brought up to this change's superset text before it
         archives.** `openspec validate --strict` cannot see this; nothing in CI can. This is the
         same pattern already hit once with `fix-cyl-redelivery-blob-collision` on
         `cyl-ingest-cli` — see `2026-09-18-fix-cyl-redelivery-status-fallback/tasks.md` 7.4.
      *Update 2026-09-16: the third pipeline change, `fix-cyl-batch-download-partial-exit-code`,
      **was archived** on `staging` in #855 (`7eeeeb2c`), so
      `openspec/specs/cyl-batch-download-for-predict/spec.md:71` now normatively says the command
      "SHALL exit `3` if any scan in the batch failed". That removes the gap this note used to flag
      — the exit code the vendored gate's `{0,3}` allowlist depends on is specified in the live
      spec, not only in an unarchived delta. The two colliding siblings above are unaffected and
      still gate archiving.*

## 1. Pre-flight (cluster state this change depends on)

- [x] 1.1 **Verify the registered templates by diffing them against the pin — this is the only guard
      that exists.** A bad `templateRef` does **not** fail at submit: measured 2026-09-16 by
      server-side dry-run, the K8s API server *accepts* a Workflow naming a nonexistent
      `WorkflowTemplate` (`created (server dry run)`, exit 0), because resolution is the Argo
      controller's job and this worker POSTs to the raw K8s API, not the Argo Server. So nothing
      downstream catches a registration defect until the run has already failed.
      **Run the committed comparator, do not eyeball a `kubectl` diff:**
      ```
      wsl -e bash -c 'export PATH=$HOME/bin:/usr/local/bin:$PATH; export KUBECONFIG=~/.kube/kubeconfig-runai-busch-lab-argo-user.yaml; cd /mnt/c/repos/salk-bloom && python3 scripts/check_registered_templates.py'
      ```
      It compares all five registered objects against the pinned upstream and prints each image pin.
      It exists because prose is not a reproducible gate: the API server defaults empty keys onto
      every stored object **and canonicalises CPU quantities**, so a naive
      `pinned["spec"] == live["spec"]` reports DRIFT on templates that are perfectly in sync.
      Measured, the normalisations needed at `spec` level are **six**, not the four an earlier draft
      of this task listed: `arguments`/`inputs`/`outputs`/`metadata` when empty, `container.name`
      when `""`, and `cpu: "0.5"` → `"500m"` (on `images-downloader` and `write-back`). `exit-gate`
      escapes the `inputs` default only because it declares three parameters — which is exactly why
      it was the one template an under-normalised check made look clean. An operator who learns to
      wave four false DRIFTs through will wave a real one through with them, and the defect that
      matters most here — a wrong inner `template:` name — cannot fail at submit.
      **Record all three bloomctl image references** (gate, images-downloader, write-back); the
      script prints them. The gate is pinned to `bloomctl:sha-0614889` (PR #774);
      `images-downloader` must be running a bloomctl containing **PR #830** or exit `3` is never
      emitted at all — in which case every check in this change passes, the deploy looks clean, and
      partial failures still fail whole runs.

      **RUN 2026-09-16. All five IN SYNC with the pin, exit 0. No real drift.** Recorded because
      "verified" is not a result:
      | template | image | notable |
      |---|---|---|
      | `images-downloader` | `bloomctl:sha-0614889` | `retryStrategy{limit:2, Always}` |
      | `predictor` | `sleap-roots-predict:sha-e025e309…` | `retryStrategy{limit:3, backoff 2m×2}` |
      | `trait-extractor` | `sleap-roots-trait-extractor:sha-689cffb` | `retryStrategy{limit:2, Always}` |
      | `write-back` | `bloomctl:sha-0614889` | `retryStrategy{limit:2, Always}` |
      | `exit-gate` | `bloomctl:sha-0614889` | inputs exactly `images-downloader-code`, `predictor-code`, `trait-extractor-code`; `timeout: 600s`; `retryStrategy{limit:2, backoff 30s×2}` |

      **The exit-3 precondition holds.** `images-downloader` runs `bloomctl:sha-0614889` =
      `06148896` (PR #774, merged 2026-09-15T17:16:54Z). PR #830's merge commit `623414f7`
      (2026-09-15T16:38:00Z) **is an ancestor** of it — confirmed by `git merge-base --is-ancestor`
      and by reading the file at that commit, where `download_for_predict.py:646` is
      `ctx.exit(0 if result.ok else 3)`. So the deployed image really does emit `3`, and this change
      is not inert.

      **Re-run immediately before merge** (task 7.5). Record actual output, not the word "verified".
      *Pre-merge re-run 2026-09-16T23:00:00Z: all five IN SYNC, exit 0, image pins unchanged from
      the 17:19Z run. No cluster drift in between.*
- [x] 1.2 **RESOLVED: upstream PR #75 merged 2026-09-16T18:50:18Z (`561d0571`).** It records the §7
      results this change cites — 7.2 applied, 7.4a run, 7.5 passed, 7.6/7.7/7.8 measured — plus the
      §9 roadmap closeouts, and it also corrected the roadmap's "`argo template list` returns four".
      On upstream `main` today 7.2, 7.3, 7.5, 7.6, 7.7, 7.8 and 9.1-9.4 all read `[x]`; only 7.4
      (split, its Bloom-side half blocked on this PR), 8.1 (this PR) and 8.2 remain open. So the
      earlier advice — "check #75's branch, not `main`" — has inverted: `main` is now the source of
      truth, and `proposal.md` has been updated accordingly. This is the case this task anticipated
      ("if it changes materially before this merges, re-check") actually firing.

## 2. TDD red — prove the tests fail against the current four-task DAG

Write every assertion as an **exact set or exact sequence**. Each weaker form below was
demonstrated, by running it, to miss a real mutation.

- [x] 2.1 Add a `_dag_tasks(body)` helper that resolves the DAG **through `spec.entrypoint`**, not
      `spec.templates[0]`: assert `entrypoint == "pipeline"`, assert exactly one template carries a
      `dag`, **assert that template's own `name` equals the entrypoint**, and return its tasks.
      Indexing `[0]` passes even when a second, gateless template is appended and the entrypoint
      repointed at it; and without the name check, renaming the `pipeline` template while leaving
      `spec.entrypoint: pipeline` passes too — a mutation that makes Argo reject **every** dispatch
      with "entrypoint pipeline not found".
- [x] 2.2 Rename `test_build_workflow_body_dag_references_all_four_templates_in_order` to
      `..._all_five_templates_in_order`. Assert an exact ordered list of
      **`(task name, templateRef.name, templateRef.template)` triples** — all three together, not
      the pair plus a separate name-set. A pair-only assertion lets `templateRef.template` invoke
      the wrong inner template; a name-*set* assertion lets two task names be swapped while the
      `templateRef` order stays put, which silently re-attributes `predictor-code` to
      trait-extraction's exit code. Also assert `set(templateRef) == {"name", "template"}` per task,
      so a missing `template:` key is an `AssertionError` rather than a `KeyError`. Keep the
      dependency-chain assertion (`deps[0] is None or deps[0] == []` is correct — Argo treats an
      empty list and an absent key identically).
- [x] 2.3 Add `test_build_workflow_body_exit_gate_is_the_only_leaf`. Express the property as
      **"exactly one task is depended on by no other, and it is `exit-gate`"**
      (`names - depended_on == {"exit-gate"}`). Do **not** write "no task lists `exit-gate` in its
      dependencies" — that passes vacuously on the four-task file and also passes when a second
      leaf is added. Also assert exactly one root, and that no task uses `depends` (the expression
      form silently overrides `dependencies`). Docstring must say why: `assessDAGPhase` derives the
      Workflow phase from the leaf.
- [x] 2.4 Add `test_build_workflow_body_continue_on_is_on_producers_only`. Assert as a **set
      equality**: `{n for n, t in by_name.items() if "continueOn" in t}` equals exactly the three
      producers, and each value `== {"failed": True}` (not `.get()` — `{failed, error}` is a
      different contract the vendored file's own note deliberately rejects). A per-task check does
      not catch `continueOn` on `exit-gate`, which makes the terminal leaf continuable and restores
      the original defect at the last hop.
- [x] 2.5 Add `test_build_workflow_body_exit_gate_receives_producer_exit_codes`. Look the gate up
      **as an assertion, not an exception**:
      ```python
      gates = [t for t in tasks if t.get("name") == "exit-gate"]
      assert len(gates) == 1, f"exit-gate tasks found: {len(gates)}"
      ```
      Not `next(...)` — that raises `StopIteration` on the four-task file, which 2.9 explicitly
      forbids, and it would also miss a *duplicated* `exit-gate` task. Assert the three parameter
      names and their `{{tasks.<name>.exitCode}}` values, **and** cross-check that every referenced
      task name exists in the DAG. `argo lint` does reject a dangling reference (measured, v3.6.5:
      renaming a producer and leaving the gate's reference stale gives `failed to resolve`, exit 1)
      — but this repo does not run it in CI and the raw Kubernetes API accepts the submission
      regardless, so this assertion is the only automatic guard.
- [x] 2.6 Add the assertions §2 currently makes about **nothing**, pinned to **literals** rather
      than to the vendored file — comparing the built body against the file it was built from is
      what makes the four existing "preserves…" tests tautological.
      (a) `spec.serviceAccountName == "bloom-workflow"` (a change to `default` makes every step fail
      with `workflowtaskresults.argoproj.io is forbidden`) and
      `metadata.generateName == "sleap-roots-pipeline-"`.
      (b) The whole `spec.volumes` list as a literal, including each `hostPath.type == "Directory"`
      and the `bloom-credentials` `secretName`. This is bloom#737's exact blast radius and §2
      asserts nothing about it today; `DirectoryOrCreate` is the mutation the vendored file's own
      comment calls out by name — a down NFS mount then writes to node-local disk and the pipeline
      reports success with vanished output.
      (c) No task carries a key outside `{name, templateRef, dependencies, continueOn, arguments}`,
      and `arguments` appears on `exit-gate` only. Without this, adding `when:` to the gate makes it
      `Omitted` — which `assessDAGPhase` treats as `Succeeded` — reintroducing the exact defect the
      gate exists to prevent while every other assertion stays green.
      (d) No unexpected top-level `spec` keys (guards `shutdown`, `onExit`, `parallelism`,
      `nodeSelector`, `activeDeadlineSeconds`).
- [x] 2.7 Extract the shape assertions into a shared `_assert_five_task_gate_dag(body)` helper and
      add `test_the_dag_shape_assertions_reject_a_gateless_vendored_file`: build a mutated copy in
      `tmp_path`, point `_VENDORED_WORKFLOW_PATH` at it, and assert the helper raises.
      **Use `pytest.raises(AssertionError, match=...)`, not a bare `pytest.raises`.** A bare one is
      satisfied by whichever assertion fires first — on a gateless file that is 2.2's triple-list
      check, never the leaf check — so the test passes even if the leaf assertion is deleted
      entirely. Three sibling cases, each `match`-anchored to the assertion it targets:
      (i) gateless (four tasks); (ii) `continueOn` stripped from the producers; (iii) **five tasks
      with the gate re-pointed at `trait-extractor`**, so `write-back` and `exit-gate` are both
      leaves — this is the only one that actually exercises the only-leaf assertion.
- [x] 2.8 Fix the locale-decode divergence while re-vendoring this file:
      `k8s_client.py:153` calls `_VENDORED_WORKFLOW_PATH.read_text()` with no `encoding=`, so it
      decodes with the platform locale (`cp1252` on Windows, UTF-8 in the container). Measured: 42
      non-ASCII bytes in the file, `read_text() != raw.decode("utf-8")` locally. Harmless today only
      because all non-ASCII is in comments that `safe_load` drops. Pass `encoding="utf-8"` there and
      in the `vendored_workflow` fixture (`test_k8s_client.py:26`).
- [x] 2.9 Run `cd services/workflows && uv run --frozen --extra test pytest tests/test_k8s_client.py -v`.
      Confirm each new test fails **with an `AssertionError` naming the missing invariant, not a
      `KeyError`/`IndexError`/`StopIteration` from a lookup line** — an erroring test proves the DAG
      lacks a task, not that the invariant is checked, and can mask a bug in the test itself. Paste
      the assertion line for each, alongside the current baseline (48 passed, 1 skipped for
      `tests/test_k8s_client.py`; 648 passed, 1 skipped for the full `tests/` — the symlink test
      skips on Windows and runs on CI Linux).

## 3. TDD green — vendor the merged Workflow

- [x] 3.1 Replace `services/workflows/vendored/sleap-roots-pipeline.yaml` with upstream's file at
      `310aae63c4db4cc1eb608a4d7801031f0061106d`, from
      `https://raw.githubusercontent.com/talmolab/sleap-roots-pipeline/<sha>/sleap-roots-pipeline.yaml`.
      **Byte-exact** — no reformatting, re-indenting, or comment stripping. Use `curl -sSL -o` or
      `Invoke-WebRequest -OutFile`; **never** `Invoke-WebRequest | Out-File` or `Set-Content`, which
      add a UTF-8 BOM, CRLF **and** a trailing newline — three simultaneous ways to break the byte
      check. Expect 10,833 bytes, LF, **no trailing newline** (`.gitattributes` forces
      `*.yaml text eol=lf`, so this is reproducible on Windows and in CI).
- [x] 3.2 Bump `SLEAP_ROOTS_PIPELINE_REF` from `9df1e52dafb565763de279e9f07e8b52804a1ac3` to
      `310aae63c4db4cc1eb608a4d7801031f0061106d`. **Keep its trailing newline** (it has one today;
      the drift script `.strip()`s it). Note this file has no extension so it falls through to
      `* text=auto`, not the `*.yaml` rule — harmless, since the script strips and regex-validates.
- [x] 3.3 Verify byte count and absence of a trailing newline (`wc -c`, `tail -c1 | xxd`).
- [x] 3.4 Re-run 2.9's command; confirm every test now passes.
- [x] 3.5 **Commit §2 and §3 together as one commit.** 2.9's deliverable is pasted output in the PR
      body, not a committed red state, so there is no reason to leave a red commit on the branch.
      3.1 and 3.2 must be in the same commit regardless — the drift job is gated on
      `services/workflows/vendored` changing and fails on any commit where the YAML and the REF
      disagree.

## 4. Correct the four-template claim wherever it is stated

Grep the **claim**, not the file. sleap-roots-pipeline#62 and #67 each shipped a correction that
left the same statement standing elsewhere. (The first draft of this change made the same mistake:
its grep was `--include=*.py` and missed the README.)

- [x] 4.1 `services/workflows/k8s_client.py:182` — "the four already-registered WorkflowTemplates"
      → "five". **Change only this one** in that file: `:23` and `:180` say "applies exactly four
      **overrides**" — a different four, which stays four.
- [x] 4.2 `services/workflows/README.md:131-134` — "references the four already-registered
      `WorkflowTemplate`s:" followed by the four-name arrow chain. Append `exit-gate` to the chain
      and change "four" → "five". `README.md:122` ("four overrides") is the legitimate other four
      and stays.
- [x] 4.3 Re-grep **repo-wide**, not just `services/workflows/`, and record the decision for every
      hit. Known non-matches to leave alone: `test_k8s_client.py:425`
      (`..._only_changes_the_four_documented_overrides`), `test_k8s_client.py:614` ("the four
      dispatch-added label keys"), `conftest.py:10`, every `test_plate_*` hit, and the
      `openspec/changes/fix-argo-workflow-vendoring/` copies (historical, non-normative).
      `vendored/sleap-roots-pipeline.yaml:37` is fixed by the vendoring itself.
      `openspec/changes/archive/2026-08-17-add-cyl-pipeline-dispatch/` (2 hits, archived history).
      `docs/issues/issue-2-pipeline-trigger.md:235-292` holds a wholly obsolete inline DAG (still
      has `models-downloader`) — out of scope, noted so it is not mistaken for a missed site.
- [x] 4.6 **Add tests for the two rollup-side ADDED scenarios.** The `cyl-pipeline-runs` and
      `cyl-pipeline-status-polling` deltas add four scenarios and this PR touches only
      `test_k8s_client.py`. The two rollup halves are unit-testable today against `sweep_once` in
      `tests/test_status_poller.py`: `Succeeded` phase with a `failed` scan row → `'complete'` with
      `failed_count > 0`, and `Failed` phase with `written` scan rows → `'failed'` with
      `done_count > 0`. The nearest existing test reaches `'partial'` via a *dispatch*-failed scan,
      which does not exercise either. The other two scenarios are cross-repo and stay live-only
      (§8) — record that explicitly rather than leaving them silently unasserted.
- [x] 4.4 **DECIDED: delete `..._preserves_dag_structure_from_vendored_file`, keep
      `..._only_changes_the_four_documented_overrides`.** The first compared the built body's DAG
      against the same file it was built from — 0 of 58 mutations caught — and its content is
      strictly subsumed by the second's whole-body deepcopy diff. The second is *not* tautological
      for its actual purpose: it tests a property of the **code** (that `build_workflow_body`
      modifies nothing beyond its four overrides), not of the file. A comment at the deletion site
      records the reasoning and notes that the nested-`steps` coverage it provided by accident (a
      `KeyError` on `templates[0]`) is now a deliberate assertion in `_dag_tasks`.
      Original task text: **Decide what to do with the two tautological DAG tests, and record it.**
      `test_build_workflow_body_preserves_dag_structure_from_vendored_file` and
      `..._only_changes_the_four_documented_overrides` compare `build_workflow_body`'s output
      against the *same file it was built from*, so they cannot fail on any vendored-file mutation —
      measured: of 58 mutations the existing suite caught 5, every one by a `KeyError` on the
      fixture side rather than an assertion. They read like DAG guards and contribute no mutation
      coverage. Either delete them or rebase them onto the new `_dag_tasks` helper. Note one of them
      is currently the only thing catching a nested-`steps` template, by `KeyError` on
      `templates[0]` — 2.1's entrypoint resolution replaces that accidental coverage with a real
      assertion, so deleting is safe once 2.1 lands.
- [x] 4.5 The spec deltas already carry the normative prose corrections; they land at archive time.

## 5. Verification

- [x] 5.1 `cd services/workflows && uv run --frozen --extra test pytest tests/ -v --tb=short`
      (`pr-checks.yml:191`). Expect the suite green.
- [x] 5.2 Also run the drift-check unit tests, since this is the PR that moves the pin:
      `uv run --extra test pytest tests/unit/test_check_vendored_workflow_drift.py tests/unit/test_pr_checks_workflow_drift_check.py`.
- [x] 5.3 `python3 scripts/check_vendored_workflow_drift.py` exits 0. **This proves provenance, not
      DAG shape** — it goes green the moment the file and the pin agree, including on a wrongly
      shaped DAG. Do not report it as "the vendoring is done".
- [x] 5.4 Offline `argo lint` — the shape gate that needs no cluster and no VPN:
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
- [x] 5.5 `openspec validate vendor-five-task-pipeline-dag --strict` passes.
- [x] 5.6 `/pre-merge` clean — CI green on the merge commit; PR #866 merged 2026-09-16T23:43:37Z.

## 6. Follow-up issues to file (not fixed here)

- [x] 6.1 **FILED as bloom#863.** Prod-dispatched Workflows mount staging credentials. The vendored file hardcodes
      `secretName: genericsecret-bloom-staging-pipeline-credentials`, and `build_workflow_body`'s
      four overrides do not include it — prod and staging share `runai-busch-lab`, separated only
      by a metadata label. So a production-dispatched run's write-back pod would authenticate
      against **staging** Supabase: its `cyl_pipeline_run_scans` rows would never match, and its
      envelopes would resolve `image_ids` against staging's `cyl_images`. Pre-existing and not
      triggered by this change, but `staging → main` promotion is what makes prod runs execute
      through to write-back. **File before merge; it must block that promotion.**
- [x] 6.2 **FILED as bloom#864.** Run identity is absent from every scientist-facing read path. `pipeline_run_id` is
      NULL on pipeline-written sources, so run-pinned reads resolve to an empty set rather than an
      error. A small migration (`nullif` on the existing `coalesce`, falling back to
      `p_argo_workflow_name`) would close it. File; needed before `staging → main`, not before this
      merge. Related: bloom#703.
- [x] 6.3 **Note, not a blocker: the per-scan status machinery is staging-only until promotion, and
      promotes with this change.** `20260912110000_add_cyl_writeback_run_scan_status.sql` and
      `20260912111000_add_cyl_pipeline_run_scan_counts.sql` are on `origin/staging` and not on
      `origin/main` (verified — `main` carries no September 2026 migrations at all). Until they
      promote, this change's central contract — "read `failed_count`, not `status`" — is
      unsatisfiable in production, because nothing maintains those counters. But they sit on the
      same branch as this change, so a `staging → main` promotion carries them together; there is no
      ordering in which the DAG reaches production without them. **Do not file this as a separate
      blocker.** What it does mean: the promotion is large (`main` is many migrations behind), so
      treat migration ordering as part of that cutover's own review, not this PR's.
- [x] 6.5 **FILED as bloom#867.** `'complete'` has no floor — a totally-failed batch reports success.
      Note a code fix alone does not reach the cluster: `images-downloader` runs
      `bloomctl:sha-0614889`, so it needs a new image plus an upstream template re-pin. Consumer
      guidance is therefore the near-term mitigation, and it is now in
      `services/workflows/README.md` ("Reading a run's outcome: use the counts, not `status`")
      because a run-status UI is being built this week. `BatchResult.ok`
      is `all(status in ("ok","skipped"))`, so 1-of-100 failed and 100-of-100 failed both exit `3`;
      the gate accepts both and the run reads `'complete'` with `done_count = 0`. Common-mode
      failures (NFS down, revoked credential) fail every scan identically and now go green. Either
      widen bloom#857's text or add a floor. Not a merge blocker — the failure is visible in
      `failed_count` — but it is the sharpest edge of the new semantics.
- [x] 6.6 **FILED as bloom#872.** An all-`Succeeded` run with a TTL-GC'd sibling never gets its counters written.
      `status_poller.py:346-353` withholds `'complete'` when any workflow 404'd this cycle and
      `continue`s, skipping reconciliation *and* the status write. A GC'd workflow 404s forever, so
      the run stalls permanently with `done_count`/`failed_count` unwritten — which is exactly the
      signal this change's specs tell consumers to trust. Pre-existing (accepted in
      `fix-cyl-pipeline-run-scan-status`'s design), but this change routes far more runs into it:
      batches that used to end `Failed` and settle now end `Succeeded`.
- [x] 6.7 **DONE — posted to bloom#863** as a scope correction with acceptance criteria. The shared-path and `scan_key` collision: Giving prod its own
      credential is necessary but not sufficient: `scan_key_for()` is `f"scan_{scan_id}"`, a
      DB-local integer with independent sequences per environment, and `scan_is_already_staged`
      compares only `scan_key`. Once prod has real credentials, a staging run that already staged
      `scan_42` makes the prod run for prod's scan 42 **skip**, and prod ingests traits computed
      from staging's images. Fixing #863 alone converts a dormant misdirection into live
      cross-environment data corruption.
- [x] 6.4 **Production is already exposed, now, independent of this PR.** The new producer image
      pins are live on the shared `runai-busch-lab` templates while prod still dispatches the
      four-task DAG with no `continueOn`. If those images moved partial success from exit 0 to exit
      3, prod batches that used to go green now go red. Check whether prod has dispatched since
      2026-09-16 and record the answer; if the window stays open, say who is watching it.

## 7. PR

- [x] 7.1 Single PR **targeting `staging`** (not `main`), branch `feat/vendor-five-task-dag`,
      bundling the OpenSpec proposal and the implementation.
- [x] 7.2 PR body states the deploy semantics: merging to `staging` **is** the deploy; prod and
      staging share `runai-busch-lab`; `staging` → `main` is the separate production cutover.
- [x] 7.3 PR body names the known limitations shipping with this, so none is later rediscovered as
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
- [x] 7.4 Run `/review-pr`. Done — 5 lenses, then a second independent review session; all findings addressed in 387fd7ff and 5bcb5f71.
- [x] 7.5 Re-run task 1.1 immediately before merge and paste the output. **DONE 2026-09-16T23:00:00Z — all five IN SYNC, exit 0** (see 1.1).
- [x] 7.6 **Rollback plan, in the PR body — and it restores a known-BROKEN state, not a known-good
      one.** If dispatch begins failing after merge, revert this PR on `staging` (restoring the
      four-task file and the old pin) and redeploy. Three things an operator must know:
      - **In-flight Workflows are unaffected.** The submitted CRD embeds the whole DAG, and
        `templateRef`s resolve against cluster objects the revert does not touch. Five-task
        Workflows already running complete normally, gate included.
      - **Queued batches change shape mid-run.** pgmq messages carry only
        `(run_id, batch_index, scan_ids)`, so a batch enqueued before the revert dispatches
        afterwards as a **four-task** DAG. One run can end up with batch 0 five-task and batch 1
        four-task — different failure semantics inside a single run, rolling up as an ordinary
        `'partial'` with nothing recording why.
      - **The revert does not undo what actually changed on the cluster.** The three producer
        templates got new image pins on 2026-09-16. Reverting gives the four-task DAG (no
        `continueOn`, no gate) driving the **new** producer images — a combination that has never
        run anywhere, and one that re-arms #56's data-loss bug exactly: a partial producer kills the
        DAG and strands the good scans.
      Affected scan rows do **not** self-recover: they are marked `failed` with no requeue, and per
      the Impact section the failure arrives via the poller seeing an `Error` Workflow, not via a
      dead-lettered message — so there is no queue artifact to find. Reconcile by hand.

## 8. Post-merge — blocks archiving

Upstream **§7.4** (the poison-scan scenario) is blocked on this PR: `cyl_pipeline_runs`/
`cyl_pipeline_run_scans` rows are created by Bloom's `POST /workflows/pipeline` route at enumerate
time, so a hand `argo submit` creates nothing for those assertions to attach to, and dispatching
through Bloom before this lands would exercise the old four-task DAG.

**Do not archive until 8.1-8.3 are done.** Record what was *observed*, not what was expected, and
record it after the run rather than at merge (bloom#708 task 14.9 precedent).

> **§8 SATISFIED 2026-09-21 by upstream's §7.4b re-run — this IS that task's Bloom-side half.**
> Recorded in full at `talmolab/sleap-roots-pipeline`
> `openspec/changes/archive/2026-09-21-add-partial-success-exit-gate/tasks.md` §7.4b; summarised
> here so this change's record stands alone.
>
> `POST /workflows/pipeline` as `bloom-pipeline-workflows` with
> `scan_ids=[12894760, 12894758, 12894759]` → `pipeline_run_id=10`, Argo
> `sleap-roots-pipeline-p6lz2`, 19:30:52Z→19:34:40Z, `Succeeded`. **All six criteria PASS.**
>
> | criterion | observed |
> |---|---|
> | DAG reaches `write-back` | PASS |
> | poison isolated at download | `FAILED scan_12894760: 1 of 1 frames failed to download`, **exit 3** ×3 attempts; retries correctly re-skipped the two good scans |
> | `continueOn` advances the DAG | predictor / trait-extractor / write-back / exit-gate all exit `0` |
> | both good scans land | fresh `.result.json` at 19:33:58Z against a 19:30:52Z start, each `predict_container_digest` matching its deployed template pin |
> | poison's per-scan row | `failed`, `source_id=None`, no staged dir, no predictions, no envelope |
> | `done_count`/`failed_count` | **`2`/`1`** — `12894758`→`written`/`source_id=145`, `12894759`→`written`/`source_id=146` |
>
> **The pre-state problem 8.1 existed to solve was solved better.** Rather than snapshotting
> `source_id`/`created_at` to distinguish a real result from an idempotent no-op, the run used
> **three brand-new synthetic scans** minted by bloom PR #884's `create-test-scan` — none with a
> prior envelope — so a no-op was structurally impossible and bloom#875 could not confound the
> result. That also means the scan ids differ from the `12894751`/`45`/`46` planned below.
>
> **Read `write-back`'s summary line with care**, recorded upstream and repeated here because it
> looks alarming and is not: it printed `Ingested 2/12 envelopes (10 failed)`. The **2** are this
> run's good scans; the **10** are srp#71 noise — the shared manifest unions and never prunes, so a
> 3-scan request carried 12 keys, and the 10 outside this run have no `cyl_pipeline_run_scans` row
> under `p6lz2` to mark. They cannot affect run 10's counts, which derive only from rows carrying
> this workflow name. That is precisely why the counts came out clean while the headline reads bad.

- [x] 8.1 **Superseded — see the §8 note above.** Pre-dispatch state capture was unnecessary
      because the run used newly minted scans with no prior envelope.
      Original task: **Capture pre-dispatch state — without this the run proves nothing.**
      (a) Assert `scan_12894751` is **not** already staged under the shared `a4_poc/input` path: a
      staged sidecar makes `stage_one_scan` return `skipped`, which counts as usable and exits `0`,
      so the poison scan silently stops being poison.
      (b) Record existing `source_id` and `created_at` for `12894745`/`12894746`. `idempotency_key`
      excludes volatile provenance, so a re-run with the same models/params re-delivers the same key
      and the RPC short-circuits to a no-op — "`cyl_trait_sources` reflects the two good scans" is
      otherwise satisfiable entirely by rows a *previous* run wrote.
      (c) Copy `run_manifest.json` from all three `a4_poc` directories, so an already-armed latch is
      distinguishable from a failure this run caused. Also assert `scan_12894751`'s key is **not
      already in** `a4_poc/input/run_manifest.json`: the manifest unions and never prunes, and per
      §7.6 predict *scopes to the leftover manifest* rather than discovering, so a stale poison key
      would be picked up, failed, and carried to write-back — turning 8.5's expected `complete` into
      `failed` for a reason unrelated to this change.
      (d) Assert the `a4_poc/predictions` artifacts for the good scans are **present**. This is the
      one condition under which sleap-roots-pipeline#76 is not a hazard: artifacts present → predict
      skips → no new bytes → write-back succeeds. If they have been cleared, predict recomputes at
      an unchanged key and collides.
- [x] 8.2 **DONE 2026-09-21** — run 10 / `sleap-roots-pipeline-p6lz2`, scans `[12894760, 12894758, 12894759]` (see the §8 note above; the planned ids were superseded by fresh synthetic ones). Original: Dispatch through Bloom on staging over the shared `a4_poc` paths: poison scan `12894751`
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
- [x] 8.3 **DONE — exit codes captured, not just DB state:** `images-downloader` exit `3` (×3 attempts), all four downstream tasks exit `0`. Original: **Capture the exit codes, not only the DB state.** Before TTL GC, record each producer
      node's `exitCode` and the gate's decision
      (`kubectl get wf <name> -n runai-busch-lab -o jsonpath=...`). Expect `{3,0,0}`. For a change
      whose one-line summary is "exit code 3 is now consumed", a DB-only oracle never observes an
      exit code at all — and a gate miswired to read `images-downloader.exitCode` three times would
      pass a DB-only check cleanly. Then assert: `done_count`/`failed_count` populated from real
      per-scan status (possible only because PR #774 landed), a per-scan `failed` row for the poison
      scan, and **changed** `source_id`/`created_at` for the good scans versus 8.1(b).
- [ ] 8.4 **NOT RUN — and archiving anyway, for reasons stated rather than assumed.** No
      crash-path batch was ever dispatched *through Bloom*. What this task was guarding against is
      "a gate that always exits 0 would pass everything above", and that specific worry is closed
      by other evidence: upstream **§7.5** drove all three producers to `exitCode 1` and the gate
      received a real `{1,1,1}`, rejected it, and failed the Workflow — so the gate demonstrably
      does not always pass. What §7.5 did *not* exercise is Bloom's dispatch route, and the
      remaining gap is therefore only the mapping `Failed` Workflow → `'failed'` run status, which
      is unit-tested in `tests/test_status_poller.py`
      (`test_a_failed_run_may_still_have_written_results`) rather than observed live.
      Residual risk accepted knowingly: an end-to-end crash through Bloom has never been observed.
      If a crash-path run ever happens naturally, record it against this task rather than
      re-deriving the gap. Original task: **Negative control.** Dispatch a second batch through
      Bloom that drives a producer to an
      exit code outside `{0,3}` (a crash, not per-scan isolation). Upstream §7.5 proved this
      hand-submitted (`srp-t75-crash-4qd66`, gate `{1,1,1}` → `Failed`), but it has never run
      through Bloom's dispatch route. Assert Workflow `Failed` and run status `failed`. Without it,
      a gate that always exits `0` passes everything above. Note §7.5 used
      `scan-ids=not-an-int`, which Bloom's trigger route may reject before dispatch — if no such
      case can be constructed from the dispatch path, record *why* here rather than leaving it
      unsaid.
      ⚠️ **Contain this one.** A crash *after* a manifest write leaves `scan_key`s with no result,
      which is exactly how the write-back latch arms — and on the shared `a4_poc` tree that would
      then fail every subsequent run over those paths, **including production's**. Either run it
      against a scratch tree (as upstream §7.5 did, accepting the reduced fidelity), or snapshot and
      restore all three `run_manifest.json` files around it.
- [x] 8.5 **DONE — `done_count=2`/`failed_count=1` with the Workflow `Succeeded`**, i.e. the documented bloom#857 shape: a green Workflow alongside a genuinely failed scan. Original: Record the observed run status. Expect **`complete` with `failed_count > 0`** — that is
      the documented bloom#857 behaviour, now written into the `cyl-pipeline-runs` and
      `cyl-pipeline-status-polling` deltas, not a failure of this change. If it reads `failed`,
      check 8.1(c) first: an already-armed manifest latch produces a `failed` run that nonetheless
      wrote correct data.
- [x] 8.6 **DONE 2026-09-21.** bloom#772 closed with the 7.4b evidence attached (comment 5768207410) — not a bare close; the comment lays out why it stayed open through #830 and what finally closed it, and names the two limitations it does NOT close (bloom#857, bloom#867). srp#56 commented (comment 5768212630) with the full §7 record, framed as a retroactive verification note since it auto-closed 2s after PR #60 merged — a live bloom#780 instance — and pointing at the archived path `openspec/changes/archive/2026-09-21-add-partial-success-exit-gate/`, the old one having gone. Original: Close **bloom#772** with the observed evidence. PR #830 deliberately used "Related to",
      not "Fixes", because the CLI change alone did not fix the live symptom; 8.2's poison-scan run
      is that symptom's actual fix. Comment on **sleap-roots-pipeline#56** with the result too — it
      auto-closed on PR #60's merge with its own §7 acceptance criteria unrun, a live instance of
      the bloom#780 pattern.
- [x] 8.7 **DONE 2026-09-21** — archived as `2026-09-21-vendor-five-task-pipeline-dag` in PR #878, after confirming 0.1's ordering (see 0.1: `fix-argo-workflow-vendoring` archived first in the same PR; the remaining sibling is recorded there as a live hazard). The only item left unticked is 8.4, deliberately and with its reasoning written out. Original: Only then: confirm task 0.1's archive ordering, verify every item above is `- [x]`, and
      run `/cleanup-merged` → `openspec archive vendor-five-task-pipeline-dag --yes`.
