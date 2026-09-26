## Context

This is a bug fix that changes a mechanism (hand-built dict → loaded-and-patched vendored file), adds
a new runtime dependency (`pyyaml`), and introduces a new CI pattern (fetch-and-diff against an
external repo) — none of which this repo has an existing precedent for. Per `openspec/AGENTS.md`, that
combination warrants a `design.md` rather than skipping straight to a proposal-only bug-fix path.

The cross-repo architecture decisions (canonical source = whole file, the three-override list, vendor
+ CI-drift-check as the distribution mechanism, scope boundary excluding the four `WorkflowTemplate`
files) were already made and agreed with the user in a prior session and are carried in as-is — see
`proposal.md`'s "Why" section for the pointer to the full cross-repo design doc. This document covers
only what's specific to implementing that design inside `salk-bloom`.

## Goals / Non-Goals

**Goals:**
- `build_workflow_body`'s output includes every field the canonical `sleap-roots-pipeline.yaml`
  defines, not a hand-picked subset — closing the exact gap that let `spec.volumes` go missing.
- A drift between the vendored copy and the pinned upstream commit fails CI loudly, before merge —
  not silently, and not only discoverable by a real cluster submission.
- The four dispatch-specific overrides (`scan-ids` value, labels, `ttlStrategy`, `metadata.namespace`
  — the fourth added during this proposal's review, see below) remain the only divergence between the
  vendored file and what's actually submitted.

**Non-Goals:**
- Vendoring or drift-checking the four `WorkflowTemplate` files (out of scope — see `proposal.md`).
- Building general-purpose cross-repo vendoring infrastructure (a package registry, a codegen step) —
  this is a single-file, narrowly-scoped mechanism sized for exactly one file.
- Fixing the busybox-only validation gap in Phase 2's original test setup — a process gap, not
  something this mechanism change resolves on its own (though the new regression test closes the
  specific instance that shipped).

## Decisions

### Loading mechanism: plain relative-path read, not `importlib.resources`

`services/` has zero existing `importlib.resources` usage anywhere (confirmed by search), and
`services/workflows/Dockerfile` does `COPY . .` — the running container has the full source tree on
disk at `/app`, not an installed wheel with packaged resource data. A plain
`Path(__file__).parent / "vendored" / "sleap-roots-pipeline.yaml"` read matches how this service
already works and needs no new packaging configuration (`package_data`, `MANIFEST.in`, or a
`pyproject.toml` build-backend change). `importlib.resources` earns its complexity when a module is
installed as a distributable package and can't assume a real filesystem path next to it — neither is
true here.

### `pyyaml` becomes a direct dependency of `services/workflows` specifically

`pyyaml` is already resolved and installed inside the running `bloom-workflows` container today — as a
*transitive* dependency of `sleap-roots-contracts` (`services/workflows/pyproject.toml`'s existing
dependency; see `uv.lock`, which resolves `sleap-roots-contracts` → `pyyaml==6.0.3`). `build_workflow_body`
would be relying on that undeclared transitive presence for functionality it directly imports — fragile,
since a future `sleap-roots-contracts` release could drop that requirement without this module noticing
until a real deploy breaks. Declaring `pyyaml>=6` (open-floor, no upper bound, per this repo's Python pin
convention) as a *direct* dependency of `services/workflows/pyproject.toml` makes explicit what the
module actually needs, independent of another package's dependency graph. (The root `bloom-tests`
package also depends on `pyyaml`, for parsing GitHub Actions workflow YAML in `tests/unit/` — that's a
separate package with its own `pyproject.toml`/`uv.lock` and has no bearing on what's installed inside
the `bloom-workflows` container.)

### CI drift-check: a small Python script with its own unit test, not inline shell

