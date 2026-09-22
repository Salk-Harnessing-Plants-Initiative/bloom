## Context

Two comparators exist for the same five `WorkflowTemplate`s in `runai-busch-lab`:

- `sleap-roots-pipeline/scripts/check_cluster_drift.sh` — cluster vs **the template files in its own
  local checkout**. Answers "is the cluster stale relative to upstream's repo?"
- `salk-bloom/scripts/check_registered_templates.py` — cluster vs **upstream files fetched at bloom's
  vendored pin**. Intended to answer the same question, but the pin it uses is the `Workflow`'s, not
  the templates'.

Upstream hit and fixed the same *class* of defect in srp#73 ("strip registrar-injected labels from the
drift comparison"), found the same way: running a task that invoked the checker for real, immediately
after the first `argo template create` since the checker existed.

The decisive measurement, 2026-09-21, same cluster, minutes apart: bloom's reports DRIFT ×5 exit 1;
upstream's, run from a clean `C:\repos\sleap-roots-pipeline` at `04c2fc1c` on `main`, reports IN SYNC
×5 exit 0. Every bloom diff is an image tag, digest, or digest env var.

A third comparator matters for the image-provenance reasoning below and is easy to overlook:
`sleap-roots-pipeline/scripts/check_manifests.py` asserts the repo-internal invariants that each
producer is digest-pinned and that each digest env var matches its own image pin. It runs only when a
human invokes it — `sleap-roots-pipeline` has no `.github/` directory and no CI at all.

## Goals / Non-Goals

- **Goals:** bloom can verify, from bloom, that the contract its vendored `Workflow` depends on holds
  in the cluster; the check does not fire on correct clusters; its logic is testable without a cluster
  or network; "could not check" is never reported as "drift" or as "fine".
- **Non-Goals:** detecting upstream template staleness; checking image pin freshness against a
  recorded pin; running in CI (the check needs live cluster access — only its logic is CI-tested); any
  change to the dispatch worker or the registered templates.

## Decisions

**Decision: derive the expected contract from the vendored `Workflow`, not from upstream at a SHA.**

The vendored `sleap-roots-pipeline.yaml` declares what bloom depends on: the `templateRef`
`(name, template)` pairs, the parameters passed per task, the workflow-level parameters templates
consume, and the volumes templates mount. Asserting exactly that is well-founded (bloom owns the
file), needs no network, cannot go stale against legitimate upstream template evolution, and is a
genuinely different question from upstream's.

Alternatives considered:

- *Converge bloom's on upstream's design — whole-spec diff against upstream `main` rather than the
  pin, porting `INJECTED_PREFIXES`, `normalise_cpu`'s numeric coercion and the `CHECK FAILED` guard.*
  Rejected: it re-implements upstream's comparator over the network against a moving target, so it is
  neither reproducible nor bloom's question, and it still fails on image pins.
- *Delete bloom's comparator; point the operator gate at upstream's.* Rejected — but **not** for the
  reason first drafted here. The first draft said upstream's comparator would miss "the vendored
  `Workflow` naming a `templateRef` or inner template that does not exist". That defect is in fact
  already caught in CI, without a cluster, by `services/workflows/tests/test_k8s_client.py`'s
  `_EXPECTED_DAG` (its `_dag_tasks` helper also documents why resolving through `spec.entrypoint`
  beats indexing `spec.templates[0]` — a discipline task 2.1 adopts). The real reason to keep a
  bloom-side comparator is the **cluster-side** case neither CI nor upstream's comparator can see:
  upstream renaming an inner template, changing a required input, or its registration diverging from
  its repo, so that bloom's vendored refs go stale while upstream's comparator reports IN SYNC against
  its own files. Deleting bloom's would also make the gate depend on a sibling checkout being present.
- *Keep the pinned-SHA fetch but narrow the comparison to structural fields and demote images.*
  Rejected: fixes today's symptom while retaining the flawed invariant for the remaining fields, so it
  cries wolf again the next time upstream legitimately changes a timeout. Same bug, smaller blast
  radius.

**Decision: image pins are not compared against any recorded value, but self-consistency is asserted.**

Demoting tags and digests wholesale is what bloom#879 asks for, and for the two `talmolab` producer
images it is right: which image they run is upstream's decision. But the blanket phrasing the first
draft used ("image pins are upstream's business") is **false for three of the five templates**, which
run bloom's own `bloomctl`. The assertion that survives that objection without any risk of crying wolf
is self-referential: a container-digest env var naming its own image must equal the digest on that
container's `image`. Both sides move together on a legitimate bump, so it can never reproduce bloom#879.
It is worth asserting rather than printing because those values land in the trait-result envelope's
provenance and are stored without validation, so a disagreement durably attributes trait rows to an
image that did not produce them.

**Decision: `timeout`, `retryStrategy` and `resources` are not asserted.**

Not declared anywhere in bloom's vendored `Workflow`, so asserting them requires an upstream reference
and reintroduces the invariant this change removes. Upstream's comparator covers them against the repo
that owns them. The first draft justified this with the claim that *none* of the omitted fields "can
produce the silent submit-accept-then-controller-error failure." **That claim was wrong** — it
overlooked volume mounts and required inputs, both of which produce exactly that failure and both of
which are derivable from the vendored `Workflow` alone. They are now asserted (contract items 4 and 5),
and the claim is narrowed to the three fields it was actually true of.

**Decision: carry the `CHECK FAILED` principle forward rather than port the mechanism.**

