# Vendor the five-task pipeline DAG (exit-gate)

## Why

[sleap-roots-pipeline#56](https://github.com/talmolab/sleap-roots-pipeline/issues/56): the three
producer stages (`images-downloader`, `predictor`, `trait-extractor`) emit exit code **3** for
partial success — "the batch ran to completion; some scans isolated-failed" — and no Argo template
reacted to it. `retryPolicy: Always` retried one genuinely-failing scan to budget exhaustion and
then killed the whole DAG, discarding the scans that had already succeeded. Demonstrated live on
2026-09-01 (workflow `sleap-roots-pipeline-jqsf9`: `Failed` at 0/3, with two confirmed-good scans
stranded).

That exit-3 convention is the salk-bloom side of the same fix — `bloomctl`'s
`batch-download-for-predict` gained it in `fix-cyl-batch-download-partial-exit-code`
([bloom#772](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/772)/#830), which
deliberately shipped *behaviorally inert*: it emits a code nothing consumed yet. This change is
what makes it consumed.

Upstream [PR #60](https://github.com/talmolab/sleap-roots-pipeline/pull/60) (MERGED
2026-09-16T02:20:47Z, squash commit `310aae63c4db4cc1eb608a4d7801031f0061106d`) fixed this with
`continueOn: {failed: true}` on the three producers **plus a fifth terminal DAG task,
`exit-gate`**, which re-derives the Workflow phase from the producers' real exit codes (`{0,3}`
pass, anything else fails).

**The gate is load-bearing, not defensive.** Argo's `continueOn` keys on node *phase*, not exit
code ([argo-workflows#6396](https://github.com/argoproj/argo-workflows/issues/6396), open), so
`continueOn` alone would report a genuine crash as `Succeeded` — strictly worse than the bug it
replaces. This was proved live rather than argued: in crash-injection run `srp-t75-crash-4qd66`
(2026-09-16) all three producers ended `Failed` with `exitCode 1`, and **`write-back` succeeded
with exit 0**. Without the gate, write-back would have been the DAG's only leaf and the Workflow
would have reported `Succeeded` for a run in which every producer crashed and zero scans were
processed. The gate received `{1,1,1}`, rejected it, and failed the Workflow correctly.

salk-bloom still vendors the **four-task** DAG, so every Bloom-dispatched run today gets none of
this. Vendoring is also what unblocks #56's remaining live verification (§7.4b): a hand
`argo submit` creates no `cyl_pipeline_runs`/`cyl_pipeline_run_scans` rows — those are written by
Bloom's `POST /workflows/pipeline` route at enumerate time — so the `done_count`/`failed_count`
assertions have nothing to attach to until a *Bloom-dispatched* run exercises the five-task DAG.

## What Changes

- **Vendor the merged Workflow byte-exact.** Replace
  `services/workflows/vendored/sleap-roots-pipeline.yaml` with upstream's file at
  `310aae63c4db4cc1eb608a4d7801031f0061106d` (10,833 bytes, LF, no trailing newline), and bump the
  sibling `SLEAP_ROOTS_PIPELINE_REF` from `9df1e52dafb565763de279e9f07e8b52804a1ac3` to that SHA.
  `scripts/check_vendored_workflow_drift.py` compares `read_bytes()` against the raw upstream file
  at the pinned SHA, so the copy must not be reformatted, re-indented, or stripped of comments.
- **The five WorkflowTemplates are NOT vendored** and are untouched by this change. They resolve
  via `templateRef` against the cluster at runtime. All five — including
  `sleap-roots-exit-gate-template` — are already registered in `runai-busch-lab` (verified
  2026-09-16, gate age 14h).
- **Update the DAG-shape test.**
  `services/workflows/tests/test_k8s_client.py::test_build_workflow_body_dag_references_all_four_templates_in_order`
  hardcodes four `templateRef` names and a four-long dependency chain. It is renamed to
  `..._all_five_templates_in_order`, extended with `sleap-roots-exit-gate-template` and
  `deps[4] == [tasks[3]["name"]]`, and gains three new assertions covering the invariants that
  make the design work (see below).
- **Correct the four-template claim where it is stated as fact.** `k8s_client.py:182` says the
  vendored DAG references "the four already-registered WorkflowTemplates" → **five**. The
  `cyl-pipeline-dispatch` spec restates the same claim in normative prose (naming all four
  templates) → updated to five. Both were found by grepping the *claim*, not the file.
- **No production code changes.** `build_workflow_body` needs no modification: it applies its four
  overrides to `spec.arguments.parameters[0]` (still `scan-ids`, still the only parameter),
  `metadata.labels`, `spec.ttlStrategy`, and `metadata.namespace`. The gate's *task-level*
  `arguments.parameters` pass through untouched. `status_poller.py` is phase-based and never
  enumerates DAG nodes, so a fifth node does not perturb it.

### New test assertions

1. **`exit-gate` is the DAG's only leaf** — no task lists it in `dependencies`. This is the
   invariant the whole design rests on: Argo's `assessDAGPhase` takes the Workflow phase from the
   leaf, so a producer that was *also* a leaf would surface its own `Failed` phase and defeat
   `continueOn` entirely.
2. **`continueOn: {failed: true}` on the three producers, and absent on `write-back`.** A vendored
   copy that silently lost `continueOn` would pass the byte check only if the REF also changed — a
   hand-edit would otherwise slip through the shape tests as they stand.
3. **The gate's three exit-code parameters** — `images-downloader-code`, `predictor-code`,
   `trait-extractor-code`, each wired to `{{tasks.<name>.exitCode}}`. Catches a miswired or dropped
   parameter, which would otherwise reach the gate as an unresolved literal (`dag.go` substitutes
   with `allowUnresolved=true`).

### Two corrections the vendoring carries in

Beyond the DAG, upstream's file also (a) changed its own "four stage templates" comment to five,
and (b) **corrected a factual claim**: `type: Directory` does *not* "fail loudly" when the NFS
mount is missing — the pod sits `Pending` and the Workflow **hangs**. Preventing silent data loss
is still the reason to keep `Directory` over `DirectoryOrCreate`; the operational expectation is a
hang, not an error. That claim was grepped repo-wide; salk-bloom does not restate it anywhere else,
so vendoring fixes it completely.

## Non-Goals

- **The five WorkflowTemplates' contents.** Not vendored, not editable from this repo.
- **Making `partial` reachable ([bloom#857](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/857)).**
  `status_poller.rollup` derives run status from Argo Workflow phases alone, and *every* Argo
  continue-past-failure mechanism yields `Succeeded`, so `partial` is unreachable at any batch
  size. After this change a run can read `complete` with `failed_count > 0`. **A green Workflow
  does not mean no scans failed**, and a zero-scan batch is also green. Documented, not fixed here.
- **The write-back manifest latch ([bloom#859](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/859)).**
  A partial predict/traits still fails the Workflow at write-back: a manifest `scan_key` with no
  result is reported as a batch failure and marked retriable. Measured 2026-09-16, this is a
  **latch**, not a per-run annoyance — `write_run_manifest` unions and never prunes, over three
  fixed directories shared by prod/staging/manual with nothing that cleans them
  ([sleap-roots-pipeline#63](https://github.com/talmolab/sleap-roots-pipeline/issues/63)) — so one
  predict/traits failure fails **every future run** over those paths, including runs whose own
  scans all succeeded, after ingesting them. This change is what makes that reachable (previously a
  partial producer killed the DAG so write-back never ran). Tracked separately; it is a known
  consequence of shipping this, not a regression introduced by it.
- **Run attribution in artifacts ([bloom#703](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/703)).**
  Measured 2026-09-16: `provenance.pipeline_run_id`, `argo_workflow_uid`, `argo_node_id` and
  `worker_request_id` are empty in all 12 live `result.json` envelopes — the `Provenance` contract
  defines them and nothing populates any of them, so both sides of the intended cross-run
  cross-check are blank.
- **Per-run path isolation ([sleap-roots-pipeline#71](https://github.com/talmolab/sleap-roots-pipeline/issues/71)).**
  Confirmed structurally from this repo: the vendored Workflow hardcodes the three `a4_poc` paths
  and `spec.arguments.parameters` contains only `scan-ids` — there is **no path parameter**, so
  every prod and staging run shares one directory, by design (the skip-if-done dedup the program
  relies on only works because paths are shared). Cross-repo; out of scope.
- **Adding `argo lint` to salk-bloom CI.** The offline lint recipe is used here as a manual
  verification step (see `tasks.md`). Wiring it into CI would mean installing `argo` and fetching
  five upstream files per run — real scope creep on a vendoring change. Possible follow-up.

## Impact

- **Affected specs:** `cyl-pipeline-dispatch` (one MODIFIED requirement, one ADDED requirement).
- **Affected code:** `services/workflows/vendored/sleap-roots-pipeline.yaml`,
  `services/workflows/vendored/SLEAP_ROOTS_PIPELINE_REF`,
  `services/workflows/tests/test_k8s_client.py`, `services/workflows/k8s_client.py` (docstring
  only).
- **Deploy semantics — the risky part of this change, not the diff.** Merging to `staging` **is**
  the deploy; there is no separate release step for the dispatch path. prod and staging
  deliberately share the `runai-busch-lab` namespace, disambiguated only by the
  `WORKFLOWS_K8S_ENV_LABEL` stamped on each submitted Workflow, and a real production dispatch
  deployment is live there (`bloom_v2_prod-cyl-pipeline-worker-1`,
  `bloom_v2_prod-cyl-status-poller-1`). `staging` → `main` promotion is the production cutover for
  this DAG; until it happens production keeps dispatching the four-task DAG and gets none of #56's
  fix, while already running the three new image pins applied to the templates on 2026-09-16.
- **Ordering constraint, already satisfied.** The gate template must be registered before any
  vendored five-task DAG ships, or every dispatch fails at submit — a simultaneous prod and staging
  outage. Verified present 2026-09-16 (see `tasks.md` 1.1 for the re-check at merge time).
- **Not BREAKING.** No API, schema, or configuration contract changes. The submitted Workflow body
  gains a task; nothing in this repo or its consumers branches on the DAG's task count.
