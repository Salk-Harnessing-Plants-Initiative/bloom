## Context

Two comparators now exist for the same five `WorkflowTemplate`s in `runai-busch-lab`:

- `sleap-roots-pipeline/scripts/check_cluster_drift.sh` — cluster vs **the repo that owns the
  templates**. Answers "is the cluster stale relative to upstream?"
- `salk-bloom/scripts/check_registered_templates.py` — cluster vs **upstream files at bloom's vendored
  pin**. Intended to answer the same question, but the pin it uses is the `Workflow`'s, not the
  templates'.

Upstream hit and fixed the same *class* of defect in srp#73 ("strip registrar-injected labels from the
drift comparison"), found the same way: running a task that invoked the checker for real, immediately
after the first `argo template create` since the checker existed. That script is the more mature of
the two and was read in full before this design was written.

The decisive measurement, 2026-09-21, same cluster, minutes apart: bloom's reports DRIFT ×5 exit 1,
upstream's reports IN SYNC ×5 exit 0. Every bloom diff is an image tag, digest, or digest env var.

## Goals / Non-Goals

- **Goals:** bloom can verify, from bloom, that the contract its vendored `Workflow` depends on holds
  in the cluster; the check does not fire on correct clusters; its logic is testable without a
  cluster; "could not check" is never reported as "drift" or as "fine".
- **Non-Goals:** detecting upstream template staleness (upstream's comparator owns that, against the
  repo that owns the templates); checking image pin freshness; running in CI (the check needs live
  cluster access — only its logic is CI-tested); any change to the dispatch worker or the registered
  templates.

## Decisions

**Decision: derive the expected contract from the vendored `Workflow`, not from upstream at a SHA.**

The vendored `sleap-roots-pipeline.yaml` declares the whole of what bloom depends on and nothing more:
five `templateRef` `name`/`template` pairs, and the parameters passed to each task. Asserting exactly
that is well-founded (bloom owns the file), needs no network, cannot go stale against legitimate
upstream template evolution, and is a genuinely different question from upstream's — which is why
keeping both comparators is not duplication.

Alternatives considered:

- *Converge bloom's on upstream's design — whole-spec diff against upstream `main` rather than the
  pin, porting `INJECTED_PREFIXES`, `normalise_cpu`'s numeric coercion and the `CHECK FAILED` guard.*
  Rejected: it re-implements upstream's comparator over the network against a moving target, so it is
  neither reproducible nor bloom's question, and it still fails on image pins that are upstream's
  business.
- *Delete bloom's comparator; point task 1.1 at upstream's.* Rejected on a blind spot: upstream's
  compares cluster against **upstream's** repo, so a bloom-side defect — the vendored `Workflow`
  naming a `templateRef` or inner template that does not exist — reports IN SYNC, exit 0. That is
  precisely the silent submit-accept failure the gate exists to catch. It would also make the gate
  depend on a sibling checkout being present.
- *Keep the pinned-SHA fetch but narrow the comparison to structural fields (`timeout`,
  `retryStrategy`, `resources`, names, inputs) and demote images.* Rejected: it fixes today's symptom
  while retaining the flawed invariant for the remaining fields, so it cries wolf again the next time
  upstream legitimately changes a timeout. Same bug, smaller blast radius.

**Decision: image pins are printed, never asserted.**

A tag can be re-pushed underneath a template, so the pin is worth recording for an operator's log.
It is not bloom's invariant, and treating it as one is the whole of bloom#879.

**Decision: `timeout`, `retryStrategy` and `resources` are not asserted.**

Not declared anywhere in bloom's vendored `Workflow`, so asserting them requires an upstream reference
and reintroduces the invariant this change removes. None can produce the silent
submit-accept-then-controller-error failure, and upstream's comparator already covers them against the
repo that owns them. Recorded as a deliberate narrowing rather than an oversight.

**Decision: carry the `CHECK FAILED` principle forward rather than port the mechanism.**

Upstream's guard exists because its normalisation runs in a subshell without `set -e`, so a failure
would empty both sides, `diff -q` would call them identical, and every template would report IN SYNC
with exit 0. Removing the blob diff removes that mechanism entirely — there is nothing left to
normalise. The principle still binds, and its analogue here is *discovery*: finding zero `templateRef`s
(or fewer than the `Workflow` declares) must not read as "nothing wrong". Hence an explicit
expected-count assertion that exits "could not check". The check never reasons from absence.

**Decision: probe cluster reachability once, before any per-template assertion.**

Separate from bloom#879, the current `_fetch_live` returns `None` on any non-zero `kubectl` exit, so
VPN-down is reported as `NOT REGISTERED` with the contract-violation exit code — a fabricated
violation, and the same "could not check reported as a result" error in the opposite direction.
Upstream probes reachability up front; this does too, reserving exit 2 strictly for "could not check".

## Risks / Trade-offs

- **Narrower than what it replaces: it no longer notices a stale template at all.** → Accepted and
  deliberate: that is upstream's comparator's job, against the repo that owns the templates, and it
  works (verified exit 0 above). The proposal records both invocations so an operator knows which
  question each answers.
- **A conforming-but-wrong template still passes** — correct inner name and declared inputs, but a
  broken `command`. → Out of scope by construction; bloom cannot know upstream's intended command.
  Upstream's comparator catches it.
- **The expected-count assertion could fire spuriously** if the vendored `Workflow`'s DAG is
  legitimately restructured to use fewer tasks. → It fails loud and names the count, and re-vendoring
  is exactly when someone should re-read this check. Loud and wrong beats silent and wrong.

## Migration Plan

No runtime component, schema or cluster object changes; nothing to roll back beyond reverting the
script. The archived task 1.1 invocation still runs — same path, same arguments, same exit-code
contract for 0/1/2 — so any operator muscle memory keeps working.

## Open Questions

None. The comparator-ownership question that motivated this design is settled under Decisions above.