`pr-checks.yml` has no existing "fetch an external file and diff it against a local copy, fail on
mismatch" pattern to reuse. The `build-and-audit` job's contract-types drift guard
(`npm run contracts:check`) is the closest *shape* analog (a wrapped script, not inline `curl`/`diff`
steps in the workflow YAML) but it resolves a pinned **npm package** during `npm ci` — it doesn't make
a bespoke HTTP call to an external raw-content URL at CI runtime the way this new job will. This *is*
genuinely the first bespoke external-repo-content fetch in `pr-checks.yml`, though not the first
unauthenticated third-party GitHub fetch at all — `pr-checks.yml` already `curl`s a release asset from
`github.com/supabase/cli` (lines ~592, ~1058), unauthenticated, with no retry. That existing precedent
means this repo already accepts *some* risk here; this design goes further by adding the resilience
measures below, precisely because this new job is proposed as a required/blocking check on every PR
(unlike the Supabase CLI download, which only runs where that step already sits in its job).

This repo's own convention for testing `pr-checks.yml` itself (`tests/unit/test_pr_checks_*.py`, e.g.
`test_pr_checks_workflow_shape.py`, `test_pr_checks_docker_build_bloomcli.py`) is to assert job/script
*shape* via a dedicated pytest unit test, which is only practical against a script with real,
unit-testable structure — not bare inline shell. A small Python script (exact path/name decided during
implementation — a natural home is alongside the vendored file, e.g.
`services/workflows/vendored/check_drift.py`, or under `scripts/` if that fits the existing
script-organization convention better once checked) gets:
- a unit test exercising its diff logic against fixture content (match / mismatch / fetch-failure
  cases), independent of any real network call in the test itself;
- a `pr-checks.yml` job that invokes it, in the same spirit as `build-and-audit` invoking
  `contracts:check` — the one place a real network fetch happens, in CI.

**Implementation choices for the script itself**, driven by CI-review findings:
- **stdlib `urllib.request` only, no third-party HTTP library.** This repo's other root-level Python CI
  scripts (`verify_env_parity.py`, `verify_image_parity.py`, `check-uv-locks.py`) are stdlib-only and
  runnable via a bare `uv run --python 3.11 python scripts/foo.py`/pre-commit-hook invocation with no
  separate install step (`.pre-commit-config.yaml` notes this is deliberate — it's also what keeps
  these hooks working for contributors without a configured Python environment, including on Windows).
  A drift-check script that needed `requests`/`httpx` installed first would break that pattern for no
  real benefit — `urllib.request` is sufficient for one GET request.