Upstream's guard exists because its normalisation runs in a subshell without `set -e`, so a failure
would empty both sides, `diff -q` would call them identical, and every template would report IN SYNC
with exit 0. Removing the blob diff removes that mechanism — there is nothing left to normalise. The
principle still binds. Its analogue here is discovery, and the first draft got the mechanism wrong:
"fewer `templateRef`s than the `Workflow` declares" compares a parse result against a number derived
from the same parse, which is a tautology that can never fire — reintroducing "failure mode is
everything is fine" in the very clause meant to outlaw it. The guard is now set equality against a
committed expected set of `(name, template)` pairs, on the precedent of `_EXPECTED_DAG`. Equality, not
`>=`: a restructured DAG should fail loud and name both sets rather than pass quietly. Recorded
deliberately, because bloom#778 was a case of loosening such a predicate and reopening a hole.

**Decision: classify per-object `kubectl` failures; do not stop at an up-front probe.**

Separate from bloom#879, `_fetch_live` returns `None` on any non-zero `kubectl` exit, so VPN-down is
reported as `NOT REGISTERED` with the violation code. An up-front reachability probe alone does not fix
it: a mid-run token expiry, transient API error, per-object RBAC denial, absent `kubectl`
(`FileNotFoundError`, the most likely failure on a Windows workstation given `kubectl` lives in WSL),
timeout, or `kubectl` exiting 0 with non-`WorkflowTemplate` output would each still fabricate a
violation. Upstream's script has this same bug (`NOT REGISTERED … drift=1` on any per-object failure),
so copying its shape would import it. Only a genuine `NotFound` reads as a missing template.

**Decision: could-not-check outranks a violation within one run.**

Violations are collected rather than short-circuited, so one run reports everything wrong. When a run
contains both a violation and an unchecked condition, the exit code is 2: a partial check must never be
reportable as a complete verdict. Upstream leaves this to last-write-wins in a loop; stating it
explicitly avoids inheriting that.

**Decision: expose a `--workflow` path and a `check_contract(...) -> int` seam.**

Mirrors `check_vendored_workflow_drift.py`'s `check_drift(vendored_path, ref_path) -> int`, which its
test suite calls directly with `tmp_path` fixtures. Without it the unit tests can only monkeypatch
module constants, and the live negative control — pointing the comparator at a deliberately broken
copy of the vendored `Workflow` — cannot be performed at all without editing the vendored file, which
the task explicitly forbids.

## Risks / Trade-offs

- **Narrower than what it replaces: it no longer notices a stale template.** → Deliberate; that is
  upstream's comparator's job. But the mitigation has a window upstream itself documents: while the
  cluster is intentionally ahead of upstream's repo (the standard pattern when a fix is applied before
  it merges), "the drift check cannot serve as a gate, because an intended divergence is
  indistinguishable from a real one". That is precisely the window in which a wrong template gets
  registered, and in it neither comparator gates anything.
- **Upstream's comparator is credited from one green run.** Exit 0 shows it reported IN SYNC, not that
  it detects staleness. The srp#79 record does show it reporting DRIFT on three templates until #79
  merged, which is the evidence that it discriminates; cited rather than assumed.
- **Post-change blind spots, listed so none is discovered later:** a stale template (by design); a
  conforming template with a wrong `command`/`args`; wrong `timeout`/`retryStrategy`/`resources`; a
  `bloomctl` tag that does not resolve to a commit carrying the exit-3 contract (filed as
  talmolab/sleap-roots-contracts#40 — the deeper version being that `Provenance` has no field for
  any of the three `bloomctl` stages, and that only one of the three could have one, since
  `write-back` would require breaking `ingest-result`'s verbatim passthrough and `exit-gate` runs
  after the record it would appear in is already committed); a registered template whose inner shape this
  comparator cannot inspect at all — now exit 2 rather than a silent pass, but still unverified;
  a **rogue or orphaned registered object**, since this comparator fetches only the five it expects
  and upstream's loops over its own *files*, so neither side enumerates the namespace; a gate whose
  `0|3` allowlist has been widened, which upstream's `check_manifests.py` checks and bloom cannot;
  a template pointed at the wrong image *repository* whose own image and env digests still agree —
  upstream's `check_manifests.py` asserts against that, bloom will not; and a re-pushed tag, which
  neither the old nor the new comparator can see.
- **The expected-set guard fires on a legitimate DAG restructure.** → It fails loud and names both
  sets, and re-vendoring is exactly when someone should re-read this check.
- **Fixtures are mocked, so the suite could be green against a fixture that is wrong in the same way
  the script was.** → The current script's normalisation list was measured against real objects
  ("Measured against all five templates, 2026-09-16"); dropping that discipline is how a rewrite ships
  a second wrong invariant. Mitigated by capturing a real `kubectl get -o yaml` once and using it as
  the fixture shape, with the capture date recorded.

## Migration Plan

No runtime component, schema or cluster object changes; nothing to roll back beyond reverting the
script. The 0/1/2 exit-code contract and the `--namespace` default are preserved, and `--workflow` is
additive with the vendored path as its default.

The script is renamed `check_registered_templates.py` → `check_template_contract.py`, and its output
vocabulary drops `DRIFT` for `OK` / `CONTRACT VIOLATION` / `CHECK FAILED`. That does break the path in
the archived task 1.1 invocation — accepted deliberately, and cheap, because nothing automated
references the script (no CI job, no Makefile target) so the only cost is muscle memory. The
annotation task 5.2 already writes records the new path, so the one place that prints the old
invocation gains a pointer to the new one. The rename is worth that cost: the drift framing is the
defect, and a name and a banner that keep asserting it would keep inviting the comparison bloom has no
standing to make. Anyone reaching for the old name finds the annotation.

## Open Questions

None. The comparator-ownership question that motivated this design is settled under Decisions.
