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
| bloom `scripts/check_registered_templates.py` | upstream files @ pinned `310aae63` | **DRIFT ×5, exit 1** |
| upstream `scripts/check_cluster_drift.sh` | upstream repo files @ `main` | **IN SYNC ×5, exit 0** |

Every one of the five diffs is an image tag, an image digest, or a digest env var. The structural
contract bloom actually depends on is fully intact: all five inner `template:` names match, and the
exit gate declares exactly the three parameters the vendored `Workflow` passes it. The three changes
being reported as drift were all correct — bloom#871's `bloomctl` bump (srp#79) and the
`SRP_PREDICT_CONTAINER_DIGEST`/`SRT_TRAITS_CONTAINER_DIGEST` injection that fixed srp#70 (srp#78).

This matters more than a noisy check. A wrong inner `template:` name cannot fail at submit — the K8s
API server accepts a `Workflow` naming a nonexistent template, because resolution is the Argo
controller's job and the dispatch worker POSTs to the raw K8s API. The failure surfaces a poll cycle
later as an `Error` Workflow with every scan in the batch marked failed, no requeue, no dead letter,
and the diagnostic TTL-GC'd within the hour. Task 1.1 of the archived
`vendor-five-task-pipeline-dag` makes this script a blocking pre-merge gate for exactly that reason.
A gate that reports DRIFT on a correct cluster trains an operator to wave DRIFT through, which is
what it was written to prevent.

**It has already produced one false record.** `fix-cyl-redelivery-blob-collision/tasks.md` task 9.6
reads "verified independently 2026-09-21 via `scripts/check_registered_templates.py`: all five
registered templates report IN SYNC with the pin, exit 0". That is not reproducible — the pin has not
moved since #866 set it, so the script could only have reported DRIFT. The quoted wording matches
upstream's comparator output verbatim, so upstream's script was almost certainly run and
misattributed. The cry-wolf is not a hypothetical risk to operator trust; it has already been
absorbed once, four days in.

## What Changes

- **Re-base the comparator on the contract bloom owns.** `check_registered_templates.py` SHALL derive
  its expectations from `services/workflows/vendored/sleap-roots-pipeline.yaml` — the file bloom
  vendors and dispatches from — and assert the live cluster satisfies them: each `templateRef`'s
  `WorkflowTemplate` exists, declares an inner template of the referenced name, and declares every
  parameter the `Workflow` passes it.
- **Stop fetching upstream.** `RAW_URL`, the `SLEAP_ROOTS_PIPELINE_REF` coupling, and the whole
  server-default normalisation surface (`_strip_server_defaults`, CPU canonicalisation) are removed —
  with no blob diff there is nothing left to normalise.
- **Demote image pins to informational.** Tags, digests and env vars are printed and never affect the
  exit code. They are upstream's business and are already checked by upstream's comparator against
  the repo that owns them.
- **Fix a second, separate latent bug.** `_fetch_live` currently returns `None` on *any* non-zero
  `kubectl` exit, so an unreachable cluster is reported as `NOT REGISTERED` with exit 1 — a fabricated
  contract violation. Reachability SHALL be probed once up front, with exit 2 reserved strictly for
  "could not check".
- **Refuse to reason from absence.** Discovery finding zero `templateRef`s, or fewer than the vendored
  `Workflow` declares, SHALL exit 2 rather than report success.
- **Add the tests it never had.** The script shipped with no test coverage at all. Unit tests with
  fixture `Workflow`s and a stubbed `kubectl` run in CI, where the script itself never can.
- **Correct the false record** in `fix-cyl-redelivery-blob-collision/tasks.md` task 9.6, and annotate
  the archived task 1.1 that still prints the superseded invocation.

## Impact

- Affected specs: `cyl-pipeline-dispatch` (one ADDED requirement; the existing CI vendored-drift
  requirement is a different check and is untouched)
- Affected code: `scripts/check_registered_templates.py`,
  `tests/unit/test_check_registered_templates.py` (new)
- Affected records: `openspec/changes/fix-cyl-redelivery-blob-collision/tasks.md` (task 9.6),
  `openspec/changes/archive/2026-09-21-vendor-five-task-pipeline-dag/tasks.md` (task 1.1 annotation)
- Not affected: the dispatch worker, the vendored `Workflow` itself, and the registered templates —
  this change touches no runtime path and registers nothing in the cluster
- Closes: bloom#879
