## 1. Documented pre-merge checklist (no code; independent of §2-4, safe to land first)

- [x] 1.1 Add as a new step 5 (after the existing step 4, "Run `npm run contracts:check`...") in
      `contracts/README.md`'s "Re-pin procedure" section: for any re-pin whose migration adds a
      data-dependent cutover guard, check the real state of every target environment the guard
      could plausibly trip in (staging always; production too if the guarded condition could
      exist there) via SSH before merging, and fold any reconciliation into the same PR. Cite
      `supabase/migrations/20260706170000_cyl_writeback_contract_a3.sql` and
      `supabase/migrations/20260831130000_cyl_writeback_contract_a7.sql` by path as the two
      concrete precedents. State plainly that this is manual (PR CI has no route to
      staging/production secrets) and is not enforced by any CI gate.
- [x] 1.2 Cross-check the new text against `specs/contract-pinning/spec.md` in this change (the
      MODIFIED requirement) — the documented content must satisfy both new scenarios there
      (instructs the pre-merge check; explicitly disclaims CI enforcement).

## 2. Regression-guard test for the reopen step (TDD: RED before the step exists)

Precedent to mirror throughout: `tests/unit/test_auto_close_workflow_shape.py` — read its module
docstring and structure before writing the new file; the new file's own docstring must state the
same causal reasoning (this step only ever runs on `push`, after a real migration failure against
real data, so PR CI structurally cannot exercise it — this unit test is the only pre-merge gate).

