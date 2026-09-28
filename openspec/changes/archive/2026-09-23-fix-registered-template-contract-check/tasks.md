## 0. Pre-flight — the baseline, recorded before anything changed

- [x] 0.1 **DONE 2026-09-21.** Both comparators run against `runai-busch-lab` minutes apart, verbatim
      below rather than summarised, because a summarised verdict is what made task 9.6 unattributable.

      bloom's, from this worktree at `dfab7c6f`, pin `310aae63`:
      ```
      $ python3 scripts/check_registered_templates.py
      sleap-roots-images-downloader-template     DRIFT
          -    "image": "ghcr.io/salk-harnessing-plants-initiative/bloomctl:sha-0614889",
          +    "image": "ghcr.io/salk-harnessing-plants-initiative/bloomctl:sha-28034f6",
      sleap-roots-predictor-template             DRIFT
          +      "name": "SRP_PREDICT_CONTAINER_DIGEST",
          +      "value": "sha256:4d4064c6ac8dadc1bedcba594c74f0d7c4b9d907ee9001999d34e664317a4060"
          -    "image": ".../sleap-roots-predict:sha-e025e309...",
          +    "image": ".../sleap-roots-predict:sha-e025e309...@sha256:4d4064c6...",
      sleap-roots-trait-extractor-template       DRIFT
          +      "name": "SRT_TRAITS_CONTAINER_DIGEST",
          +      "value": "sha256:ab5a1f43a74f2d00e809f2deb0dc886876028cc3028b0fdaf600f408e860f369"
          -    "image": ".../sleap-roots-trait-extractor:sha-689cffb",
          +    "image": ".../sleap-roots-trait-extractor:sha-689cffb@sha256:ab5a1f43...",
      sleap-roots-write-back-template            DRIFT   (same bloomctl bump)
      sleap-roots-exit-gate-template             DRIFT   (same bloomctl bump)
      DRIFT: at least one registered template does not match the pin.
      EXITCODE=1
      ```

      upstream's, from `C:\repos\sleap-roots-pipeline`, **clean working tree, `04c2fc1c` on `main`**
      (recorded because this comparator reads its own working tree, not `origin/main`):
      ```
      $ bash scripts/check_cluster_drift.sh
      IN SYNC         sleap-roots-exit-gate-template
      IN SYNC         sleap-roots-images-downloader-template
      IN SYNC         sleap-roots-predictor-template
      IN SYNC         sleap-roots-trait-extractor-template
      IN SYNC         sleap-roots-write-back-template
      === cluster is IN SYNC with the repo ===
      EXITCODE=0
      ```

      Every bloom diff is an image tag, digest, or digest env var. Structural contract confirmed intact
      by direct inspection: all five inner `template:` names match their `templateRef.template`, and
      `sleap-roots-exit-gate-template` declares exactly `images-downloader-code`, `predictor-code`,
      `trait-extractor-code` — with **no defaults**, which is why contract item 4 matters.

- [x] 0.2 **DONE 2026-09-22.** Three live objects captured and committed under
      `tests/unit/fixtures/` (`live_exit_gate_template.yaml`, `live_images_downloader_template.yaml`,
      `live_predictor_template.yaml`), volatile metadata stripped, capture date in each header.
      **This changed the implementation:** the stored objects carry `inputs: {}` — present but
      **empty**, not absent — on the four templates that declare no inputs, so every accessor
      tolerates absent / `{}` / `{parameters: []}` alike. A hand-built fixture would have missed that.
      `exit-gate` also confirmed to declare its three inputs with no `default` and no `value`.

## 1. Rename and seam first — the tests have nothing to grip without them

- [x] 1.0 **DONE** (`06f31a35`) — `git mv scripts/check_registered_templates.py
      scripts/check_template_contract.py` as a standalone commit, no content change, so the rewrite's
      diff stays reviewable. `DRIFT` purged from the output in 3.10.
- [x] 1.1 **DONE** — `--workflow PATH` added (default: the committed vendored `Workflow`), and
      `check_contract(workflow_path, namespace, expected_refs=None) -> int` returns an exit code
      rather than calling `sys.exit`. Mirrors `check_drift(vendored_path, ref_path) -> int`.
      `expected_refs` is injectable so tests can exercise synthetic DAGs without the production
      constant pre-empting them; production callers never pass it.

