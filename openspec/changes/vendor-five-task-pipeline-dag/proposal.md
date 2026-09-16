# Vendor the five-task pipeline DAG (exit-gate)

## Why

[sleap-roots-pipeline#56](https://github.com/talmolab/sleap-roots-pipeline/issues/56): the three
producer stages (`images-downloader`, `predictor`, `trait-extractor`) emit exit code **3** for
partial success — "the batch ran to completion; some scans isolated-failed" — and no Argo template
reacted to it. `retryPolicy: Always` retried one genuinely-failing scan to budget exhaustion and
then killed the whole DAG, discarding the scans that had already succeeded. Demonstrated live on
2026-09-01 (workflow `sleap-roots-pipeline-jqsf9`: `Failed` at 0/3, with two confirmed-good scans
stranded — recorded in both #56 and bloom#772, not only in the since-expired Workflow object).

That exit-3 convention is the salk-bloom side of the same fix — `bloomctl`'s
`batch-download-for-predict` gained it in `fix-cyl-batch-download-partial-exit-code`
([bloom#772](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/772), shipped by
PR #830), which deliberately shipped *behaviorally inert*: it emits a code nothing consumed yet.
This change is what makes it consumed.

Upstream [PR #60](https://github.com/talmolab/sleap-roots-pipeline/pull/60) (MERGED
2026-09-16T02:20:47Z, squash commit `310aae63c4db4cc1eb608a4d7801031f0061106d`) fixed this with
`continueOn: {failed: true}` on the three producers **plus a fifth terminal DAG task,
`exit-gate`**, which re-derives the Workflow phase from the producers' real exit codes (`{0,3}`
pass, anything else fails).

**The gate is load-bearing, not defensive.** Argo's `continueOn` keys on node *phase*, not exit
code ([argo-workflows#6396](https://github.com/argoproj/argo-workflows/issues/6396), open), so
`continueOn` alone would report a genuine crash as `Succeeded` — strictly worse than the bug it
replaces. Without the gate, `write-back` would be the DAG's only leaf, and a run in which every
producer crashed and zero scans were processed would report `Succeeded`.

The gate's own semantics are verified upstream and recorded: **§7.3, the gate truth-table probe,
is complete** — 7/7 exit-code vectors run 2026-09-15 in `runai-talmo-lab`. The end-to-end
crash-injection scenario (**§7.5, upstream's "load-bearing test"**) is **still unchecked upstream
and is not recorded in the roadmap or on PR #60**; an operator-reported probe exists but has no
durable record, so this proposal does not rest on it. §7.5 remains outstanding upstream and is
mirrored here as a post-merge task (7.1b), because the crash path has never been exercised through
Bloom's dispatch route at all.

salk-bloom still vendors the **four-task** DAG, so every Bloom-dispatched run today gets none of
this. Upstream's own §8.1 is "open the companion `salk-bloom` PR: copy the merged file byte-exact"
— this change is that step. Vendoring is also what unblocks #56's remaining live verification
(**§7.4**, the poison-scan scenario): a hand `argo submit` creates no
`cyl_pipeline_runs`/`cyl_pipeline_run_scans` rows — those are written by Bloom's
`POST /workflows/pipeline` route at enumerate time — so the `done_count`/`failed_count` assertions
have nothing to attach to until a *Bloom-dispatched* run exercises the five-task DAG.

The exit-gate-vs-alternatives design rationale was decided and recorded upstream in
`sleap-roots-pipeline`'s `add-partial-success-exit-gate`; no local `design.md` is needed, because
this change vendors that decision rather than making one. The one genuinely salk-bloom-side
decision — deploy sequencing into a production-shared namespace — is covered under Impact below.

## What Changes

- **Vendor the merged Workflow byte-exact.** Replace
  `services/workflows/vendored/sleap-roots-pipeline.yaml` with upstream's file at
  `310aae63c4db4cc1eb608a4d7801031f0061106d` (10,833 bytes, LF, no trailing newline), and bump the
  sibling `SLEAP_ROOTS_PIPELINE_REF` from `9df1e52dafb565763de279e9f07e8b52804a1ac3` to that SHA.
  `scripts/check_vendored_workflow_drift.py` compares `read_bytes()` against the raw upstream file
  at the pinned SHA, so the copy must not be reformatted, re-indented, or stripped of comments.
- **The five WorkflowTemplates are NOT vendored** and are untouched by this change. They resolve
  via `templateRef` against the cluster at runtime. All five are registered in `runai-busch-lab`:
  operator-observed 2026-09-16T17:19Z, with `sleap-roots-exit-gate-template` created
  2026-09-16T02:39:01Z — 18 minutes after PR #60 merged. **Note two upstream records still say
  otherwise** (task 7.2 unchecked; the roadmap's "returns four as of 2026-09-16"); both predate the
  02:39Z registration and are stale. Reconciling them is tracked in `tasks.md` 1.2.
- **Update the DAG-shape tests.**
  `services/workflows/tests/test_k8s_client.py::test_build_workflow_body_dag_references_all_four_templates_in_order`
  hardcodes four `templateRef` names and a four-long dependency chain. It is renamed to
  `..._all_five_templates_in_order` and extended, and **three new tests** are added alongside it
  covering the invariants that make the design work (see below).
- **Correct the four-template claim everywhere it is stated as fact.** Three sites, found by
  grepping the *claim* repo-wide rather than one file: `k8s_client.py:182`,
  `services/workflows/README.md:131-134` (the same four-name arrow chain), and the
  `cyl-pipeline-dispatch` spec's normative prose. Each → five.
- **No production code changes.** Verified by execution against the real upstream file, not
  argued: `build_workflow_body` passes the five-task DAG through unmodified and JSON-serializable.
  It applies its four overrides to `spec.arguments.parameters[0]` (still `scan-ids`, still the only
  workflow-level parameter), `metadata.labels` (still only `project: busch-lab` upstream, so no
  collision), `spec.ttlStrategy` (still absent upstream, so an add not a clobber), and
  `metadata.namespace`. The gate's *task-level* `arguments.parameters` are never read and pass
  through intact.

### New test assertions

Each is written as an **exact set or exact sequence**, not a spot-check. A prior review round in
this program found an "allowlist assertion" that was a substring check which still passed when the
thing under test was mutated to accept every input; each assertion below was chosen because a
weaker form was demonstrated to miss a real mutation.

1. **`exit-gate` is the DAG's only leaf** — stated as "exactly one task is depended on by no other,
   and it is `exit-gate`". The weaker "no task depends on `exit-gate`" passes *vacuously* on the
   current four-task file, and also passes when a second leaf is added. Argo's `assessDAGPhase`
   takes the Workflow phase from the leaf, so a producer that is also a leaf defeats `continueOn`.
2. **`continueOn` is on exactly the three producers** — as a set equality over task names, not
   three individual checks. `continueOn` on `exit-gate` makes the terminal leaf continuable and
   restores the original defect at the last hop; a per-task check does not catch it.
3. **The gate's three exit-code parameters**, plus a cross-check that each `{{tasks.<name>}}`
   reference names a task that actually exists. Renaming a producer while updating only the
   dependency chain otherwise leaves a dangling reference that `dag.go` substitutes with
   `allowUnresolved=true`.
4. **The DAG is resolved through `spec.entrypoint`**, not `spec.templates[0]`, and exactly one
   template carries a `dag`. Indexing `[0]` passes even when a second, gateless template is
   appended and the entrypoint repointed at it.
5. **`templateRef.template`** (the inner template name) is asserted alongside `templateRef.name`,
   as pairs — otherwise a task can invoke the wrong template out of the right `WorkflowTemplate`.
6. **A standing gateless-vendored-file test**: a file and pin bumped together to a gateless DAG
   passes the byte-level drift check, so these assertions are the only thing between that and
   production. Asserted permanently, not just as a one-time red.

### Two corrections the vendoring carries in

Beyond the DAG, upstream's file also (a) changed its own "four stage templates" comment to five,
and (b) **corrected a factual claim**: `type: Directory` does *not* "fail loudly" when the NFS
mount is missing — the pod sits `Pending` and the Workflow **hangs**. Preventing silent data loss
is still the reason to keep `Directory` over `DirectoryOrCreate`; the operational expectation is a
hang, not an error. That claim was grepped repo-wide; salk-bloom does not restate it anywhere else,
so vendoring fixes it completely.

## Non-Goals

- **The five WorkflowTemplates' contents.** Not vendored, not editable from this repo — but see the
  Risk note in Impact: the gate's fail-safe guards live there, unverifiable from here.
- **[sleap-roots-pipeline#72](https://github.com/talmolab/sleap-roots-pipeline/issues/72) — the
  gate image is pinned by tag with `IfNotPresent`, and depends on `/bin/sh` in a versioned
  application image.** OPEN, filed 3 minutes after PR #60 merged. This is the highest-consequence
  known defect in the thing being vendored: because `exit-gate` is the DAG's *only leaf*, anything
  that stops the gate pod from running fails or hangs **every** workflow, including fully
  successful ones. It is invisible to every check this change relies on — `argo lint`, the drift
  check, and the new shape tests all pass with it present.
- **[sleap-roots-predict#44](https://github.com/talmolab/sleap-roots-predict/issues/44) — narrow
  the forwarded `run_manifest.json` to `ok ∪ skipped`.** OPEN. Forwarding the manifest unchanged is
  safe only because of the behaviour this change alters: once the templates discriminate exit codes
  and trait-extraction can run after a partial predict, a raw-forwarded manifest turns each of
  predict's failed scans into a *trait-extraction* failure — **misattributed to the wrong stage**.
  Of the deferred items this is the only one that causes wrong data attribution rather than a wrong
  status string.
- **Restoring a within-batch `partial` signal
  ([bloom#857](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/857)).**
  `status_poller.rollup` derives run status from Argo Workflow phases alone. A batch that isolates
  some scans' failures now exits `3`, the gate accepts it, and the phase is `Succeeded` — so
  rollup rule (4)'s `'partial'` no longer arises from partial failure *within* a batch, the case it
  was introduced for. (It remains reachable when whole batch Workflows differ in outcome across a
  multi-batch run; "unreachable at any batch size" would be too strong.) After this change a run
  can read `complete` with `failed_count > 0`. **A green Workflow does not mean no scans failed.**
  This is now stated normatively in the `cyl-pipeline-runs` and `cyl-pipeline-status-polling`
  deltas rather than left to a GitHub issue. (The zero-scan case is *unmeasured* and is
  input-directory-state-dependent — on a fresh directory predict discovers nothing and exits 1, so
  the gate rejects and the Workflow fails; on the shared directory it skips everything and exits 0,
  so the Workflow is green. Upstream §7.6 exists to characterise this and explicitly forbids
  asserting either branch as fact until run.)
- **The write-back manifest latch
  ([sleap-roots-pipeline#71](https://github.com/talmolab/sleap-roots-pipeline/issues/71) symptom 2,
  with [#63](https://github.com/talmolab/sleap-roots-pipeline/issues/63) as the mechanism).** A
  partial predict/traits still fails the Workflow at write-back: a manifest `scan_key` with no
  result is reported as a batch failure and marked retriable
  ([bloom#859](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/859)). Because
  `write_run_manifest` unions and never prunes, over three fixed directories shared by
  prod/staging/manual with nothing that cleans them, a **deterministically**-failing scan latches:
  it fails every subsequent run over those paths, including runs whose own scans all succeeded,
  after ingesting them. A *transient* failure self-heals on the next run, since producers
  re-process any manifest key lacking a result. This change is what makes the latch reachable
  (previously a partial producer killed the DAG so write-back never ran) — a known consequence of
  shipping this, not a regression introduced by it.
- **Run attribution in artifacts ([bloom#703](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/703)).**
  `provenance.pipeline_run_id`, `argo_workflow_uid`, `argo_node_id` and `worker_request_id` are
  reported empty in live `result.json` envelopes — the `Provenance` contract defines them and
  nothing populates any of them, so both sides of the intended cross-run cross-check are blank.
  (Corroborated independently by sleap-roots-pipeline#70's 2026-09-15 envelope reading.)
- **Shared-path manifest scoping
  ([sleap-roots-pipeline#71](https://github.com/talmolab/sleap-roots-pipeline/issues/71)).**
  Confirmed structurally from this repo: the vendored Workflow hardcodes the three `a4_poc` paths
  and `spec.arguments.parameters` contains only `scan-ids` — there is **no path parameter**, so
  every prod and staging run shares one directory. This is by design and **per-run path isolation
  is explicitly ruled out** by both #71 and #37 (it would break the skip-if-done dedup and the
  batch oracle); the fix shape is per-run *manifest identity* with shared artifacts. Cross-repo.
- **Adding `argo lint` to salk-bloom CI.** The offline lint recipe is used here as a manual
  verification step (`tasks.md` 5.3). Wiring it into CI would mean installing `argo` and fetching
  five upstream files per run — real scope creep on a vendoring change. Possible follow-up.

## Impact

- **Affected specs:** `cyl-pipeline-dispatch` (one MODIFIED, one ADDED requirement);
  `cyl-pipeline-runs` (one MODIFIED requirement); `cyl-pipeline-status-polling` (one ADDED
  requirement). The latter two are not incidental: this change falsifies a normative statement in
  `cyl-pipeline-runs` — that `'partial'`/`'failed'` exist because both describe *"some or all scans
  got no useful pipeline result"* — by making `'complete'` describe that too. `'complete'` is the
  string a dashboard, a downstream consumer, or a future agent branches on to decide whether an
  experiment's traits are whole, so the redefinition is written into the specs rather than left in
  a GitHub issue and a Non-Goals bullet.
- **Affected code:** `services/workflows/vendored/sleap-roots-pipeline.yaml`,
  `services/workflows/vendored/SLEAP_ROOTS_PIPELINE_REF`,
  `services/workflows/tests/test_k8s_client.py`, `services/workflows/k8s_client.py` (docstring
  only), `services/workflows/README.md` (docs only).
- **Deploy semantics — the risky part of this change, not the diff.** Merging to `staging` **is**
  the deploy; there is no separate release step for the dispatch path. prod and staging
  deliberately share the `runai-busch-lab` namespace, disambiguated only by the
  `WORKFLOWS_K8S_ENV_LABEL` stamped on each submitted Workflow. `staging` → `main` promotion is the
  production cutover for this DAG; until it happens production keeps dispatching the four-task DAG
  and gets none of #56's fix, while already running the three new image pins applied to the
  templates on 2026-09-16.
- **Ordering constraint and its true blast radius.** The gate template must be registered before
  any vendored five-task DAG ships. If it is not, submission fails — and per this capability's
  existing "Submission outcome is recorded before the message is settled" requirement, a failed
  submission marks **every scan row in the batch `failed` and dead-letters the queue message**,
  with requeue explicitly out of scope. So the failure mode is not a transient error but the
  **permanent, unrecoverable failure of every batch dispatched in the window**, across prod and
  staging simultaneously, with no automatic recovery once the template is restored. Verified
  satisfied 2026-09-16 (`tasks.md` 1.1 re-checks at merge; 6.6 is the rollback).
- **Risk: the change's fail-safe behaviour lives in an object this repo neither vendors nor
  drift-checks.** The gate template carries `timeout: 600s` — which converts "gate pod stuck
  `Pending` ⇒ Workflow hangs forever ⇒ Bloom's poller never resolves the run" into a bounded
  failure — plus a `retryStrategy` and explicit `resources` to avoid BestEffort eviction. After
  this change the DAG has a mandatory fifth pod *between "all work committed" and "Workflow
  terminal"*, and salk-bloom has no mechanism to confirm those guards are present in the registered
  copy. `tasks.md` 1.1 verifies the registered template's `inputs.parameters` for this reason;
  see also sleap-roots-pipeline#72 under Non-Goals.
- **Pre-existing defect this change's promotion step would activate: prod-dispatched Workflows
  mount *staging* credentials.** The vendored file hardcodes
  `secretName: genericsecret-bloom-staging-pipeline-credentials`, and `build_workflow_body`'s four
  overrides do not include it — prod and staging share `runai-busch-lab`, separated only by a
  metadata label that steers nothing. So a production-dispatched run's write-back pod authenticates
  against **staging** Supabase: its `cyl_pipeline_run_scans` rows never match, and its envelopes
  resolve `image_ids` against staging's `cyl_images`. Nothing here causes this and it does not
  affect the staging merge, but `staging → main` is exactly what makes prod runs execute through to
  write-back. Filed as a follow-up (`tasks.md` 6.1) and **must block that promotion**. Note the
  vendored file's own comment anticipates it: the `-staging` suffix exists "so a later production
  credential gets its own distinct name".
- **Coupling: the gate runs a salk-bloom-published image.** Upstream's gate template pins
  `ghcr.io/salk-harnessing-plants-initiative/bloomctl:sha-0614889` (= `06148896`, PR #774,
  populating `done_count`/`failed_count`). The terminal single point of failure of the pipeline is
  an artifact *this* repo publishes, and that pin must move in lockstep with the images-downloader
  and write-back templates.
- **New observable state for consumers of `cyl_pipeline_runs`.** `status_poller` needs no change —
  it reads `status.phase` and never enumerates DAG nodes — but the phases it observes deliberately
  change. Beyond `complete` with `failed_count > 0` (bloom#857), a previously impossible
  combination becomes reachable: **`failed` with `done_count > 0`**, when write-back commits
  results and the gate then rejects. Before this change write-back never ran on a red DAG, so
  `failed` implied `done_count == 0`. The gate also adds a fifth pod to the critical path, so the
  `Running` window lengthens and scan rows stay `queued` slightly longer.
- **Not BREAKING.** No API, schema, or configuration contract changes. The submitted Workflow body
  gains a task; nothing in this repo or its consumers branches on the DAG's task count.
- **Archive ordering.** `openspec/changes/fix-argo-workflow-vendoring/` is complete (28/28) but
  **not yet archived**, and its delta MODIFIES this same requirement. This change's MODIFIED block
  carries that sibling's text forward (the `spec.volumes`/`entrypoint`/`serviceAccountName`
  paragraph and its volumes scenario) so nothing is lost, but the sibling must still archive
  **first** or it will revert "five" to "four". Gated in `tasks.md` 0.1.
