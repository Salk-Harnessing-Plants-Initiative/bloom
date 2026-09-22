## Why

`scripts/check_registered_templates.py` (added by #866) compares the five registered Argo
`WorkflowTemplate`s against upstream's template files **at the SHA pinned in
`services/workflows/vendored/SLEAP_ROOTS_PIPELINE_REF`**. That premise is wrong. The vendored file is
the `Workflow`; the templates resolve by `templateRef` at submit time precisely so they can advance
independently of it. Pinning them to the `Workflow`'s SHA asserts an invariant that does not exist, so
the script reports DRIFT on all five after any legitimate template update — indefinitely, until
someone re-vendors for unrelated reasons.

Measured live 2026-09-21 against `runai-busch-lab`, same cluster, minutes apart:

| Comparator | Compares against | Result |
| --- | --- | --- |
| bloom `scripts/check_registered_templates.py` | upstream files fetched at pinned `310aae63` | **DRIFT ×5, exit 1** |
| upstream `scripts/check_cluster_drift.sh` | the template files in its own local checkout | **IN SYNC ×5, exit 0** |

Upstream's comparator compares the cluster against the working tree of whatever checkout it is run
from — it neither fetches nor resolves `origin/main`, and does not assert the checkout is clean. The
run above was from `C:\repos\sleap-roots-pipeline`, clean, at `04c2fc1c` on `main`. Recorded because a
verdict from a dirty or stale checkout would be unreproducible in exactly the way this change is about.

Every one of the five diffs is an image tag, an image digest, or a digest env var. The structural
contract bloom actually depends on is fully intact: all five inner `template:` names match, and the
exit gate declares exactly the three parameters the vendored `Workflow` passes it. The three changes
being reported as drift were all correct — bloom#871's `bloomctl` bump (srp#79) and the
`SRP_PREDICT_CONTAINER_DIGEST`/`SRT_TRAITS_CONTAINER_DIGEST` injection that fixed srp#70 (srp#78).

This matters more than a noisy check. A wrong inner `template:` name cannot fail at submit — the K8s
API server accepts a `Workflow` naming a nonexistent template, because resolution is the Argo
controller's job and the dispatch worker POSTs to the raw K8s API. The failure surfaces a poll cycle
later as an `Error` Workflow with every scan in the batch marked failed, no requeue, no dead letter,
and the diagnostic TTL-GC'd within the hour. A gate that reports DRIFT on a correct cluster trains an
operator to wave DRIFT through, which is what it was written to prevent.

**It has already produced one false record.** `fix-cyl-redelivery-blob-collision/tasks.md` task 9.6
reads "verified independently 2026-09-21 via `scripts/check_registered_templates.py`: all five
registered templates report IN SYNC with the pin, exit 0". That record is self-contradicting on its
face: task 9's purpose was to confirm the `bloomctl` bump to `sha-28034f6` had landed in the cluster,
and this comparator reporting "IN SYNC with the pin" would have meant the bump had **not** landed. It
cannot evidence both. Independently, the pin has not moved since #866 set it while the templates
diverged from it on 2026-09-17, so a 2026-09-21 run could only have reported DRIFT. Which comparator
actually ran is not recoverable from the record — which is the point, and why this change also
requires such records to name their comparator. The cry-wolf is not a hypothetical risk to operator
trust; it has already been absorbed once, four days in.

## What Changes

- **Re-base the comparator on the contract bloom owns.** It SHALL derive its expectations from
  `services/workflows/vendored/sleap-roots-pipeline.yaml`, resolving the DAG through `spec.entrypoint`,
  and assert six things per task — each independently able to produce the submit-accepted,
  controller-errored, whole-batch-failed outcome above:
  1. the referenced `WorkflowTemplate` exists;
  2. it declares an inner template of the referenced name;
  3. every parameter the task passes is declared by that inner template;
  4. every input that template requires without a `default` is supplied — **the direction that
     actually fires.** Only `exit-gate` passes parameters at all, and its three inputs are declared
     with no defaults, so asserting only (3) is vacuous for four of the five templates while upstream
     adding a required input goes unchecked. That failure lands at the DAG's only leaf, *after*
     write-back has committed trait rows;
  5. every `volumeMounts[].name` is declared in the `Workflow`'s `spec.volumes` — four templates mount
     `images-input-dir`, `predictions-output-dir`, `traits-output-dir` and `bloom-credentials` by
     string, and a mismatch fails at pod creation, not at submit;
  6. every `{{workflow.parameters.<name>}}` a template references is declared by the `Workflow` — the
     images-downloader template consumes `scan-ids` this way.
- **Stop fetching upstream.** `RAW_URL`, the `SLEAP_ROOTS_PIPELINE_REF` coupling, and the whole
  server-default normalisation surface (`_strip_server_defaults`, CPU canonicalisation) are removed —
  with no blob diff there is nothing left to normalise.
- **Stop treating image pins as this repo's invariant,** but assert the one image property that is
  self-referential and so cannot cry wolf: a container-digest env var naming its own image must equal
  the digest on that container's `image`. Those values become the provenance of trait rows, so a
  disagreement durably attributes trait data to an image that did not produce it. Everything else about
  images is printed, on every path, and asserted nowhere.
- **Anchor the discovery guard to a committed expected set,** not to a count derived from the same
  parse. The latter is a tautology that can never fire — the exact "failure mode is everything is fine"
  this change exists to remove.
- **Reserve exit 2 for "could not check", properly.** `_fetch_live` currently returns `None` on *any*
  non-zero `kubectl` exit, so an unreachable cluster is reported as `NOT REGISTERED` with exit 1. An
  up-front reachability probe alone does not fix this: per-object failures must be classified, so only
  a genuine `NotFound` reads as a missing template and a transient error, permission denial, absent
  `kubectl`, timeout, or non-`WorkflowTemplate` output all exit 2. A missing or unparseable vendored
  file exits 2 as well, rather than colliding with the violation code via an uncaught traceback.
- **Add the tests it never had,** run without cluster or network access, plus a `--workflow` path
  override and a `check_contract(...) -> int` seam mirroring `check_drift(vendored, ref)` in the
  sibling script — without which the tests have nothing to grip and the negative control cannot run.
- **Correct the records this defect corrupted** (see tasks 4.1–4.2).

### Divergence from bloom#879's suggested fix

Recorded explicitly because #879 is auto-closed by this merge and its suggested fix is not what landed:

- #879 proposes comparing against upstream `main`'s template files so structural drift still surfaces,
  asserting `timeout`, `retryStrategy` and `resources`. **Not done.** Those fields are absent from
  bloom's vendored `Workflow`, so asserting them needs an upstream reference and reinstates the flawed
  invariant for the remaining fields — the same bug with a smaller blast radius. None can produce the
  silent submit-accept failure, and staleness is upstream's comparator's question.
- #879 says to keep the existing server-default normalisation. **Removed,** because the blob diff it
  served is gone. Its substance was correct and is not being repudiated.
- #879 states the srp#78 digests "also addresses the tag-mutability half of srp#72". **That is
  inaccurate** and worth correcting on the issue: srp#72 is exclusively about the exit gate, and the
  srp#78 digests went to predictor and trait-extractor. srp#72 remains **open and unmitigated from
  bloom's side.** This change does not fix it; it prints the exit gate's tag-pinned-plus-`IfNotPresent`
  state as an advisory naming srp#72. The old comparator could not detect a tag re-push either, so
  nothing is lost here — but nothing is gained without that advisory.

### Known gap this change does not close

Three of the five templates run **bloom's own** image, `bloomctl:sha-28034f6` (images-downloader,
write-back, exit-gate). Nothing verifies that such a tag resolves to a bloom commit carrying the
behaviour bloom depends on — archived task 1.1 records that images-downloader must run a `bloomctl`
containing PR #830 or exit `3` is never emitted at all, in which case every check passes and partial
failures still fail whole runs. Spot-checked at authoring: `28034f6` does contain #830's
`ctx.exit(0 if result.ok else 3)`. Not asserted here, and filed as a follow-up rather than left
implicit. Note also that the upstream invariant this proposal leans on for image provenance is enforced
by `sleap-roots-pipeline/scripts/check_manifests.py`, **manually** — that repo has no `.github/` and no
CI at all — so "upstream checks it" means "a human runs a second script", not "a job enforces it".

