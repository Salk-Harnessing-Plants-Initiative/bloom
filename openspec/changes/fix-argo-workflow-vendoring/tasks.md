> **Commit grouping**: three commits in one PR against `staging` (per this repo's proposal+implementation
> convention — no proposal-only PR). Pass `--base staging` explicitly when opening the PR (this branch's
> upstream tracking was briefly misconfigured to `origin/main` during setup; fixed, but don't rely on
> tracking/default-branch inference either way).
> 1. `chore(workflows): vendor the canonical sleap-roots-pipeline Argo Workflow (bloom #737)` — section 1.
>    Inert on its own (nothing references these files yet); CI green.
> 2. `fix(workflows): load build_workflow_body from a vendored canonical Argo Workflow source (bloom #737)`
>    — section 2 (the behavioral rewrite, its tests, the new direct dependency, the README/docstring
>    updates), plus the two small documentation-only additions from section 4 that produce real diff
>    content (4.3's pin-reverification note, 4.4's optional `project.md` entry). CI green (full suite
>    passes per task 2.6).
> 3. `chore(ci): add sleap-roots-pipeline.yaml drift-check to pr-checks.yml` — section 3. CI green (task
>    3.6 verifies against the real pin pre-PR).
>
> **Verification gates are checks, not diffs, and aren't "in" any one commit**: 4.1 (lint), 4.2 (lockfile
> sync), 4.5 (`openspec validate --strict`), and 3.5 (`pip-audit` against the new dependency) all run
> against the full branch state once every commit above exists locally — 4.1 in particular touches files
> from both commit 2 and commit 3, so it cannot be scoped to either one alone. Run them all, in this
> order, right before opening the PR.

## 1. Vendor the canonical file

- [x] 1.1 Copy `sleap-roots-pipeline`'s `sleap-roots-pipeline.yaml` at commit
      `4d00ec6aa84c0a0f6be07269630e136aead57b6e` verbatim to
      `services/workflows/vendored/sleap-roots-pipeline.yaml`. Verify byte-for-byte against the
      sibling checkout (`c:\repos\sleap-roots-pipeline`, that commit) before committing — this copy
      *is* the CI drift-check's baseline, so it must be exact from the start.
- [x] 1.2 Add `services/workflows/vendored/SLEAP_ROOTS_PIPELINE_REF` containing exactly
      `4d00ec6aa84c0a0f6be07269630e136aead57b6e` (no trailing content beyond a single trailing
      newline, matching how the drift-check script will read it).

## 2. `build_workflow_body`: tests first (red), then implementation (green)

- [x] 2.1 Bind the vendored file's path to a module-level constant in `k8s_client.py` (e.g.
      `_VENDORED_WORKFLOW_PATH = Path(__file__).parent / "vendored" / "sleap-roots-pipeline.yaml"`),
      matching this module's existing pattern for `TOKEN`/`CA_CERT`/`API_URL`/`NAMESPACE` — all
      module-level, all individually `monkeypatch.setattr`-able in tests. Without this, the tests in
      2.2 below have no seam to patch a missing/malformed file onto.