- **An explicit request timeout and job-level `timeout-minutes` — both independently testable, not
  just described in prose.** This repo has a documented incident (issue #454, referenced in
  `pr-checks.yml`) where a network call with no timeout wedged a job to GitHub's default 6-hour cap
  with zero output. A job whose entire purpose is one external network call is exactly that failure
  shape. The script structures its fetch behind a small function that takes an explicit timeout
  parameter (matching `k8s_client.py`'s existing `timeout=15.0` convention for outbound HTTP calls),
  so a unit test can monkeypatch `urllib.request.urlopen` and assert it was called with that timeout —
  a prose commitment alone isn't enough here; see `tasks.md` 3.1. The job itself also sets a short
  `timeout-minutes`, asserted by the job-shape test (`tasks.md` 3.3).
- **One bounded retry before failing** (a fixed short delay between attempts, not full exponential
  backoff — matching the precision of what this repo's compose-health-check steps actually do
  elsewhere: fixed-interval polling, not backoff). No job in `pr-checks.yml` retries an external fetch
  today, but this job's failure mode is unusually costly for a false positive: because it's proposed as
  blocking, a transient GitHub blip would otherwise read identically to a real, intentional drift (see
  the next decision) and could tempt someone to "fix" it by touching the vendored file, or to disable
  the check entirely. A single retry is a small addition that meaningfully cuts that false-positive
  rate.
- **Distinct failure messages for "fetch failed" vs. "content drifted."** These are different problems
  requiring different human responses (re-run vs. investigate a real change), and the CI-drift
  requirement's scenarios are written to test both distinctly — see the delta spec.
- **Path-scoped trigger — as a job-level `if:` conditional, NOT a top-level `on.pull_request.paths:`
  filter.** `pr-checks.yml` currently has no path filtering anywhere; every job in the file shares one
  top-level `on: pull_request: branches: [main, staging]` trigger with no `paths:` key. This matters
  because `pr-checks.yml` is a single workflow file holding every other job too (`build-and-audit`,
  `docker-build`, `compose-health-check`, `dev-stack-smoke`, etc.). Adding a top-level `paths:` filter
  to scope the new job would scope the *entire file* instead — silencing every other job (including
  security-relevant ones like the Trivy CVE scan) on any PR that doesn't happen to touch
  `services/workflows/**`, which is the opposite of this decision's goal and a severe regression, not a
  narrow one. The new job MUST instead compute whether its own relevant paths changed (e.g. via
  `dorny/paths-filter` or an equivalent `git diff`-based step) and gate only itself with a job-level
  `if:`, leaving every other job's trigger untouched. This is scoped to
  `services/workflows/vendored/**`, `services/workflows/k8s_client.py`, and
  `services/workflows/pyproject.toml`.
- **No `GITHUB_TOKEN` is added for this fetch.** `raw.githubusercontent.com` is served from GitHub's CDN
  and is not subject to the `api.github.com` core rate limit that a token would raise — attaching
  `GITHUB_TOKEN` to a `raw.githubusercontent.com` URL doesn't buy the headroom that pattern usually
  implies. If rate-limit resilience is ever actually needed, the correct fix is switching to
  `api.github.com/repos/talmolab/sleap-roots-pipeline/contents/sleap-roots-pipeline.yaml?ref=<SHA>` with
  an `Accept: application/vnd.github.raw` header and a token — not assumed necessary for a single
  low-frequency per-PR fetch of a small file, but noted here so a future implementer doesn't reach for
  the wrong fix if rate-limiting is ever observed in practice.

### Defensive assertion on `parameters[0].name`

The CI drift-check guards against the *vendored copy* drifting from upstream. It does not guard
against `build_workflow_body` itself mis-indexing into a structurally-changed-but-still-in-sync file
(e.g. a future canonical-file change reorders `spec.arguments.parameters`). The
`parameters[0].name == "scan-ids"` assertion before overwriting `parameters[0].value` is a cheap,
independent second check — if it fires, that's a configuration error (same `K8sConfigError` treatment
as a missing credential: raised before any network call), not a silent wrong submission.

**Extended during a later review round to guard the assumed shape, not just the name.** The original
assertion assumed `parameters` was present and shaped as a list of dicts, checking only whether
`parameters[0]["name"]` matched — but a vendored file where `parameters` exists with an unexpected
shape (not a list, or its first element not a dict) would raise a raw `AttributeError`/`TypeError` from
`.get("name")` instead of `K8sConfigError`. That uncaught exception is not caught by
`dispatch_worker.py`'s `process_one()` (which only catches `K8sConfigError`/`K8sSubmissionError`), so
it would fall through to the generic loop-error handler and crash-loop the same claim every time its
visibility timeout expired — a real availability gap, not silent data loss, but still not the clean
config-error path this assertion exists to guarantee. Now checked with `isinstance` before any `.get()`
call.

### Confirmed: the vendored file's `entrypoint`/`serviceAccountName` match today's hardcoded values

The current hand-built `build_workflow_body` hardcodes `spec.entrypoint: "pipeline"` and
`spec.serviceAccountName: "bloom-workflow"`. The vendored `sleap-roots-pipeline.yaml` (pinned commit
`4d00ec6aa84c0a0f6be07269630e136aead57b6e`) defines the identical values for both fields — confirmed by
direct comparison against the sibling `sleap-roots-pipeline` checkout, not assumed. This is the basis
for the Migration Plan's equivalence claim below: after this change, submitted Workflow pods run under
the same `serviceAccountName` they do today, not a different one.

The vendored file's DAG structure was checked the same way, not left as the one unverified assumption
in an otherwise-confirmed set of claims: its four tasks (`images-downloader` → `predictor` →
`trait-extractor` → `write-back`), each task's `templateRef.name`
(`sleap-roots-images-downloader-template`, `sleap-roots-predictor-template`,
`sleap-roots-trait-extractor-template`, `sleap-roots-write-back-template`), and the dependency chain
(each task depending on exactly the previous one) all match `k8s_client.py`'s existing `_TEMPLATE_REFS`
list exactly — confirmed by direct comparison, not assumed, the same rigor applied to
`entrypoint`/`serviceAccountName` above. This closes the one place this proposal would otherwise have
left "the two representations happen to match" as an unverified assumption — precisely the class of
mistake this whole change exists to stop making.