## Impact

- Affected specs: `cyl-pipeline-dispatch` (two ADDED requirements; the existing CI vendored-drift
  requirement at `spec.md` is a different check and is untouched)
- Affected code: `scripts/check_registered_templates.py`,
  `tests/unit/test_check_registered_templates.py` (new)
- Affected records: `openspec/changes/fix-cyl-redelivery-blob-collision/tasks.md` (task 9.6),
  `openspec/changes/archive/2026-09-21-vendor-five-task-pipeline-dag/{tasks.md,proposal.md}` (the
  annotations in tasks 4.1–4.2)
- Cross-repo: one follow-up PR in `talmolab/sleap-roots-pipeline` recording the observation in
  `docs/bloom-integration/roadmap.md` item A4 (task 5.7)
- Follow-up issues to file, not fixed here: the `bloomctl` tag-to-commit provenance gap above; and a
  standing reference for the `_fetch_live` exit-code conflation, which is a real bug adjacent to but
  not part of #879 and would otherwise be buried by #879's auto-close
- Not affected: the dispatch worker, the vendored `Workflow` itself, and the registered templates —
  this change touches no runtime path and registers nothing in the cluster. It does not, however, leave
  *no* data consequence to bound: `cyl-pipeline-status-polling`'s spec already records that a batch can
  reach `'failed'` with `done_count > 0`, and a controller-side resolution failure at the exit gate is
  another route into that state. Out of scope to fix, in scope to name.
- Existing CI coverage this does **not** duplicate: `services/workflows/tests/test_k8s_client.py`'s
  `_EXPECTED_DAG` already asserts the vendored `Workflow`'s five `(task, templateRef.name,
  templateRef.template)` triples without a cluster. This comparator's distinct value is the
  **cluster-side** registration, which no CI job can see.
- Closes: bloom#879