## 2. RED — tests before the rewrite

`tests/unit/test_check_template_contract.py`, 53 tests, no cluster and no network. Loaded via
`importlib.util.spec_from_file_location`; `_probe_cluster` and `_fetch_live` patched as named seams;
one test asserts the real `kubectl` argv and timeout because every other test mocks it away.

- [x] 2.1 Fixture builders (`_wf`, `_task`, `_tmpl`, `cluster`) mirroring the real stored shapes.
- [x] 2.2 **The bloom#879 regression guard** —
      `test_differing_image_tags_and_digests_do_not_fail_the_check`, using the five real image values
      observed in 0.1.
      **Honest correction to this task as originally written.** It claimed this test would
      "demonstrate the defect". It does not, and could not: against the old script a fixture-built
      template goes red on `priorityClassName`/`retryStrategy`/`timeout`/`resources` long before the
      image lines matter, so its colour would prove nothing about image pins. The demonstration of the
      defect is the **live** baseline in 0.1 — real cluster, real objects, five real diffs, all of them
      image-only. This test's job is narrower and still worth having: it prevents the defect's return.
      Those are two different jobs and the original task conflated them.
- [x] 2.3 Missing `WorkflowTemplate` → exit 1, names the object.
- [x] 2.4 Wrong inner template name → exit 1, names both.
      **The original task probed the wrong layer**, and the implementation caught it. It mutated the
      *vendored Workflow*'s `templateRef.template`, which is a bloom-side edit and correctly trips the
      expected-set guard at exit 2, never reaching the inner-name assertion. A wrong inner name is a
      **cluster-side** defect, so the test now leaves the vendored Workflow correct and registers an
      object whose inner template is named differently. Both layers are covered:
      `test_wrong_inner_template_name_is_a_violation` (cluster side, exit 1) and
      `test_discovered_refs_not_equal_to_the_expected_set_is_unavailable` (repo side, exit 2).
- [x] 2.5 Parameter passed but not declared → exit 1.
- [x] 2.6 Required input not supplied → exit 1. Plus
      `test_required_input_may_be_supplied_by_a_workflow_level_argument`.
- [x] 2.7 Declared input carrying a `default` need not be passed → exit 0.
- [x] 2.8 `volumeMounts[].name` absent from `spec.volumes` → exit 1.
- [x] 2.9 Undeclared `{{workflow.parameters.X}}` → exit 1; declared one → exit 0.
- [x] 2.10 Digest env var disagreeing with its own image → exit 1, names both digests.
- [x] 2.11 Digest env var agreeing, on an image recorded nowhere in this repo → exit 0.
- [x] 2.12 The false-positive trap: a workflow-level global no template declares → exit 0.
- [x] 2.13 The 4-of-5 shape, parametrised over `inputs` ∈ {absent, `{}`, `{parameters: []}`} ×
      `arguments` ∈ {absent, empty} → exit 0 in all six combinations.
- [x] 2.14 Unreachable cluster → exit 2, and nothing reported as missing.
- [x] 2.15 Non-`NotFound` `kubectl` failure → exit 2; `FileNotFoundError` → exit 2;
      `TimeoutExpired` → exit 2.
- [x] 2.16 `kubectl` exit 0 with non-`WorkflowTemplate` output → exit 2.
- [x] 2.17 Vendored `Workflow` absent / unparseable / non-mapping / no entrypoint / entrypoint
      unmatched → exit 2 in all five.
- [x] 2.18 Discovered ref set ≠ expected set → exit 2, naming both. Plus an empty DAG → exit 2.
- [x] 2.19 Violation + could-not-check in one run → exit 2, with the violation still printed.
- [x] 2.20 Two different violation kinds in one run → both named.
- [x] 2.21 Image references printed on the violation path as well as the success path.
- [x] 2.22 `EXIT_OK/EXIT_VIOLATION/EXIT_UNAVAILABLE == 0/1/2` and all distinct.
- [x] 2.23 Duplicate `templateRef`s checked per task, not keyed by template name.
- [x] 2.24 No network: asserts `urllib`/`requests`/`httpx`/`raw.githubusercontent.com` appear nowhere
      in the source — the import must be **gone**, not merely unused.
      Added beyond the plan: `test_expected_refs_constant_matches_the_real_vendored_workflow`, which
      reads the real vendored file and fails if it and `EXPECTED_TEMPLATE_REFS` ever disagree; and
      five tests parsing the captured live objects, so the parser is pinned against the shape the API
      server actually stores rather than only against builders.
