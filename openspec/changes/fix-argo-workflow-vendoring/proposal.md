## Why

A baseline end-to-end validation of the A4 pipeline (2026-08-24/25 — the first real end-to-end run
attempted since `sleap-roots-pipeline` PR #33 on 2026-07-30) found that real Argo submission, once an
unrelated CA-cert bug was fixed, reached the Kubernetes API for the first time via the dispatch worker
added in bloom #677 (Phase 2, merged 2026-08-18) — and immediately failed: `volume 'images-input-dir'
not found in workflow spec`.

Root cause: `services/workflows/k8s_client.py::build_workflow_body` is an independent Python
reconstruction of the same `Workflow` shape `sleap-roots-pipeline`'s `sleap-roots-pipeline.yaml`
already canonically defines — and it silently dropped `spec.volumes` entirely. Nothing caught this
because Phase 2's own validation deliberately used a minimal `busybox` test workflow with no
`hostPath`/GPU/real-data dependencies (`openspec/changes/archive/2026-08-17-add-cyl-pipeline-dispatch/design.md`),
so the real four-stage DAG was never exercised against a real submission until this session's E2E test.

Two independent representations of the same `Workflow` shape, hand-copied from one to the other with
no mechanism to detect drift, is the root problem — not just the missing volumes specifically. This
proposal fixes the class of bug: `build_workflow_body` stops hand-reconstructing the CRD and instead
loads a vendored, CI-drift-checked copy of the canonical file.

Full cross-repo design context (already agreed before this proposal, not re-litigated here):
`sleap-roots-pipeline` repo, `docs/superpowers/specs/2026-08-25-shared-argo-workflow-source-design.md`
(branch `design-shared-argo-template-source`; companion fix tracked as
[talmolab/sleap-roots-pipeline#49](https://github.com/talmolab/sleap-roots-pipeline/pull/49), a
comment-only guardrail notice on that file's header — open, not yet merged to `main`).

Tracked as [bloom#737](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/737).

## What Changes

- **Vendor the canonical Workflow file.** Add `services/workflows/vendored/sleap-roots-pipeline.yaml`
  — a byte-for-byte copy of `sleap-roots-pipeline`'s `sleap-roots-pipeline.yaml` at commit
  `4d00ec6aa84c0a0f6be07269630e136aead57b6e` (head of PR #49 at time of writing; comment-only, doesn't
  touch `spec`) — plus a sibling `services/workflows/vendored/SLEAP_ROOTS_PIPELINE_REF` file containing
  just that SHA.
- **Rewrite `build_workflow_body`** to read and parse the vendored YAML fresh on every call (a plain
  relative-path read via `Path(__file__).parent / "vendored" / "sleap-roots-pipeline.yaml"` — this
  service has no `importlib.resources` precedent anywhere and its Dockerfile copies the full source
  tree rather than installing a wheel, so a relative-path read matches how it already works; no
  module-level caching, so no explicit copy step is needed — each parse already yields an independent
  structure), and apply exactly four overrides on top of it (three from the cross-repo design agreed
  before this proposal, plus a fourth found during this proposal's own review — see `design.md`):
  - `spec.arguments.parameters[0].value` — the batch's real, comma-joined `scan-ids`, replacing the
    manual file's `""` placeholder. A defensive assertion (`parameters[0].name == "scan-ids"`) runs
    before the overwrite, so a structural change to the vendored file that the CI drift-check wouldn't
    catch (e.g. someone reorders the pin bump with an out-of-band vendored-file edit) still fails loudly
    here instead of silently mis-targeting the wrong parameter.
  - `metadata.labels` — `submitted-by`/`pipeline-run-id`/`batch-index`/`environment` merged into
    whatever labels the vendored file already carries (it currently sets `project: busch-lab`, which
    today's hand-built body drops entirely — this change preserves it, a strictly more complete label
    set than today's).
  - `spec.ttlStrategy` — added only by the dispatch worker, **deliberately not folded into the shared
    file**: the `bloom-pipeline` ServiceAccount that submits dispatched Workflows has no `delete` RBAC
    on `workflows.argoproj.io`, so without a TTL, dispatched objects would accumulate in the shared
    cluster with no way for the worker to ever clean them up (unlike a human running the manual file,
    who can `kubectl delete` their own test runs).
  - `metadata.namespace` — forced to the configured `WORKFLOWS_K8S_NAMESPACE`, overwriting whatever the
    vendored file sets. The vendored file hardcodes `namespace: runai-busch-lab`, which today's
    hand-built body never sets at all (namespace targeting today happens only via the submission URL
    path). The Kubernetes API rejects a submission whose body namespace disagrees with the URL's
    namespace segment, so leaving the vendored value in place would create a second, independent source
    of truth for namespace that could silently diverge from `WORKFLOWS_K8S_NAMESPACE` in the future —
    exactly the class of bug this proposal exists to prevent. Forcing it keeps namespace single-sourced.
- **`pyyaml` becomes a direct dependency**: `pyyaml>=6` added to `services/workflows/pyproject.toml`
  (open floor, no upper bound, per this repo's pin convention). It is already present today as a
  *transitive* dependency (via `sleap-roots-contracts`, resolved in `uv.lock`) — this change makes
  explicit what `build_workflow_body` will directly import, rather than relying on another package's
  dependency graph to keep supplying it.
- **New CI drift-check.** A new Python script (stdlib `urllib.request` only — no new dependency needed
  for CI tooling — with its own pytest unit test, following this repo's `test_pr_checks_*.py`
  convention of asserting CI job/script shape) fetches
  `https://raw.githubusercontent.com/talmolab/sleap-roots-pipeline/<pinned-SHA>/sleap-roots-pipeline.yaml`
  at the SHA recorded in `SLEAP_ROOTS_PIPELINE_REF` and diffs it byte-for-byte against the vendored
  copy, failing loudly on any mismatch. Wired into `.github/workflows/pr-checks.yml` as a new job,
  gated by a job-level `if:` condition (not a change to the file's shared top-level trigger, which
  would scope every other job in `pr-checks.yml` rather than just this one) so it only runs on PRs that
  touch the vendored file, `k8s_client.py`, or the pin, with an explicit timeout and one bounded retry,
  and a failure message that distinguishes "the upstream fetch itself failed" (transient — re-run)
  from "the content genuinely drifted" (real — needs a human decision). See `design.md` for the full
  reasoning. This is the one network fetch in the whole design — it happens in CI (already trusted,
  already talks to GitHub), never in the running
  service or its container build.
- **Tests**: `services/workflows/tests/test_k8s_client.py` gains a test asserting the constructed
  body's `spec.volumes` matches the vendored file's `spec.volumes` exactly — the direct regression test
  for the bug this fixes. Existing tests asserting DAG/label/TTL shape are updated only where the new
  load-and-patch construction changes their expected literal body; none of the behavioral assertions
  they encode change.

**Explicitly out of scope** (tracked as separate follow-ups):
- The four `*-template.yaml` `WorkflowTemplate` files (`images-downloader`/`predictor`/
  `trait-extractor`/`write-back`) — `build_workflow_body`'s DAG only ever references those by name via
  `templateRef`, never their bodies, so there is nothing to vendor there. Whether the cluster's
  *registered* copies of those four match the repo is a real, similarly-shaped risk but a different
  mechanism (template registration vs. programmatic Workflow construction).
- The busybox-only validation gap in Phase 2's original test setup
  (`openspec/changes/archive/2026-08-17-add-cyl-pipeline-dispatch/design.md`'s Risks section) — this
  proposal's regression test closes the specific gap that let the missing-volumes bug ship, but a
  broader "exercise the real DAG against a real cluster before enabling a worker on real traffic"
  process gap remains a separate follow-up.
- Re-verifying the pinned SHA once `sleap-roots-pipeline` PR #49 merges to `main` — tracked as a task
  below, not blocking this change (PR #49 is comment-only and doesn't touch `spec`, so the vendored
  content is correct either way; only the pin's provenance — an unmerged branch head vs. a commit
  reachable from `main` — is worth tidying up afterward).

## Impact

- **Affected specs**: `cyl-pipeline-dispatch` (**modified** — the existing "constructed as a `Workflow`
  CRD" requirement gains the load-from-vendored-source mechanism and a volumes-parity scenario; **new**
  requirements for the vendored-source-loading mechanism itself and the CI drift-check).
- **Affected code**:
  - `services/workflows/k8s_client.py` — `build_workflow_body` rewritten.
  - `services/workflows/vendored/sleap-roots-pipeline.yaml`,
    `services/workflows/vendored/SLEAP_ROOTS_PIPELINE_REF` (new).
  - `services/workflows/pyproject.toml` — new direct `pyyaml>=6` dependency.
  - `services/workflows/tests/test_k8s_client.py` — new + updated tests.
  - `services/workflows/README.md` — its existing "Pipeline dispatch worker" section names
    `build_workflow_body` and describes what it constructs; that description is updated to match the
    load-and-patch mechanism (and to no longer omit `spec.volumes`, the field whose absence caused this
    bug).
  - New CI drift-check script (path decided during implementation — see `design.md`) and its unit test.
  - `.github/workflows/pr-checks.yml` — new job wiring the drift-check script in.
- **Related, not modified here**: `sleap-roots-pipeline`'s own guardrail comment (PR #49, a separate
  repo, tracked and merged independently); the four `WorkflowTemplate` files (out of scope, see above).