- [x] 2.2 In `services/workflows/tests/test_k8s_client.py`, add (confirm these **fail** against the
      current hand-built implementation before writing any production code):
  - `test_build_workflow_body_volumes_match_the_vendored_file_exactly` — load the vendored YAML in the
    test itself (independently of `k8s_client`, e.g. via a raw `yaml.safe_load` on the fixture path),
    call `build_workflow_body`, assert `body["spec"]["volumes"] == vendored["spec"]["volumes"]`. This
    is the direct regression test for the bug this change fixes.
  - `test_build_workflow_body_preserves_entrypoint_and_service_account_from_vendored_file` — same
    comparison for `spec.entrypoint` and `spec.serviceAccountName`.
  - `test_build_workflow_body_preserves_dag_structure_from_vendored_file` — same comparison for the
    DAG: task names, `templateRef.name` for each, and the dependency chain, against the vendored
    file's own `spec.templates[0].dag.tasks` — this is the DAG-shape equivalent of the entrypoint/
    serviceAccountName check above, closing the one place this proposal would otherwise have left "the
    two representations happen to match" unverified by an actual test (design.md confirms the match
    by inspection; this test makes it a standing regression guard, not a one-time inspection).
  - `test_build_workflow_body_only_changes_the_four_documented_overrides` — load the vendored YAML
    independently, apply the four overrides by hand (scan-ids value, merged labels, ttlStrategy,
    namespace), and assert `build_workflow_body`'s actual output equals that hand-patched structure
    exactly — a full-structure diff, not per-field spot checks. This is what actually proves the "no
    other field modified" requirement; the field-specific tests above only spot-check individual paths.
  - `test_build_workflow_body_merges_labels_rather_than_replacing` — assert the returned
    `metadata.labels` includes `project: busch-lab` (present in the vendored file today) *in addition
    to* the four dispatch-added labels.
  - `test_build_workflow_body_forces_namespace_to_the_configured_value` — monkeypatch `NAMESPACE` to a
    value that differs from the vendored file's hardcoded `runai-busch-lab`; assert the returned
    `metadata.namespace` equals the monkeypatched value, not the vendored file's.
  - `test_build_workflow_body_returns_independent_copies_across_calls` — call `build_workflow_body`
    twice, mutate the first result's `spec.volumes`, assert the second call's result is unaffected.
    The implementation re-reads and re-parses the vendored file on every call rather than caching a
    parsed structure (see `design.md`'s "No module-level caching" decision), which already gives every
    call an independent object graph — this test is a regression guard against a future change that
    adds caching without also adding a copy step, not proof that today's implementation needs an
    explicit `copy.deepcopy`.
  - `test_build_workflow_body_raises_configerror_on_missing_vendored_file` — monkeypatch
    `_VENDORED_WORKFLOW_PATH` (from 2.1) to a nonexistent path; assert `K8sConfigError`, not an
    uncaught `FileNotFoundError`, and assert no network call is attempted (reuse this file's existing
    `calls["posted"] = False` pattern).
  - `test_build_workflow_body_raises_configerror_on_unparseable_vendored_file` — monkeypatch
    `_VENDORED_WORKFLOW_PATH` to a fixture file containing invalid YAML syntax; assert `K8sConfigError`,
    not an uncaught YAML parse error, and assert no network call is attempted.
  - `test_build_workflow_body_raises_configerror_on_structurally_wrong_vendored_file` — monkeypatch
    `_VENDORED_WORKFLOW_PATH` to a fixture that parses as *valid* YAML but isn't shaped like a Workflow
    (e.g. a bare list, or a mapping missing `spec` entirely); assert `K8sConfigError`, not an uncaught
    `KeyError`/`TypeError` from `build_workflow_body`'s own field lookups. Distinct from the invalid-YAML
    case above — this is valid YAML with the wrong shape, not a syntax error.
  - `test_build_workflow_body_raises_configerror_when_scan_ids_parameter_missing_or_misnamed` — feed a
    variant of the vendored structure (via a test-local fixture file, monkeypatching
    `_VENDORED_WORKFLOW_PATH` to it) whose `spec.arguments.parameters[0].name` isn't `"scan-ids"`;
    assert `K8sConfigError` is raised before any value is overwritten.
- [x] 2.3 Update the existing tests whose expected literal body changes shape because construction now
      loads-and-patches rather than building a dict from scratch — at minimum re-verify
      `test_build_workflow_body_has_correct_apiversion_kind_and_generatename`,
      `test_build_workflow_body_includes_required_labels`,
      `test_build_workflow_body_includes_environment_label`,
      `test_build_workflow_body_includes_ttl_strategy`,
      `test_build_workflow_body_parameterizes_scan_ids_for_this_batch_only`, and
      `test_build_workflow_body_dag_references_all_four_templates_in_order` still pass unmodified in
      *behavior* (their assertions describe outcomes, not construction mechanism) — adjust only if the
      loaded vendored file's DAG task order or structure requires it. None of these should need new
      expected values, since the vendored file's DAG references the same four templates in the same
      order the hand-built version did (independently confirmed — see 2.2's new DAG-structure test).
- [x] 2.4 Add `pyyaml>=6` to `services/workflows/pyproject.toml`'s `dependencies` as a direct dependency
      (open floor, no upper bound, per this repo's Python pin convention) — it is already present
      transitively via `sleap-roots-contracts`, so this makes explicit what the module directly needs
      rather than relying on another package's dependency graph. Run `uv lock` for `services/workflows`
      and commit the updated `uv.lock`.
- [x] 2.5 Implement the rewrite: `build_workflow_body` reads and parses `_VENDORED_WORKFLOW_PATH` (from
      2.1) with `yaml.safe_load` fresh on every call (no module-level caching — see `design.md`),
      applies the four overrides (scan-ids value with the defensive name assertion, merged labels,
      ttlStrategy, namespace forced to `NAMESPACE`), and returns the result. Raise `K8sConfigError` for
      a missing file, a YAML parse failure, a structurally-wrong-but-valid file (missing
      `spec`/`metadata`), or a failed defensive assertion — before any network call, matching this
      module's existing config-error convention. Update `k8s_client.py`'s module docstring and
      `build_workflow_body`'s own docstring, both of which currently describe the pre-change
      hand-building mechanism, including a mention of the namespace-forcing behavior specifically
      (the docstring most likely to be read before this function is touched again should say why
      `metadata.namespace` is overwritten, not just that it is).
- [x] 2.6 Run `services/workflows`' test suite (`uv run --extra test pytest`) and confirm every test
      from 2.2 and 2.3 passes (green).
- [x] 2.7 Update `services/workflows/README.md`'s "Pipeline dispatch worker" section, which currently
      names `build_workflow_body` and enumerates what it constructs from scratch (and, notably, never
      mentions `spec.volumes` — the same omission that caused this bug). Specifically:
  - Rewrite the "Constructs a Workflow CRD" bullet list to describe the load-from-vendored-file-plus-
    four-overrides mechanism (scan-ids value, merged labels including the vendored file's own
    `project: busch-lab`, ttlStrategy, namespace forced to `WORKFLOWS_K8S_NAMESPACE`) instead of
    "constructs from scratch."
  - Update the existing "Namespace is a single hardcoded value for v1" paragraph, which currently
    describes namespace as reaching the K8s API only via the URL path — after this change,
    `metadata.namespace` is also present in the submitted object body, forced to the same configured
    value. This paragraph goes stale the moment this change lands if left untouched.
  - Add a short clarifying note distinguishing `bloom-pipeline` (the ServiceAccount that *submits*
    Workflows, named in this README's Provisioning section) from `bloom-workflow`
    (`spec.serviceAccountName`, set inside the submitted object, now loaded from the vendored file) —
    two different ServiceAccounts with similar names. `design.md` documents this distinction, but
    `design.md` is not the place a future reader debugging an RBAC/identity question will naturally
    land; the README's Provisioning section, which already names `bloom-pipeline`, is.

## 3. CI drift-check: tests first (red), then implementation (green)

- [x] 3.1 Write unit tests for the new drift-check script's diff logic (path decided during
      implementation, e.g. `services/workflows/vendored/check_drift.py` or under `scripts/` —
      whichever this repo's script-organization convention favors once checked), against local fixture
      content only (no real network call in the test). Structure the script's fetch behind a small,
      directly-callable function that takes the URL and a timeout as explicit parameters, so these
      tests can monkeypatch `urllib.request.urlopen` (or the wrapping function) rather than needing a
      real network call:
  - a match case (vendored content identical to the "upstream" fixture → passes/exits 0)
  - a mismatch case (differing content → fails/exits non-zero with a message that identifies it as a
    **content drift**, not a fetch problem)
  - a fetch-failure case (the "upstream" fetch raises on every attempt, including the retry → fails
    non-zero with a message that identifies it as a **fetch failure**, distinguishable from the
    mismatch case's message — do not let these two failure modes produce the same output)
  - a retry-then-succeed case — first fetch attempt raises, second succeeds and matches → passes,
    proving the retry doesn't itself introduce a false failure
  - a test asserting the fetch function is actually called with an explicit timeout value (not just
    that the script eventually times out in practice) — the request-level timeout design.md commits to
    is otherwise unverified by anything in this task list
  Confirm all five fail before the script exists.
- [x] 3.2 Implement the script using stdlib `urllib.request` only (matching this repo's other
      root-level Python CI scripts — `verify_env_parity.py`, `check-uv-locks.py` — which are
      stdlib-only and need no separate install step): read the pinned SHA from
      `SLEAP_ROOTS_PIPELINE_REF`, fetch
      `https://raw.githubusercontent.com/talmolab/sleap-roots-pipeline/<SHA>/sleap-roots-pipeline.yaml`
      with an explicit request timeout (matching `k8s_client.py`'s `timeout=15.0` convention), retry
      once on a fetch failure (a fixed short delay, not exponential backoff), diff byte-for-byte
      against the vendored copy, and exit non-zero with a message that clearly distinguishes "fetch
      failed" from "content drifted." Confirm 3.1's tests pass.
- [x] 3.3 Write a `tests/unit/test_pr_checks_*.py`-style test (red — write and confirm it fails against
      the current `pr-checks.yml`, which has no such job) asserting the new job:
  - exists in `.github/workflows/pr-checks.yml`, invoking the 3.2 script
  - has an explicit `timeout-minutes` set
  - has no `continue-on-error: true` and no shell-level exit-code masking (`|| true`, `; exit 0`) on
    its own step — the only realistic masking risk for a plain `run:` script invocation (unlike the
    Trivy `exit-code: '0'`/`'1'` action input this repo's `docker-build` job uses, which is specific to
    that action and doesn't apply to a bare script step)
  - does **not** add or modify a `paths:` filter on `pr-checks.yml`'s shared top-level
    `on.pull_request` trigger — assert the top-level trigger block is unchanged from today's (no
    `paths:` key), so a future edit can't silently scope every other job in the file down to
    `services/workflows/**` while trying to scope just this one
  - is itself scoped to only run when `services/workflows/vendored/**`, `services/workflows/k8s_client.py`,
    or `services/workflows/pyproject.toml` change, via a job-level `if:` condition (e.g. computed by
    `dorny/paths-filter` or an equivalent changed-files step) — not via the top-level trigger
- [x] 3.4 Add the job to `.github/workflows/pr-checks.yml`, following this file's existing job
      conventions (see `build-and-audit`'s `contracts:check` step for the closest analog — a script
      invocation, not inline shell logic) and the job-level `if:` path-scoping from 3.3, leaving the
      file's shared top-level trigger untouched. Confirm 3.3's test passes.
- [x] 3.5 Run `pip-audit` locally against `services/workflows`' updated lockfile
      (`cd services/workflows && uv export --frozen --no-hashes | uvx pip-audit@2.10.0 -r /dev/stdin`)
      to confirm the new direct `pyyaml` dependency (added in 2.4) introduces no known CVE before
      opening the PR — the existing "Audit workflows dependencies" CI step has no `--ignore-vuln`
      escape hatch today, so a surprise here would fail the PR with no easy override. This is a
      pre-PR sanity check, not a red/green step tied to this section's own implementation.
- [x] 3.6 Run the drift-check script locally against the current vendored file/pin and confirm it
      passes before opening the PR.

## 4. Verification gates and follow-up tracking

Run these against the full branch, after every commit above exists locally, immediately before opening
the PR — none of them is scoped to a single commit's diff.

- [x] 4.1 Run `ruff check`, `ruff format --check`, and `black --check` (pinned
      versions matching `.pre-commit-config.yaml`) clean on all new/changed files
      (`k8s_client.py`, `tests/test_k8s_client.py`, the new drift-check script and
      its test). Note: `.pre-commit-config.yaml`'s `files:` scope only actually
      covers `langchain/`, `bloommcp/`, and `services/workflows/` — `scripts/` and
      root `tests/unit/` are clean today by this hand-run check, not by any
      standing pre-commit or CI enforcement (found during PR review).
- [x] 4.2 Run `uv lock --check` for `services/workflows` (or `python scripts/check-uv-locks.py`) to
      confirm the lockfile committed in 2.4 stays in sync.
- [x] 4.3 File (or note in this change's own follow-up, if a formal issue isn't warranted yet) a
      reminder to re-verify `SLEAP_ROOTS_PIPELINE_REF`'s pinned SHA once `sleap-roots-pipeline` PR #49
      merges to that repo's `main` — the content is correct either way (comment-only, doesn't touch
      `spec`), but the pin should ideally reference a commit reachable from `main` once one exists.
- [x] 4.4 (Optional, small) Add a one-line `services/workflows` entry to `openspec/project.md`'s
      "External Packages" section, matching the existing per-service pointer style (e.g. "Video
      worker: see `services/video-worker/pyproject.toml`") — that section currently has no entry for
      this service at all, a pre-existing gap this change is a natural place to backfill.
- [x] 4.5 Run `openspec validate fix-argo-workflow-vendoring --strict` and resolve any issues before
      requesting review.