- [x] 2.25 **RED confirmed before implementation: 14 failed, 38 errored, 1 passed.**
      The one pass is worth recording rather than hiding: `test_fetch_live_returns_none_only_for_a_
      genuine_notfound` passed against the old script *for the wrong reason* — the old `_fetch_live`
      returned `None` on every non-zero exit, so it satisfied the NotFound case by accident. Its
      companion, `test_fetch_live_raises_for_a_non_notfound_kubectl_error`, was RED, and that pair is
      what actually pins the distinction.

## 3. GREEN — rewrite the comparator

- [x] 3.1 DAG resolved through `spec.entrypoint`, with
      `test_dag_is_resolved_through_the_entrypoint_not_templates_zero` pinning it.
- [x] 3.2 Contract extracted: per-task refs and `arguments.parameters`, kept separate from
      workflow-level `spec.arguments.parameters` and `spec.volumes`.
- [x] 3.3 Expected-set guard by set equality, before any verdict.
- [x] 3.4 Reachability probed once; a `Forbidden` on the list is treated as reachable, since a
      kubeconfig may hold `get` without `list`.
- [x] 3.5 Six assertions per template, all violations collected; `kubectl` failures classified;
      `timeout=30s` on every invocation.
- [x] 3.6 Tolerant accessors throughout (`(x.get("inputs") or {})`), per 0.2's finding.
- [x] 3.7 Image references printed on every path, plus the srp#72 advisory where a template is
      tag-pinned with `imagePullPolicy: IfNotPresent`. Verified live: it fires on `exit-gate` only.
- [x] 3.8 Could-not-check outranks a violation.
- [x] 3.9 `RAW_URL`, `REF_FILE`, `_fetch_pinned`, `_strip_server_defaults`, `_DEFAULTED_EMPTY` and the
      `difflib`/`json`/`urllib.error`/`urllib.request` imports all deleted.
- [x] 3.10 Docstring rewritten, including the five-way comparison table showing which check owns which
      question, and pointers to upstream's `check_cluster_drift.sh` (staleness) and
      `check_manifests.py` (repo-internal image invariants) — both manual, since that repo has no CI.
- [x] 3.11 **53 passed in 0.44s** (`uv run --extra test pytest
      tests/unit/test_check_template_contract.py -q`). Scoped deliberately: a bare
      `pytest tests/unit/` cannot be green on this workstation, where
      `tests/unit/test_weekly_backup.py` fails collection with
      `AttributeError: module 'os' has no attribute 'geteuid'` — pre-existing and unrelated.
      Full-suite evidence comes from CI. `uvx ruff@0.9.9 check` clean on both files (note `scripts/`
      and `tests/` are outside both CI's and pre-commit's ruff scope, so this was run by hand).

## 4. Live verification — the proof the crying wolf stopped

- [x] 4.1 **DONE 2026-09-22, exit 0** against `runai-busch-lab`, observed verbatim:
      ```
      Checking runai-busch-lab against .../services/workflows/vendored/sleap-roots-pipeline.yaml

        sleap-roots-images-downloader-template   template=images-downloader image=...bloomctl:sha-28034f6
        sleap-roots-predictor-template           template=predictor         image=...sleap-roots-predict:sha-e025e309...@sha256:4d4064c6...
        sleap-roots-trait-extractor-template     template=trait-extractor   image=...sleap-roots-trait-extractor:sha-689cffb@sha256:ab5a1f43...
        sleap-roots-write-back-template          template=write-back        image=...bloomctl:sha-28034f6
        sleap-roots-exit-gate-template           template=exit-gate         image=...bloomctl:sha-28034f6
                                                 advisory: tag-pinned with IfNotPresent (talmolab/sleap-roots-pipeline#72)

      OK: every registered template satisfies the vendored Workflow's contract.
      EXITCODE=0
      ```
      Same cluster and same commit that reported DRIFT ×5 exit 1 in 0.1.