Worth flagging explicitly because of a naming collision that's easy to confuse: **`bloom-pipeline`** is
the RBAC identity the dispatch worker authenticates as to *submit* Workflows to the K8s API
(`WORKFLOWS_K8S_TOKEN` etc.), while **`bloom-workflow`** (`spec.serviceAccountName`, set inside the
submitted object) is the identity the Workflow's *pods* run as once Argo's controller picks them up —
two different ServiceAccounts, similar names, serving different halves of the submission-to-execution
pipeline. This change doesn't alter either identity or its RBAC; it only changes how the *value*
`"bloom-workflow"` reaches the submitted object (loaded from the vendored file rather than hardcoded).

### A fourth override: `metadata.namespace` is forced to `WORKFLOWS_K8S_NAMESPACE`

The vendored file's `metadata` block sets both `namespace: runai-busch-lab` and
`labels: {project: busch-lab}` — neither of which today's hand-built `build_workflow_body` sets at
all; namespace targeting today happens purely via the submission URL path
(`.../namespaces/{WORKFLOWS_K8S_NAMESPACE}/workflows`), never via the object body. Loading the file
verbatim would embed `metadata.namespace` in the submitted object for the first time, and the
Kubernetes API validates that an object body's `metadata.namespace` (when present) matches the URL's
namespace segment — a mismatch is rejected. Today both happen to be `"runai-busch-lab"`, so submission
would work, but that's a coincidence, not a guarantee: if `WORKFLOWS_K8S_NAMESPACE` is ever
reconfigured without a corresponding vendored-file edit (or vice versa), submissions would start
failing with a generic `K8sSubmissionError` — exactly the class of silent-coupling failure this whole
proposal exists to close off, just relocated to a different field. Found during this proposal's
review, after the cross-repo design's three-override list had already been agreed.

**Resolution: `build_workflow_body` adds a fourth explicit override**, forcing
`metadata.namespace = WORKFLOWS_K8S_NAMESPACE` (the same value already used to build the submission
URL) regardless of what the vendored file sets. This keeps a single source of truth for namespace,
consistent with the existing "Namespace targeting is a single configured value in v1" requirement, and
can never silently drift — the URL and the body are always constructed from the same variable.

### `metadata.labels` override is a full merge, not a replace

The vendored file's `metadata.labels` currently contains `project: busch-lab` — a label the current
hand-built body does not carry at all, so this change is an immediate, observable addition, not a
hypothetical future one. The four dispatch-added labels
(`submitted-by`/`pipeline-run-id`/`batch-index`/`environment`) are merged into the vendored file's
existing `metadata.labels`, not a wholesale replacement of the key, so `project: busch-lab` (and any
label a future vendored-file revision adds) is preserved on every submitted Workflow going forward —
a strictly more complete label set than today's, consistent with the existing "at minimum" phrasing in
the mandatory-attribution-labels requirement.

**Extended during a later review round to detect a key collision, not just merge silently.** A plain
`dict.update()` lets the dispatch-added keys win on any collision with no signal that anything unusual
happened. Today's vendored file only sets `project: busch-lab` — no collision — but a future
vendored-file revision that happens to add e.g. a generic `environment` label would be silently and
permanently shadowed, with zero CI signal (the drift-check only diffs vendored-vs-upstream content, not
override semantics). `build_workflow_body` now raises `K8sConfigError` if the vendored file's labels
already define any of the four dispatch keys — the same structural-drift treatment the `scan-ids`
assertion already gives labels' sibling override.

### No module-level caching — the vendored file is read and parsed fresh on every call

