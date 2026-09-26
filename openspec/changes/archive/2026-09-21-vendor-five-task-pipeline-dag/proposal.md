# Vendor the five-task pipeline DAG (exit-gate)

## Why

[sleap-roots-pipeline#56](https://github.com/talmolab/sleap-roots-pipeline/issues/56): the three
producer stages (`images-downloader`, `predictor`, `trait-extractor`) emit exit code **3** for
partial success — "the batch ran to completion; some scans isolated-failed" — and no Argo template
reacted to it. `retryPolicy: Always` retried the whole failing *step* to budget exhaustion (the
poison scan failing identically every attempt) and then killed the DAG. Demonstrated live on
2026-09-01 (workflow `sleap-roots-pipeline-jqsf9`: `Failed` at 0/3, with two confirmed-good scans
fully staged but never reaching the predictor — recorded in both #56 and bloom#772, not only in the
since-expired Workflow object). Note `images-downloader` did not yet emit `3` on that date; it
gained the convention in PR #830 on 2026-09-15, which is why the run failed rather than partially
succeeding.

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

This is a measurement, not an argument from Argo semantics. Upstream **§7.5 ran 2026-09-16**
(`srp-t75-crash-4qd66`, `scan-ids=not-an-int`, against a scratch path tree): all three producers
ended `Failed` with `exitCode 1`, and **`write-back` Succeeded with exit 0** — because the scratch
`traits/` dir was empty and the crash preceded `write_run_manifest`, so `discover_envelopes` fell
back to unscoped discovery, found nothing, and reported success. Without the gate, write-back would
have been the DAG's only leaf and `assessDAGPhase` would have reported **`Succeeded`** for a run in
which every producer crashed and zero scans were processed. The gate received `{1,1,1}` — resolved
to real values, not empty strings, which is also the first live confirmation that
`{{tasks.X.exitCode}}` resolves *through* Retry nodes on this controller — rejected it, and failed
the Workflow correctly.

**These results are recorded upstream in PR #75, merged 2026-09-16T18:50:18Z (`561d0571`).** On
`sleap-roots-pipeline`'s `main` today 7.2, 7.3, 7.5, 7.6, 7.7, 7.8 and 9.1-9.4 all read `[x]`; only
7.4 (split, its Bloom-side half blocked on this change), 8.1 (this PR) and 8.2 remain open.

salk-bloom still vendors the **four-task** DAG, so every Bloom-dispatched run today gets none of
this. Upstream's own §8.1 is "open the companion `salk-bloom` PR: copy the merged file byte-exact"
— this change is that step. Vendoring is also what unblocks the remaining half of #56's live
verification. Upstream split its poison-scan task in PR #75: **§7.4a ran 2026-09-16**
(`srp-t74a-poison-gfzp6`) and passed every *artifact* criterion — `images-downloader` exited **3**
and `continueOn` let the DAG advance past a `Failed` producer, where on 2026-09-01 the identical
scenario ended `Failed` at 0/3 with both good scans stranded and the predictor never running. **That
run's Workflow nonetheless ended `Failed`**, at write-back (exit 1, three attempts, gate `Omitted`)
— that failure is sleap-roots-pipeline#76 under Non-Goals, not #56, and upstream task 7.4 remains
unchecked for exactly that reason.
**§7.4b — the Bloom-side half — is blocked on this change**: `cyl_pipeline_runs`/
`cyl_pipeline_run_scans` rows are written by Bloom's `POST /workflows/pipeline` route at enumerate
time, so a hand `argo submit` creates nothing for `done_count`/`failed_count` to attach to, while
dispatching through Bloom today would exercise the old four-task DAG and prove nothing.

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
  operator-observed 2026-09-16, with `sleap-roots-exit-gate-template` created
  2026-09-16T02:39:01Z — 18 minutes after PR #60 merged — and all five confirmed **in sync with the
  pin** by `scripts/check_registered_templates.py`. Upstream `main` agrees since PR #75 merged
  (task 7.2 `[x]`, roadmap "five DAG tasks live as of 2026-09-16").
  <!-- Annotation, bloom#879 (2026-09-22): the 2026-09-16 observation above was sound at the time,
       but "in sync with the pin" is no longer a meaningful property to assert — the pin is the
       Workflow's, not the templates', and they advance independently of it by design. The script is
       now scripts/check_template_contract.py and checks the vendored Workflow's contract instead. -->


- **Update the DAG-shape tests.**
  `services/workflows/tests/test_k8s_client.py::test_build_workflow_body_dag_references_all_four_templates_in_order`
  hardcodes four `templateRef` names and a four-long dependency chain. It is renamed to
  `..._all_five_templates_in_order` and extended, and **four new tests** are added alongside it
  covering the invariants that make the design work (see below).
- **Correct the four-template claim everywhere it is stated as fact.** Three sites, found by
  grepping the *claim* repo-wide rather than one file: `k8s_client.py:182`,
  `services/workflows/README.md:131-134` (the same four-name arrow chain), and the
  `cyl-pipeline-dispatch` spec's normative prose. Each → five.
- **One one-word production change**, plus no others. `_load_vendored_workflow` calls
  `Path.read_text()` with no `encoding=`, so it decodes the vendored file with the platform locale
  — `cp1252` on a Windows dev box, UTF-8 in the Linux container. Measured: the file holds **63
  non-ASCII bytes / 21 characters** (only `—` and `→`), and `read_text() != raw.decode("utf-8")`
  locally.

  Today this is genuinely harmless, and for a more specific reason than "they're in comments":
  cp1252 maps every byte the file actually contains, so the decode **silently mojibakes rather than
  raising**, and since all 21 characters are in comments that `yaml.safe_load` discards, the parsed
  result is identical either way. The real argument for fixing it is the failure that is one
  upstream edit away: cp1252 leaves five bytes undefined (`0x81 0x8D 0x8F 0x90 0x9D`), none of which
  appear here today. Were one to appear, `read_text()` would raise `UnicodeDecodeError` — which is
  **not** an `OSError`, so it would escape `_load_vendored_workflow`'s `K8sConfigError` contract as
  a raw traceback rather than the fail-fast misconfiguration error every other structural defect in
  that file produces. Fix: `read_text(encoding="utf-8")`, in `k8s_client.py`, the test fixture that
  mirrors it, and `scripts/check_vendored_workflow_drift.py`'s pin read.
- **No other production code changes.** Verified by execution against the real upstream file, not
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
   reference names a task that actually exists. `argo lint` does reject a dangling reference
   (measured, v3.6.5: `failed to resolve`, exit 1) — but this repo does not run it in CI, and the
   raw Kubernetes API accepts the submission either way, so the unit assertion is the only
   automatic guard.
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
- **[sleap-roots-pipeline#76](https://github.com/talmolab/sleap-roots-pipeline/issues/76) —
  re-delivery fails at write-back, because predict's `.slp` output is not byte-reproducible and the
  blob upload precedes the idempotency gate.** OPEN, found while running §7.4a. Two runs of the
  same scan with identical inputs produce an **identical** `idempotency_key` but **different**
  `.slp` bytes (identical file sizes, different digests). The blob address embeds that key, so a
  recompute writes different bytes to the same address and bloomctl refuses to overwrite — correct
  on its own. The flaw is the ordering: the strict blob upload runs *before* the RPC's
  `ON CONFLICT (idempotency_key) DO NOTHING`, which would have made the re-delivery a harmless
  no-op. `Ingested 0/2` confirms nothing reaches the RPC.

  **The discriminator is changed-key vs unchanged-key, not post-bump vs pre-bump.** Re-delivery is
  idempotent on the **skip** path and broken on the **recompute** path — demonstrated by a pair of
  runs rather than argued. Precisely:
  - *unchanged key, artifacts present* → predict skips, no new bytes, write-back succeeds
    (`srp-t77-redeliver-t82vr`: 12 `result.json` mtimes frozen, 24 `.slp` blobs byte-identical,
    Workflow `Succeeded`);
  - *unchanged key, artifacts absent* → predict recomputes and writes different bytes to the same
    address → **collision** (7.4a's fresh scratch tree, against scans ingested an hour earlier);
  - *changed key* (e.g. a predictor pin bump) → the recompute lands at a **new** address and cannot
    collide (`srp-t76-zero-shared-hrrkz` recomputed all 8 scans post-bump; write-back succeeded).

  Note #60's pin bump did not *cause* a collision — it is the changed-key case, which is safe. What
  it did was create the **precondition**, by populating blobs at a fresh address set that a later
  unchanged-key recompute can then collide with. `srp-t74a-poison-gfzp6` was itself post-bump and
  still collided, so "post-bump recomputes are safe" would be false.

  **Operational hazard to carry forward:** if a `predictions/` directory is ever cleared or lost
  while Bloom still holds blobs for those keys, the next run over those scans fails at write-back.
  It also means the A4 batch oracle only ever held on the skip path. For this change's post-merge
  verification the relevant guard is therefore narrow and checkable: the shared `a4_poc/predictions`
  artifacts must still be present at dispatch time, so predict skips (`tasks.md` 8.1(d)).
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
  deltas rather than left to a GitHub issue.
- **The zero-scan outcome — now measured, and worse than "green".** Upstream §7.6 ran it both ways
  on 2026-09-16, same submission, opposite verdict. On a **fresh** directory the stages disagree
  about what "empty" means — `images-downloader` treats zero *requested* as success (exit 0) while
  predict and traits treat zero *discovered* as failure (exit 1) — and the gate converts that
  disagreement into a definite `Failed` on a **mixed** `{0,1,1}` vector, which also proves the gate
  evaluates each producer independently rather than keying off the last or worst. On the **shared**
  `a4_poc` directory, predict scopes to the leftover `run_manifest.json` (8 keys) instead of
  discovering nothing, recomputes all 8, and the Workflow reports a fully green `Succeeded` — so
  **a zero-scan submission does substantial real work on someone else's scan set**. That is
  #37/#71, measured rather than theorised. Not fixed here.
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

  ⚠️ **The latch crosses environments: a *staging* run can latch *production*.** The three `a4_poc`
  paths are hardcoded with no path parameter and are identical in the four- and five-task DAGs, so
  prod and staging write-back read the same `run_manifest.json`. `continueOn` is precisely what
  newly lets a staging partial producer reach write-back at all. Once a staging run leaves a
  `scan_key` with no result there, **production's four-task write-back fails over the same tree**,
  including prod runs whose own scans all succeeded. Checked 2026-09-16: the latch is **not armed**
  — `a4_poc/input/run_manifest.json` holds 8 keys, all with results, mtime 2026-09-10, and the
  poison scan is not among them. Upstream's §7.4a ran against a scratch tree, so it left nothing
  behind.
- **Run attribution in artifacts ([bloom#703](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/703)).**
  #703 is specifically that `batch-ingest-result` does not cross-check
  `RunManifest.pipeline_run_id` against envelope provenance, so results from one run can be ingested
  under another's manifest. Separately observed, and making that cross-check moot rather than merely
  absent: `provenance.pipeline_run_id`, `argo_workflow_uid`, `argo_node_id` and `worker_request_id`
  are empty in live `result.json` envelopes (sleap-roots-pipeline#70's 2026-09-15 envelope reading)
  — the `Provenance` contract defines them and nothing populates them, so both sides of the intended
  cross-check are blank.
- **Shared-path manifest scoping
  ([sleap-roots-pipeline#71](https://github.com/talmolab/sleap-roots-pipeline/issues/71)).**
  Confirmed structurally from this repo: the vendored Workflow hardcodes the three `a4_poc` paths
  and `spec.arguments.parameters` contains only `scan-ids` — there is **no path parameter**, so
  every prod and staging run shares one directory. This is by design and **per-run path isolation
  is explicitly ruled out** by both #71 and #37 (it would break the skip-if-done dedup and the
  batch oracle); the fix shape is per-run *manifest identity* with shared artifacts. Cross-repo.
- **Adding `argo lint` to salk-bloom CI.** The offline lint recipe is used here as a manual
  verification step (`tasks.md` 5.4). Wiring it into CI would mean installing `argo` and fetching
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
- **Affected code:** `services/workflows/vendored/sleap-roots-pipeline.yaml` and
  `SLEAP_ROOTS_PIPELINE_REF` (the vendoring); `services/workflows/k8s_client.py` (the docstring
  correction **and** this change's one production edit, `read_text(encoding="utf-8")`);
  `scripts/check_vendored_workflow_drift.py` (the same encoding fix on the pin read — so the fix
  lands in **three** places, not two); `scripts/check_registered_templates.py` (new — makes
  `tasks.md` 1.1's pre-merge gate reproducible instead of prose);
  <!-- Annotation, bloom#879 (2026-09-22): "reproducible" overstated it. The script shipped with no
       test coverage, and its pinned-upstream premise made it report DRIFT on all five correct
       templates from 2026-09-17, which corrupted one downstream verification record. Renamed to
       scripts/check_template_contract.py, rewritten against the vendored Workflow's contract, and
       now covered by tests/unit/test_check_template_contract.py. Note also that nothing automated
       runs it: it is an operator gate in per-change task lists, not a CI job. -->

  `services/workflows/tests/test_k8s_client.py` and `tests/test_status_poller.py` (tests);
  `services/workflows/README.md` (consumer guidance, docs only); **`.pre-commit-config.yaml`**.
  That last one is easy to miss when reverting and has teeth: it excludes the vendored directory
  from `end-of-file-fixer`, which otherwise appends a newline (10,833 → 10,834 bytes, measured) and
  flips the blocking drift check to "the vendored copy was hand-edited without bumping the pin" on
  a file nobody edited. A revert that drops it re-arms that trap.
- **Deploy semantics — the risky part of this change, not the diff.** Merging to `staging` **is**
  the deploy; there is no separate release step for the dispatch path. prod and staging
  deliberately share the `runai-busch-lab` namespace, disambiguated only by the
  `WORKFLOWS_K8S_ENV_LABEL` stamped on each submitted Workflow. `staging` → `main` promotion is the
  production cutover for this DAG; until it happens production keeps dispatching the four-task DAG
  and gets none of #56's fix, while already running the three new image pins applied to the
  templates on 2026-09-16.
- **Ordering constraint and its true blast radius — and it does NOT surface at submit.** The gate
  template must be registered before any vendored five-task DAG ships. It is tempting to assume an
  unresolvable `templateRef` fails the submission; **it does not.** Measured 2026-09-16 with a
  server-side dry-run against `runai-busch-lab`: a `Workflow` naming a nonexistent
  `WorkflowTemplate` is **accepted** by the Kubernetes API server (`created (server dry run)`, exit
  0). Template resolution is the Argo *controller's* job, and this worker deliberately POSTs to the
  raw K8s API rather than the Argo Server, which is what would validate at submit time.

  So the real failure path is: `submit_workflow` succeeds → `complete_batch` records the name and
  **deletes** the queue message → the controller errors the Workflow → the poller's rollup sees
  `Error` and concludes `'failed'` → `_reconcile_unresolved_scans` marks every still-`queued` scan
  `failed`. Every batch dispatched in the window is still **permanently and unrecoverably failed**
  across prod and staging simultaneously, with no requeue. But three things differ from the
  "fails at submit" story, and they change the runbook: there is **no dead-lettered message** to
  find, `error_message` never carries the cause, and the only diagnostic — the controller's message
  on the Workflow object — is TTL-GC'd after `WORKFLOWS_K8S_TTL_SECONDS` (default 3600). The
  failure also surfaces a poll cycle later rather than immediately.

  **This makes `tasks.md` 1.1 the only real guard**, since submission cannot catch it. Verified
  satisfied 2026-09-16; 1.1 re-checks at merge and 7.6 is the rollback.
- **Risk: the change's fail-safe behaviour lives in an object this repo neither vendors nor
  drift-checks.** The gate template carries `timeout: 600s` — which converts "gate pod stuck
  `Pending` ⇒ Workflow hangs forever ⇒ Bloom's poller never resolves the run" into a bounded
  failure — plus a `retryStrategy` and explicit `resources` to avoid BestEffort eviction. After
  this change the DAG has a mandatory fifth pod *between "all work committed" and "Workflow
  terminal"*. There **is** a mechanism to confirm those guards: `tasks.md` 1.1 diffs the registered
  object's own YAML against the pinned upstream template, which covers the inner template name, the
  declared inputs, the timeout, the retryStrategy, the resources and the image pin in one command.
  See also sleap-roots-pipeline#72 under Non-Goals, which this diff does *not* fix — it is about the
  tag-pinning policy, not drift from the pin.
- **Pre-existing defect this change's promotion step would activate: prod-dispatched Workflows
  mount *staging* credentials.** The vendored file hardcodes
  `secretName: genericsecret-bloom-staging-pipeline-credentials`, and `build_workflow_body`'s four
  overrides do not include it — prod and staging share `runai-busch-lab`, separated only by a
  metadata label that steers nothing. So a production-dispatched run's write-back pod authenticates
  against **staging** Supabase: its `cyl_pipeline_run_scans` rows never match, and its envelopes
  resolve `image_ids` against staging's `cyl_images`. Nothing here causes this and it does not
  affect the staging merge, but `staging → main` is exactly what makes prod runs execute through to
  write-back. Filed as a follow-up (bloom#863) and **must block that promotion**. Note the
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