- [x] 4.2 **Negative controls against the real cluster**, via broken *copies* of the vendored
      `Workflow` (never the vendored file, never mutating the cluster).

      **An earlier version of this task recorded all five as exit 1, and two of those rows were
      not reproducible.** They were produced by a harness that injected a matching
      `expected_refs`, so the discovery guard could not pre-empt the case under test — and the
      record did not say so. Anyone re-running the CLI as shipped would have got a different
      answer. That is the same defect this change retracts in task 9.6 of
      `fix-cyl-redelivery-blob-collision`, committed by this change's own author, and caught by
      the PR review rather than by me. Re-run 2026-09-22 through the **production path**
      (`--workflow <copy> --namespace runai-busch-lab`, no injection), recording what happened:

      | case | observed | exit |
      |---|---|---|
      | volume removed from `spec.volumes` | `UNDECLARED VOLUME … mounts 'traits-output-dir'` ×2 (trait-extractor, write-back) | **1** |
      | global removed from `spec.arguments` | `UNRESOLVABLE REF … references {{workflow.parameters.scan-ids}}` | **1** |
      | exit-gate params truncated | `MISSING PARAMETER … requires 'predictor-code' (no default)` ×2 | **1** |
      | nonexistent `templateRef.name` | `CHECK FAILED the vendored Workflow's templateRefs are not the expected set` | **2** |
      | wrong inner `template:` | `CHECK FAILED the vendored Workflow's templateRefs are not the expected set` | **2** |
      | inline task with no `templateRef` | `CHECK FAILED task 'notify' has no resolvable templateRef` | **2** |

      The last three are **correct behaviour, not a regression**: all three edit the vendored
      side, and a vendored-side ref change is a repo configuration fault the expected-set guard
      is there to catch, not a cluster contract violation. The cluster-side equivalents — a
      genuinely absent object and a genuinely wrong inner name — are covered by 4.3 and by
      `test_missing_workflow_template_is_a_violation` /
      `test_wrong_inner_template_name_is_a_violation`, which perturb the cluster rather than the
      repo. So three of the six contract assertions are live-proven end to end through the
      production path; the other three are proven against real `kubectl` only at the
      classification step (4.3) plus unit tests. Stated precisely rather than rounded up.
- [x] 4.3 The `NotFound` classification confirmed against real `kubectl`, which is the one assumption
      unit tests cannot reach: `kubectl get workflowtemplate sleap-roots-does-not-exist` emits
      `Error from server (NotFound): workflowtemplates.argoproj.io "…" not found`, so the classifier
      returns "absent" (→ exit 1) rather than raising (→ exit 2).
- [x] 4.4 Fixture shape confirmed against reality by construction — the three fixtures *are* live
      captures from 2026-09-22, and five tests assert the parser finds `inputs.parameters`,
      `volumeMounts`, `templates[].name` and the digest env vars in them.
      **Not live-verified, and deliberately left so:** the digest-disagreement assertion (contract
      item 7). Producing that condition on the real cluster would require registering a deliberately
      inconsistent template, i.e. mutating a shared namespace that production also uses. It is covered
      by unit tests both ways (disagreeing → exit 1, agreeing → exit 0) and by
      `test_real_predictor_digest_env_agrees_with_its_own_image` over the captured object. Five of the
      six task-level assertions are live-proven; this one is not, and that gap is stated rather than
      implied.

## 5. Correct the records this defect already corrupted

- [x] 5.1 `fix-cyl-redelivery-blob-collision/tasks.md` task 9.6 rewritten: the 2026-09-21 evidence is
      marked **retracted as unreproducible**, on the self-contradiction (task 9 was confirming the
      bump had landed; "IN SYNC with the pin" would have meant it had not) and on the pin not having
      moved since #866. No attribution guessed — an earlier draft of this proposal inferred "upstream's
      script was probably run" from the wording, which the wording does not support, since bloom's own
      script also prints `IN SYNC`. The substantive conclusion is re-established from the dated,
      comparator-named runs in 0.1 and 4.1. Cross-references bloom#780.