`build_workflow_body` reads and `yaml.safe_load`s the vendored file anew on every call, rather than
parsing it once at module-import time and caching the result (no `functools.lru_cache`, no
module-level parsed-structure constant). `yaml.safe_load` already returns a brand-new object graph
each time it's called, so re-parsing from disk on every call already gives every caller an independent
structure with no aliasing risk — an explicit `copy.deepcopy` step is unnecessary on top of that and is
not part of the implementation. The independent-copies test (`tasks.md` 2.2) exists as a regression
guard against a *future* change introducing caching without also introducing a copy step, not because
today's straightforward implementation needs one.

### Refinement (found during PR review): a 404 is a *permanent* fetch failure, not a transient one

The first implementation of "distinct failure messages for fetch-failed vs. content-drifted" (above)
caught every network-level problem — DNS failure, timeout, and HTTP error status — in one
`except (urllib.error.URLError, OSError)` clause and reported all of them as "transient — re-run this
job." That's wrong for an HTTP 404 specifically: `urllib.error.HTTPError` is itself a subclass of
`URLError` (confirmed directly against Python's `urllib.error` module, not assumed), so a 404 was being
silently folded into the "transient" bucket. A 404 on `raw.githubusercontent.com` for a specific commit
SHA means that commit no longer resolves upstream at all — exactly the dangling-pin scenario the Risk
above describes — and no amount of retrying or re-running the job fixes that; only a human re-pinning
`SLEAP_ROOTS_PIPELINE_REF` does. `check_vendored_workflow_drift.py` now special-cases `HTTPError` with
`code == 404` into its own `PinNotFoundError` (a `FetchError` subclass) and `check_drift` reports it
with a distinct "PIN NO LONGER RESOLVES UPSTREAM" message — see the next section for how the retry
policy around it was subsequently refined. Two related gaps closed at the same time:
`http.client.IncompleteRead` (raised by `resp.read()` on a truncated response — not a
`URLError`/`OSError` subclass, so it previously escaped uncaught and would have crashed the script with
Python's default exit code 1, colliding with the "content drift" exit code) is now also caught; and
`SLEAP_ROOTS_PIPELINE_REF` being missing or containing something that isn't a 40-character commit SHA
is now a clean, distinct "PIN FILE MISSING/MALFORMED" failure (`EXIT_PIN_FILE_INVALID`) that never
reaches the network, rather than a raw `FileNotFoundError` or a confusing 404 from building a URL out
of garbage.

### Second refinement (found during a later PR review round): a single 404 must still get a retry

The first refinement above initially had `fetch_with_retry` never retry a `PinNotFoundError` at all —
reasoned as "retrying a 404 wastes the budget on something that can't succeed." A later review round
caught the gap in that reasoning: `raw.githubusercontent.com` is served off GitHub's CDN, which can
briefly 404 a commit that was only just pushed, before it's fully propagated — indistinguishable from a
genuinely dangling pin (branch deleted, commit garbage-collected) from the response alone. This was not
hypothetical for this PR specifically: its own pin was re-pinned live, same-day, right after
`sleap-roots-pipeline` PR #49 merged (see the Risk above) — exactly the scenario where a fresh commit
could transiently 404. `fetch_with_retry` now gives a 404 the same retry chance as any other failure;
only if it persists across the *entire* retry budget does it re-raise as `PinNotFoundError` and
`check_drift` report `EXIT_PIN_NOT_FOUND` (a code now distinct from generic transient-fetch-failure's
`EXIT_FETCH_FAILED`, closing a separate finding from the same review round that the two failure modes
previously shared one exit code, distinguishable only by message text).

### Error handling for a missing/unparseable vendored file

Treated the same as a missing `WORKFLOWS_K8S_CA_CERT`/`_TOKEN`/`_API_URL` today: a `K8sConfigError`
raised before any network call, not a runtime surprise on first real dispatch. This covers both a file
that fails to parse as YAML at all, and a file that parses as valid YAML but doesn't have the expected
shape (e.g. missing `spec`/`metadata` keys, or a top-level structure that isn't a mapping) — the latter
would otherwise surface as a raw `KeyError`/`TypeError` from `build_workflow_body`'s own field lookups
rather than a clean configuration error, which is the same class of "uncaught exception instead of a
recognizable failure" this proposal exists to close off, just one level removed from the scan-ids-name
case the defensive assertion above already covers. This keeps `build_workflow_body`'s failure modes
consistent with the rest of this module's existing config-error convention (see `k8s_client.py`'s
module docstring).

### Symlink rejection (found during a later review round)

The vendored path's `detect-vendored-workflow-changes` path-scoping (see the CI drift-check decision
above) watches `services/workflows/vendored/**` by path, not by resolved content. If that path were ever
replaced with a symlink pointing outside the watched paths, only the *first* edit (creating the symlink)
would trigger the drift-check; every subsequent edit to the symlink's target would be invisible to the
`git diff` the detection job runs, permanently blinding drift detection from that point on. Both
`check_vendored_workflow_drift.py`'s `check_drift` and `k8s_client.py`'s `_load_vendored_workflow` now
reject a symlinked vendored path outright (`Path.is_symlink()`), before reading its content.

## Risks / Trade-offs

- **RESOLVED during PR review, not merely a theoretical risk: the pin pointed at an unmerged upstream
  branch head, and hitting the consequence took days, not a hypothetical future.** `SLEAP_ROOTS_PIPELINE_REF`
  originally recorded `4d00ec6aa84c0a0f6be07269630e136aead57b6e`, the head of `sleap-roots-pipeline` PR
  #49. That PR merged (`9df1e52d...`) and — as is normal, expected cleanup — its feature branch was
  deleted, leaving the original pinned commit unreachable from `main` and at real risk of eventual
  garbage collection. Re-pinned live to `main`'s new HEAD once the merge landed (same commit content;
  the PR was comment-only). This is exactly the scenario the CI-drift-check decision's "distinct failure
  messages" bullet anticipated in the abstract — see the classification refinement immediately below,
  which closes the gap a first implementation of that idea still had.