- [x] 2.1 (RED) Write `tests/unit/test_deploy_reopen_on_migration_failure_shape.py`. Parametrize
      every assertion over `[("deploy-staging", "migrate_staging"), ("deploy-production",
      "migrate_prod")]` from the start (not two near-duplicate test bodies) so the same test
      function covers both jobs symmetrically. Assert, per job:
      - the job declares `permissions: { contents: read, issues: write, pull-requests: read }`
      - a step exists whose `if:` string is exactly `failure() && steps.<id>.outcome ==
        'failure'` for that job's migration step id — assert the literal substring, including
        `failure() &&`; do NOT assert merely that the step id is referenced, since a condition
        like `steps.<id>.outcome != 'success'` would also match a substring-only check while being
        wrong (it would fire on `skipped` too)
      - the step uses a SHA-pinned `actions/github-script` (or equivalent), matching this repo's
        supply-chain convention
      - the embedded script's issue-closing-keyword regex is byte-identical to
        `auto-close-issues-on-staging.yml`'s own (extract both and assert equality)
      - the embedded script checks `state === 'closed'`, `state_reason === 'completed'`, AND
        `closed_by.login === 'github-actions[bot]'` (or equivalent JS) before reopening — assert
        all three substrings independently; a real GitHub issue can have `state_reason:
        'completed'` from a human close (confirmed against real repo data: bloom#685 was closed
        by a human with `state_reason: completed`), so `state`/`state_reason`/`closed_by` are each
        independently load-bearing, and a test that only checks two of the three would pass an
        implementation that reopens an issue a human closed
      - the embedded script's exact-string `if:` assertion (above) is itself the skip-vs-fail
        test: additionally assert directly that the extracted `if:` string, when the literal
        outcome value is substituted, is true for `outcome: 'failure'` and false for
        `outcome: 'skipped'` and `outcome: 'success'` (a plain Python string/boolean check on the
        parsed condition, not a GitHub Actions expression evaluator) — this is the test that
        would fail if a future edit weakened the condition back toward the round-1 bug
      - the embedded script logs and returns without error when zero closing-keyword matches are
        found in the PR's title/body, mirroring `auto-close-issues-on-staging.yml:72-75`'s own
        `numbers.size === 0` branch — assert the equivalent zero-length check exists
      - the embedded script contains a length check on the `commits/{sha}/pulls` result before
        proceeding (guards the zero-or-multiple-PRs case)
      - the embedded script skips a matched number when the fetched issue has `.pull_request` set
      - the embedded script wraps both the top-level PR-resolution call and the per-issue
        processing in their own try/catch (assert two distinct `try`/`catch` regions, not one)
      - the per-issue loop body contains no `return`/`break` statement on the skip path — only
        `continue`/fall-through to the next iteration — so that one issue's skip or reopen outcome
        structurally cannot affect any other issue's in the same run (this is the test proxy for
        the mixed-multi-issue-outcome scenario: a live two-issue integration test isn't feasible
        in PR CI for the same reason nothing else here is, so this shape assertion is what stands
        in for it)
      - the comment body template string references both a run-identifying token (e.g.
        `context.runId` or the run's HTML URL) and the failed step's name, so a regression that
        drops either from the comment text is caught here rather than only in the implementation
        task
      Run the suite now and confirm every new test fails for the expected reason (`deploy.yml` has
      none of this yet) — not from a typo, import error, or an off-by-one on a not-yet-existing
      step. Confirm the test asserts absence as clearly as it will later assert presence (e.g. the
      `if:`-string check should fail with a clear "step not found" message, not a raw exception).
- [x] 2.2 (RED) Extend the same test file with the regex-behavior cases: reuse (import or
      duplicate — decide based on whether the two workflows' regexes are extracted from
      sufficiently similar YAML paths to share a helper) the exact `KEYWORD_CASES` table from
      `test_auto_close_workflow_shape.py` and assert the new step's extracted regex produces
      identical matches. Confirm this fails until §3 lands (the regex doesn't exist in
      `deploy.yml` yet).
- [x] 2.3 Add the module docstring (per the precedent note above) explaining why this is a shape
      test and the sole pre-merge gate for this step's logic, mirroring
      `test_auto_close_workflow_shape.py`'s own docstring in depth, not just conclusion.

## 3. Implement the reopen step for both jobs (GREEN)

Landed as one commit for both jobs together, not staging-then-production — design.md is explicit
these are deliberately the same mechanism, not a variant, and splitting them would leave the
parametrized §2 suite red between two separate commits for no benefit.

- [x] 3.1 Add an explicit `permissions: { contents: read, issues: write, pull-requests: read }`
      block to both the `deploy-staging` and `deploy-production` jobs in
      `.github/workflows/deploy.yml`.
- [x] 3.2 Add a new step to `deploy-staging`, inserted immediately after "Show migration status
      on failure (staging)" and before "Migration summary (staging)" (a deterministic, log-order-
      sensible insertion point — not "wherever ordering is cleanest"), gated `if: failure() &&
      steps.migrate_staging.outcome == 'failure'` (the exact form, matching the existing sibling
      steps' pattern at `deploy.yml:1359`), that:
      - wraps the following in a top-level try/catch, logging via `core.warning` and returning
        cleanly on error rather than throwing
      - resolves the triggering PR via `GET /repos/{owner}/{repo}/commits/{sha}/pulls` for
        `github.sha`; if the result length is not exactly 1, logs and returns
      - re-extracts issue numbers from that PR's live `title`/`body` (fetched via
        `github.rest.pulls.get`, never `${{ }}`-interpolated) via the shared closing-keyword regex;
        if zero numbers are found, logs and returns (mirroring
        `auto-close-issues-on-staging.yml:72-75`)
      - for each number, wrapped in its own try/catch (mirroring
        `auto-close-issues-on-staging.yml:80-112`, falling through to the next number on error or
        on a skip — no `return`/`break` on any per-issue path): fetches the issue; skips if
        `issue.pull_request` is present; skips (logging why) unless `state === 'closed'` AND
        `state_reason === 'completed'` AND `closed_by?.login === 'github-actions[bot]'`; otherwise
        calls `issues.update({state: 'open'})` and `issues.createComment(...)` naming the run URL
        (`context.runId` / the run's HTML URL) and the failed step, stating that auto-close
        outran deploy verification — phrase the comment text environment-agnostically (no literal
        "staging"/"production" string) so §3.3's reuse is byte-identical, not just logic-identical
- [x] 3.3 Add the structurally identical step to `deploy-production`, inserted at the same
      relative position ("Show migration status on failure (production)" →  new step → "Migration
      summary (production)"), gated `if: failure() && steps.migrate_prod.outcome == 'failure'`
      (matching `deploy.yml:679`'s pattern), reusing the same script logic (a shared composite
      action or reusable workflow is not required for this change's scope — duplicating the
      ~30-line script inline in both jobs, as `auto-close-issues-on-staging.yml` does for its own
      single job, is acceptable and matches this repo's existing style). Before finalizing, diff
      the two script bodies line-by-line and confirm neither contains a hardcoded
      "staging"/"production" string, run-id variable name, or step-id typo left over from
      copy-pasting — the comment text and logic must be identical except for the `if:` step id.
- [x] 3.4 Run `tests/unit/test_deploy_reopen_on_migration_failure_shape.py` — confirm every case
      from §2.1/§2.2 now passes (GREEN) for both jobs.

## 4. Validate and land

- [x] 4.1 `openspec validate harden-cutover-guard-deploy-gaps --strict` — resolve every issue it
      raises.
- [x] 4.2 Run the full `tests/unit/` suite locally (`uv run --extra test pytest tests/unit/`) —
      confirm no regression in `test_auto_close_workflow_shape.py` or elsewhere.
- [x] 4.3 Confirm `deploy.yml` still parses as valid YAML/Actions syntax after the edit. This repo
      has no dedicated `actionlint`/`yamllint` CI job (confirmed absent from `pr-checks.yml` and
      `.pre-commit-config.yaml` — the same gap the already-archived `fix-kong-reload-on-deploy`
      change flagged for an equivalent edit); rely on §2's `yaml.safe_load`-based test passing
      plus a manual read-through of the diff, not a new linter.
- [x] 4.4 `/pre-merge`, then `/pr-description` and open the bundled PR (proposal + implementation)
      against `staging`, linking bloom#780. Do not merge — leave that to the user. (PR #807)

## 5. Address PR #807 review findings (5-agent adversarial self-review)

The author (this session) ran `/review-pr` against the open PR and applied its findings, following
the same TDD discipline as the original build: RED tests added and confirmed failing before each
implementation change.

- [x] 5.1 (RED) Extend `tests/unit/test_deploy_reopen_on_migration_failure_shape.py` with new
      assertions: `context.job`-derived environment label present; `issues.update` precedes
      `issues.createComment` in the per-issue loop; a `Closed by #<N>` misattribution guard
      compares against `pr.number`; every API call is wrapped in a `Promise.race`-based timeout;
      the two jobs' scripts are byte-identical (no longer just a manual diff); the existing
      run/step-name comment assertion also checks for the literal "Apply database migrations"
      string. Confirmed 8 of the new/strengthened cases RED (the byte-identical and
      step-name-in-comment cases were already true and stayed green — no implementation change
      needed for those two).
- [x] 5.2 (GREEN) Implemented in `.github/workflows/deploy.yml`, identically in both jobs (script
      bodies are now genuinely byte-identical, not just similar): derive the environment label
      from `context.job`; reorder `issues.update` before `issues.createComment`; add the
      `issues.listComments` + `Closed by #<N>` misattribution guard; wrap every `github.rest.*`
      call in a `withTimeout` helper (15s, `Promise.race`-based). Confirmed all 91 cases GREEN.
- [x] 5.3 Verified the extracted script directly, beyond shape-testing: `node --check` on both
      jobs' extracted scripts (syntax-valid), and executed against a hand-built mock of the
      GitHub API for three scenarios — happy-path reopen (comment correctly names the
      environment), the misattribution guard correctly blocking a cross-PR reopen, and the
      timeout wrapper firing at ~15s instead of hanging.
- [x] 5.4 Corrected `design.md`: removed the false claim that the step calls
      `github.rest.pulls.get` (it doesn't — `listPullRequestsAssociatedWithCommit`'s own response
      already carries `title`/`body`); removed the false claim that this change is "the first" to
      need direct HTTPS from this runner (the pre-existing "Cloudflare API token preflight" step
      already does, and is now cited as the real precedent); documented the new misattribution
      guard, timeout wrapper, and env-aware-comment-via-`context.job` decisions; added the
      `deploy-production` no-op limitation and the `workflow_dispatch`-against-a-stale-commit
      risk to the Risks section.
- [x] 5.5 Tightened `contracts/README.md`'s re-pin-procedure step 5 (and the matching
      `contract-pinning` spec requirement text) so skipping the production check requires an
      affirmative, checkable reason ("I confirmed from schema/migration history the guarded
      condition cannot exist there"), not an unverified "probably fine."
- [x] 5.6 Updated `specs/deploy-migrations/spec.md`: extended the PR-resolution requirement to
      cover the timeout wrapper (+ a new scenario); renamed and extended the reopen-guard
      requirement to cover the misattribution check and the update-before-comment ordering (+ new
      scenarios: misattributed-reopen-is-blocked, hung-call-times-out).
- [x] 5.7 `openspec validate harden-cutover-guard-deploy-gaps --strict` and the full
      `tests/unit/` suite — both green after all of the above.

## 6. Address round-3 review findings (second /review-pr pass, post-fix)

A second `/review-pr` pass specifically re-verified the round-5 (§5) fixes and hunted for anything
they introduced. 3 of 5 reviewers independently found the same real regression; one reviewer
additionally demonstrated, by actually mutating the code and rerunning the suite, that two of the
round-5 shape tests were too loose to catch a broken implementation.

- [x] 6.1 (RED) Strengthened `test_script_guards_against_misattributed_reopen` to assert the
      exact live conditional string (not a loose substring/regex) — confirmed it now fails
      against a deliberately dead-coded guard (`if (false && closedByPrNumber !== pr.number)`),
      which the prior version of this test did not catch.
- [x] 6.2 (RED) Strengthened `test_script_wraps_api_calls_with_a_timeout` to assert a 1:1 count
      between outbound call sites and `withTimeout(` wraps, not just that the wrapper exists
      somewhere — confirmed it now fails against a script with one of five calls left unwrapped,
      which the prior version did not catch.
- [x] 6.3 (RED) Added `test_script_paginates_comments_to_find_the_closing_comment`, asserting the
      comment lookup uses `github.paginate(...)` — confirmed RED against the then-current
      single-page `listComments({ per_page: 100 })` fetch.
- [x] 6.4 Hardened `test_state_is_reopened_before_the_comment_is_posted` to assert each call
      appears exactly once in the loop body before checking their order (caught and fixed a
      self-inflicted decoy: the `withTimeout` label strings, e.g.
      `` `issues.update(#${issue_number})` ``, contained the same substring the test was
      searching for — fixed by matching the fully-qualified `github.rest.issues.update(` call
      instead).
- [x] 6.5 (GREEN) Implemented in `.github/workflows/deploy.yml`, identically in both jobs:
      replaced the single-page `github.rest.issues.listComments({ per_page: 100 })` fetch with
      `github.paginate(github.rest.issues.listComments, { per_page: 100 })`, so the misattribution
      guard's comment search covers an issue's full history regardless of length. Confirmed all
      93 cases GREEN.
- [x] 6.6 Verified beyond the shape tests: re-ran the mutation tests that motivated 6.1/6.2 against
      the actual file (confirmed each fails exactly as expected, then restored the file), and ran
      a new mock-execution scenario with 251 simulated comments spanning 3 pages — confirmed the
      real script correctly finds the closing comment on the third page and reopens the issue.
- [x] 6.7 Corrected `design.md`: removed two more leftover `pulls.get` mentions (the permissions
      and try/catch sections) that survived the §5 fix round unnoticed; documented the pagination
      fix and the underlying "why oldest-first-by-default silently breaks this" reasoning;
      disclosed that the timeout wrapper stops the *script's* wait but doesn't cancel the
      underlying HTTP request (Octokit gets no `AbortSignal`); noted the `withTimeout` helper
      never `clearTimeout`s its losing timer; added the misattribution guard's silent-skip UX gap
      to the Risks section as an accepted, undone follow-up candidate.
- [x] 6.8 Updated `specs/deploy-migrations/spec.md`: extended the reopen-guard requirement to
      require pagination (+ a new scenario: closing comment found despite a long comment
      history); fixed the same stale `pulls.get` reference in the permissions requirement.
- [x] 6.9 `openspec validate harden-cutover-guard-deploy-gaps --strict` and the full `tests/unit/`
      suite — both green after all of the above.

## 7. Post-merge (not part of this PR)

- [ ] 7.1 After merge and a real deploy run: `openspec:archive harden-cutover-guard-deploy-gaps`.
- [ ] 7.2 Confirm bloom#780 itself is closed once the fix is live. If closing manually rather than
      via the PR's own `Closes #780` auto-close, get the user's explicit go-ahead for that specific
      GitHub write before posting anything — same convention as every other GitHub write this
      session. Applying this change's own lesson to itself: since this particular PR's merge
      doesn't gate on a future deploy the way a migration would, "merged" is sufficient evidence
      here (there is no deploy-time behavior to wait on beyond the merge itself) — this step is
      about not skipping the authorization step, not about waiting for a deploy.