- [x] 5.2 Archived `2026-09-21-vendor-five-task-pipeline-dag` annotated in **three** places —
      `tasks.md` task 1.1 and both `proposal.md` claims — each noting bloom#879 and the new
      invocation, with the historical text left intact. Each annotation states that the
      **2026-09-16 result was sound at the time** (nothing changed in the templates between the pin
      and 2026-09-17) so a later reader does not over-correct a valid record, and that 1.1's
      "record all three bloomctl image references" instruction remains live because the bloomctl
      provenance gap is filed rather than closed.

## 6. Validation and merge

- [x] 6.1 `openspec validate fix-registered-template-contract-check --strict` passes.
- [x] 6.2 Re-confirmed: no unarchived change carries either ADDED requirement heading, and no
      unarchived change touches `cyl-pipeline-dispatch` at all. Both deltas are ADDED, not MODIFIED,
      so the archive-ordering hazard does not apply. **Re-check immediately before merge**, since
      other changes land meanwhile.
- [x] 6.3 **`/review-pr` run 2026-09-22 on PR #892; scores 7.5 / 7.5 / 8 / 7 / 6.** It found nine
      real defects, every one of them an instance of a fault this change was written to abolish —
      either a non-violation reported with the violation code, or an assertion whose failure mode
      was a silent pass. All fixed in this PR, with live re-verification:
      1. **A DAG task with no `templateRef`** (an ordinary inline step) was invisible to the
         expected-set guard, reached `_fetch_live(None, …)`, and died on an uncaught `TypeError` —
         process exit 1, the violation code. Now exit 2, naming the task.
      2. **`required_inputs(...) - passed - globals_` was unsound.** Argo binds
         `spec.arguments.parameters` to the *entrypoint* template's inputs; a `templateRef`'d DAG
         task must supply its callee's non-defaulted inputs itself. Subtracting the globals
         exempted any required input whose name merely collided with a global — silently disabling
         the assertion the design argues matters most. Inert today, removed anyway; the test that
         pinned the wrong behaviour is inverted.
      3. **`kubectl` exit 0 with an empty body** parsed to `None`, the same sentinel as `NotFound`,
         so it reported `NOT REGISTERED` + exit 1. Now exit 2.
      4. **A typo'd `--namespace`** passed the probe (a list against a nonexistent namespace exits
         0) and then produced five fabricated `NOT REGISTERED` violations. The probe now keeps its
         listing and refuses an empty one. Verified live: `--namespace runai-typo-lab` → exit 2.
      5. **argv injection.** A `templateRef.name` beginning with `-` reached `kubectl` as a flag,
         and a name without a `template` was invisible to the discovery guard. **Verified live**:
         `kubectl get workflowtemplate --server=http://127.0.0.1:9/ …` was honoured and dialled,
         so `--kubeconfig=`/`--token=` could exfiltrate the operator's bearer token from a file
         vendored out of another GitHub org. Fixed with a `--` terminator and a DNS-1123 check.
         Also measured: `get workflowtemplate -- <obj> -n ns -o yaml` silently *ignores* `-o yaml`,
         so the flags must precede the terminator — the first fix attempt was wrong and the live
         check caught it.
      6. **`script:` / `initContainers` / `sidecars` templates** made the volume, image and digest
         assertions vacuous, reporting OK on a template with an undeclared mount. All accessors now
         cover them, and a template with no inspectable container is exit 2, not a silent pass.
      7. **`required_inputs` ignored `valueFrom`**, so an input drawing from a `configMapKeyRef`
         was a false `MISSING PARAMETER`.
      8. **`digest_disagreements` had three false-positive paths** — a `valueFrom` digest read as
         `""`, a tag-pinned image carrying another component's digest, and two digest vars on one
         container. Narrowed to the case it can actually justify.
      9. **A malformed `spec.volumes`/`spec.arguments`** in the vendored file produced up to nine
         cluster contract violations instead of exit 2.
      Two test defects also fixed: `assert "predictor" in err` is subsumed by the object name it
      appears in and could never fail independently, and
      `test_unavailable_outranks_a_violation_in_the_same_run` perturbed the vendored side, so the
      guard fired first and the assertion was satisfied by the guard's own output — it was not
      testing precedence at all. Strengthening the assertion is what exposed it.
      Test count 53 → 74.