- **This is the first bespoke external-repo-content fetch in `pr-checks.yml`** (see the CI drift-check
  decision above for the distinction from the existing, less rigorous Supabase CLI `curl` precedent).
  Rejected alternatives (published package, live fetch at build/runtime for the *service itself*, as
  opposed to CI) are documented in the cross-repo design doc referenced from `proposal.md`; this repo's
  implementation doesn't reopen that choice.
- **A future canonical-file change requires a coordinated two-repo update** (bump the pin here whenever
  `sleap-roots-pipeline.yaml`'s `volumes`/`entrypoint`/`serviceAccountName`/DAG structure changes) — the
  guardrail comment added in `sleap-roots-pipeline` PR #49 is the mitigation for someone forgetting;
  this repo's CI drift-check is the mitigation for someone remembering to update the vendored file but
  not the pin (or vice versa).

## Migration Plan

No data migration. Deploying this change requires no coordinated rollout step beyond the normal
merge-to-staging path. `build_workflow_body`'s output changes from today's in exactly these ways, all
either newly-correct or explicitly confirmed equivalent (not assumed):
- `spec.volumes`, previously missing entirely, is now present (the bug fix itself).
- `spec.entrypoint` (`"pipeline"`) and `spec.serviceAccountName` (`"bloom-workflow"`) are unchanged —
  confirmed identical to today's hardcoded values by direct comparison against the vendored file (see
  "Confirmed: the vendored file's `entrypoint`/`serviceAccountName` match today's hardcoded values"
  above).
- `metadata.labels` gains `project: busch-lab` in addition to the four labels already required today —
  additive, not a behavior change any existing requirement or consumer depends on being absent.
- `metadata.namespace` is now present in the submitted body for the first time, forced to
  `WORKFLOWS_K8S_NAMESPACE` (the fourth override, above) — equivalent to today's behavior since that's
  the same value the URL path has always targeted.
- The four overrides (`scan-ids` value, labels, `ttlStrategy`, `metadata.namespace`) are applied to a
  loaded file instead of an inline dict literal — a construction-mechanism change with no observable
  difference in the overrides' own values.

## Open Questions

None outstanding for this repo's implementation. (The cross-repo design's own open questions were
already resolved before this proposal — see `proposal.md`'s "Why" section.)