- [x] 6.4 **Green, though the `/pre-merge` skill itself was not invoked** — the checks it wraps were
      run individually and are recorded above: `uvx ruff@0.9.9` clean on both files (3.11),
      `pytest tests/unit/test_check_template_contract.py` 74 passed (3.11), `services/workflows`
      657 passed / 1 skipped, the full `tests/unit/` suite compared against an unmodified
      `origin/staging` worktree (49 failed / 1139 passed vs 49 failed / 1086 passed — identical
      failure count, passes up by exactly the 53 then-added tests, all 49 pre-existing Windows
      environment failures), and `openspec validate --strict` valid. CI then ran the authoritative
      versions: **33 SUCCESS, 2 SKIPPED, 0 failures**, twice — once on the fix commit and again on
      the `staging` merge commit. Recorded this way rather than ticked as "/pre-merge run", because
      it was not.
- [x] 6.5 **MERGED 2026-09-23T18:33:13Z** as `378b5456` into `staging` (PR #892), approved by
      @blm3886 with no change requests. Branch was updated from `staging` first — the ruleset sets
      `strict_required_status_checks_policy: true`, so BEHIND genuinely blocks — and the merge was
      clean, with the PR's 13-file diff and its "No schema changes" declaration both unaffected.
      **bloom#879 did NOT auto-close, and will not until the `staging`→`main` promotion.** GitHub's
      closing keywords fire only on merge into the default branch, which is `main`. Several earlier
      notes in this file and in the PR body asserted it would close on merge; that was wrong.
      Benign in effect — the issue stays open as a live pointer to the recorded gaps — but it means
      the "auto-close buries the follow-ups" argument does not apply to this merge.
- [x] 6.6 **Provenance gap filed as talmolab/sleap-roots-contracts#40** (2026-09-22), in the repo
      that owns the `Provenance` model rather than in bloom — an earlier draft of this task had that
      backwards. Investigating it to write the issue changed the ask: the obvious fix ("have
      `write-back` stamp its own digest") is incompatible with `ingest-result` sending the *original
      parsed JSON* so the producer's `idempotency_key` survives byte-exactly, and `exit-gate` runs
      after the provenance record is committed, so it could never appear there under any design.
      #40 therefore poses the design question — should the contract carry the *ingesting* agent's
      identity at all, or does that belong on `cyl_trait_sources` where bloom owns the column —
      rather than prescribing a field. My stated read there is that it should be declined and
      recorded bloom-side, but the model's owner makes that call.
      **The `_fetch_live` exit-code conflation was deliberately not filed.** It is already fixed in
      this PR; an issue would be closed on arrival, and its only value was a paper trail. Decided
      with the user 2026-09-22. The fix and its reasoning are in this change's §6.3 and in the PR
      description, which is where someone looking for it would land.
      **Not filed, deliberately:** whether `images-downloader`/`exit-gate` identity belongs on the
      run record. That is a `cyl_pipeline_runs` design question, adjacent to the unresolved queue
      thread on bloom#404, and not one to shape unilaterally — raise with Benfica instead.
- [x] 6.7 **DONE 2026-09-22.** Review posted to PR #892 (verdict COMMENT — GitHub does not allow
      approving one's own PR), and the evidence comment posted to bloom#879
      (`issues/879#issuecomment-5784243915`): the 0.1/4.1 measurements, the divergence from #879's
      suggested fix, the nine review findings, the known gaps, and the correction that the srp#78
      digests did **not** address srp#72's tag-mutability half. Both authorized by the user for
      these specific posts; re-confirm before posting anything further.
- [ ] 6.8 **NOT DONE — deliberately left unticked, archived in this state.** Record the observation
      in `sleap-roots-pipeline` `docs/bloom-integration/roadmap.md` item A4: two comparators, one
      cluster, opposite verdicts, and the coverage map showing that nothing else compares the
      vendored `Workflow` to the cluster. It is a separate PR in a sibling repo, so it cannot land
      with this change, and archiving is not worth delaying for it — but a quietly ticked box would
      be worse than an open one. Consider srp#58 (the origin of upstream's comparator, closed) as a
      more durable home than the roadmap, since that is where someone asking "why are there two of
      these?" would look.
