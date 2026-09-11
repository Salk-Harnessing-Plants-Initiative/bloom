## Context

`auto-close-issues-on-staging.yml` closes issues at PR-merge time by regex-matching closing
keywords out of the **PR's own title/body** (via the GitHub API), not out of any commit message.
`deploy.yml`'s `deploy-staging`/`deploy-production` jobs are triggered by `push` to `staging`/
`main` and run considerably later than the merge — by the time a migration fails, the issue(s) it
was meant to fix are almost always already closed. So this change is "reopen an already-closed
issue," not "delay closing until deploy succeeds."

## Decisions

### The `if:` condition MUST include `failure()` explicitly — this was wrong in an earlier draft

An earlier draft of this design specified the new step's gate as bare
`steps.migrate_staging.outcome == 'failure'`, reasoning that including `failure()` was
unnecessary. That is a real GitHub Actions bug, caught in review: a step's `if:` expression that
does not itself reference one of `success()`/`failure()`/`always()`/`cancelled()` is implicitly
ANDed with `success()`. By the time this step would run, the job's own status is already
`failure` (the migration step failed), so the implicit `success()` evaluates false and the step
would **never run** — silently defeating the entire change. `deploy.yml` already has the correct
pattern twice, for the exact same step id, and the new step must copy it exactly:

- `deploy.yml:679` — `if: failure() && steps.migrate_prod.outcome == 'failure'`
- `deploy.yml:1359` — `if: failure() && steps.migrate_staging.outcome == 'failure'`

The new reopen step's `if:` MUST be `failure() && steps.migrate_staging.outcome == 'failure'`
(staging) / the `migrate_prod`-keyed equivalent (production) — never a bare outcome check.

### Resolve the issue number via the commits→PRs API, not the commit message

This repo's branch protection squash-merges PRs onto `staging`, and the repo's
`squash_merge_commit_message` setting is `COMMIT_MESSAGES` — the squash commit body is the
concatenation of the branch's own commit messages, **not** the PR description box. Verified
directly against several real `staging` squash-merge commits: the PR body (where `Closes #N`
actually lives, per `auto-close-issues-on-staging.yml`'s own logic) is routinely absent from the
squash commit text entirely. So `github.event.head_commit.message` is not a reliable source.

Instead, the new step calls `GET /repos/{owner}/{repo}/commits/{sha}/pulls`
(`listPullRequestsAssociatedWithCommit`) for the deploy job's `github.sha` to resolve the PR that
produced this commit, and reads that same response's own `title`/`body` fields — this endpoint
already returns them in full, so no separate `github.rest.pulls.get` call is needed or made (an
earlier draft of this doc claimed one; corrected during review, since the fields are already
present on the first response). It then re-runs the exact same regex
(`auto-close-issues-on-staging.yml`'s `/\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s+#(\d+)/gi`)
against that text — fetched via the API inside the script, never interpolated into the script
source or a shell `run:` block via `${{ }}`, matching `auto-close-issues-on-staging.yml`'s own
safe pattern and avoiding any script-injection surface. A referenced number that turns out to be a
PR, not an issue (`issue.pull_request` is present), is skipped, mirroring
`auto-close-issues-on-staging.yml:87-90`'s own guard.

If `commits/{sha}/pulls` returns zero or more than one PR (e.g. a direct push to `staging`, or an
unusual merge history), the step logs this and takes no action, rather than guessing.

### Reopen guard: `state_reason` + `closed_by`, not the timeline API

Only reopen an issue that looks auto-closed, not a manual close that happens to share a number
coincidentally. The guard checks, per referenced issue:

- `issue.state === 'closed'`
- `issue.state_reason === 'completed'`
- `issue.closed_by.login === 'github-actions[bot]'`

All three come from a single `GET /repos/{owner}/{repo}/issues/{issue_number}` call — `closed_by`
already reflects whoever performed the most recent close, with no pagination and no second API
call. An earlier draft proposed the issue *timeline* API instead (to find the actor of the most
recent `closed` event); that's real and would work, but `closed_by` gives the identical guarantee
with less code and one less permission surface to reason about, so this change uses it.

If any check fails, the step logs why that issue number was skipped and takes no action on it —
this is evaluated independently per issue number, so one PR referencing both an auto-closed issue
(reopen) and a separately, manually-closed issue (skip) handles each correctly without one
affecting the other. This mirrors the existing workflow's own conservative bias (a missed reopen
just leaves an issue closed and someone eventually notices, as happened twice already; a wrongful
reopen would be a more confusing, harder-to-diagnose mistake for someone to hit later).

### Misattribution guard: confirm the closing comment names THIS run's own PR

The three-part guard above (`state`/`state_reason`/`closed_by`) confirms an issue was closed *by
the automation*, but not *by which PR*. Caught in review: if PR A closes #900 via a real closing
keyword and PR A's own deploy succeeds, and a later, unrelated PR B also happens to say
"Fixes #900" in its title/body but PR B's *own* deploy then fails for an unrelated reason, the
three-part guard alone would incorrectly reopen #900 and post a comment implying #900's real fix
(PR A's) failed to deploy — false, and exactly the "wrongful reopen is a more confusing mistake"
failure mode this design otherwise tries to avoid.

Fixed by reading back `auto-close-issues-on-staging.yml`'s own comment on the issue — it always
reads `Closed by #<N> (merged into `staging`)...` — and comparing that `<N>` to the *current* run's
own `pr.number` (the PR `commits/{sha}/pulls` resolved earlier in this same step). Only reopen if
they match. This adds one comment-listing call per candidate issue (already covered by the
existing `issues: write` permission grant — comment-listing needs no additional scope) and finds
the most recent matching bot comment by searching from the end of the list backward, so a
since-reopened-and-reclosed issue is judged by its latest close, not a stale earlier one.

**Caught in a later review round**: an earlier version of this fix called
`github.rest.issues.listComments` directly with `per_page: 100` and no further paging. GitHub's
default sort for that endpoint is oldest-first, and the bot's closing comment is posted at
merge time — i.e. among the *newest* comments — so for any issue with more than 100 total
comments, that first (and only-fetched) page would systematically miss it, `closedByPrNumber`
would resolve to `null`, and the guard would skip a perfectly legitimate same-PR reopen. This
fails safe (no wrongful action), but it's a real functional regression, not a theoretical one —
exactly the kind of issue this feature exists to protect (a postmortem-style issue like #780
itself) is exactly the kind that accumulates a long comment thread. Fixed by paging through
`github.paginate` instead of a single bounded fetch, so the search covers the issue's full
comment history regardless of length.

### Every outbound API call is wrapped with an explicit timeout

Caught in review: this runner class has a documented history (bloom#616) of *silently dropping*
outbound traffic rather than refusing it — a `ufw` default-deny-outbound rule typically blackholes
packets instead of returning a fast, loud connection-refused error. Without an explicit timeout, a
blocked call here would hang for the job's full `timeout-minutes: 30` ceiling, holding the shared
`concurrency: group: deploy-bloom` lock — the single Salk-server deploy slot — for half an hour on
every future push, precisely when a human is already dealing with a failed deploy. Every
`github.rest.*` call in the new step is wrapped in a small `Promise.race`-based `withTimeout`
helper (15s per call) that rejects with a clear "`<label> timed out after <ms>ms`" error, caught by
the existing try/catch layers and logged via `core.warning` rather than left to the job's own
30-minute ceiling to eventually notice.

**Caught in a later review round, worth stating plainly rather than leaving implicit**:
`Promise.race` only decides which settlement the script's `await` sees first — it does not cancel
the losing promise. Octokit is never given an `AbortSignal` here, so a genuinely stalled request
keeps running in the background after the timeout wins the race; the *script's own control flow*
recovers in 15s, but the underlying socket's actual teardown is still bounded by whatever the
OS/network layer eventually does with it (e.g. a TCP connect-phase drop typically resolves on its
own within a couple of minutes, not indefinitely, but also not exactly 15s either). This is the
correct fix for the failure mode that motivated it — the *step* stops waiting and logs a clear
diagnosis instead of silently holding the concurrency lock for the full job timeout — but it is a
bound on this script's own progress, not a hard wall-clock guarantee on the underlying HTTP call.
The helper also does not `clearTimeout` its losing timer once the real call wins (the
overwhelmingly common case) — harmless in this short-lived Action process, but noted here rather
than silently left as an unexamined loose end.

### The comment names which environment failed, without the two jobs' scripts diverging

Caught in review: the original comment text was deliberately environment-agnostic (to keep the
two jobs' scripts byte-identical), which meant a reader couldn't tell a staging failure from a
production one without clicking through to the linked run — a real ergonomic gap given the two
carry very different urgency. Fixed without reintroducing per-job script variants: `context.job`
(a real `@actions/github` context field, populated from `GITHUB_JOB`) is literally the running
job's id — `"deploy-staging"` or `"deploy-production"` — so `context.job.replace(/^deploy-/, '')`
derives the environment label at runtime from data the script already has, rather than from a
hardcoded per-copy string. The two jobs' `with: script:` bodies are therefore now genuinely
byte-identical (asserted directly by a test — see the "Test strategy" section below), not merely
identical-by-inspection as they were before this fix.

### The issue is reopened before the comment is posted, not after

Caught in review: the original order posted the "reopening" comment first, then called
`issues.update({state: 'open'})`. If the `update` call failed partway through (a transient error,
a rate limit) after the comment had already posted, the issue would be left closed but bearing a
comment claiming it had been reopened — and a later run hitting the same issue would repeat the
mistake, since the guard's preconditions (`state === 'closed'`, etc.) would still hold. Swapping
the order makes a partial failure strictly milder: if `update` fails, nothing externally visible
happens yet (safely retried on a future run); if `update` succeeds but `createComment` fails, the
issue is at least genuinely open, and the guard's own `state === 'closed'` check then prevents any
future duplicate comment on it.

### Scope the failure condition to the migration step specifically — and distinguish skipped from failed

The "Apply database migrations" step is itself conditionally gated
(`deploy.yml:1321` — `if: steps.check_migrations_staging.outputs.pending != '0'`), so its outcome
can be `success`, `failure`, **or `skipped`** (0 pending migrations — a common, correct, non-error
state). The new step's `if:` (see above) checks `outcome == 'failure'` specifically, which already
excludes `skipped` — a skipped migration step must never trigger a reopen. Smoke-test failures,
Caddy crash-loops, cert-issuance timeouts, etc. are a different, already-visible class of problem
(the deploy run itself shows red, with no auto-closed issue silently masking it) — reopening
issues for every possible deploy-job failure reason would be scope creep beyond what bloom#780
describes.

### `permissions:` block: `contents: read`, `issues: write`, `pull-requests: read`

`deploy.yml` currently has no `permissions:` block anywhere and so inherits the repository's
`default_workflow_permissions: write` setting — broad enough for this already, but implicitly and
fragilely (a repo-level setting change would silently break or over-grant this). The job(s) that
gain the new step get an explicit block. `pull-requests: read` is required because the step calls
`commits/{sha}/pulls` — `auto-close-issues-on-staging.yml` itself already declares this permission
even though it never calls a pulls API (it only reads the trigger's own payload), so a step that
*does* call one must declare it too, or the call 403s silently on the one path nobody is watching
synchronously.

### The whole script body is wrapped in a top-level try/catch, not just the per-issue loop

`auto-close-issues-on-staging.yml` wraps its per-issue close action in try/catch
(`:108-112`, "don't fail the whole run if one number is bogus"). This change's script adopts the
same per-issue resilience, and additionally wraps the top-level `commits/{sha}/pulls` call in its
own try/catch — an unhandled exception there (rate limit, transient network blip, a
missing-permission 403) would otherwise fail this already-on-the-failure-path step with a bare,
unexplained stack trace, exactly when a human is trying to understand why an issue silently stayed
closed. Both layers log via `core.warning`/`core.info` and exit cleanly rather than throwing.

### Documented pre-merge checklist lives in `contracts/README.md`, not `openspec/AGENTS.md`

An earlier draft placed this in `openspec/AGENTS.md` "or a document it references" — a placement
decision deferred rather than made, and wrong on inspection: `openspec/AGENTS.md` is exclusively
about the OpenSpec proposal/change lifecycle, has no existing migration-conventions section to
extend, and is a scope mismatch for SQL/SSH-specific operational guidance. `contracts/README.md`
already has a "Re-pin procedure" section, and `contract-pinning`'s own existing spec requirement
already mandates that this file document "the pinned version, the re-pin procedure, and the rule
that a re-pin which only re-stamps the schema `$id` is a structural no-op" — i.e. this is already
the established, spec-mandated home for guidance about this exact class of migration. This change
adds one new step to that existing procedure (a pre-merge check against real target-environment
state, for any re-pin whose migration carries a cutover guard) and cites
`supabase/migrations/20260706170000_cyl_writeback_contract_a3.sql` and
`supabase/migrations/20260831130000_cyl_writeback_contract_a7.sql` by path as the two concrete
examples, rather than restating their `DO $guard$` pattern in prose.

Direction 1 from bloom#780 ("check real target-environment state before merging a cutover-guard
migration") cannot be a `pull_request`-triggered CI check today: `deploy.yml` scopes
`DEPLOY_HOST`/`DEPLOY_USER`/the SSH key behind `environment: staging`/`environment: production`,
reachable only from a `push` to `staging`/`main` or an explicit `workflow_dispatch` — never from a
`pull_request` event, by GitHub's own environment-protection design (confirmed: `deploy.yml`'s
`on:` block has no `pull_request` trigger at all). Building a new, separately-secreted
`pull_request` workflow just for this would be a meaningfully larger, riskier change (a new
automated path with deploy-adjacent SSH access, reachable from any PR) than the actual problem
calls for. `contracts/README.md`'s new step documents this as a manual step, run via SSH + the
same `scripts/deploy_run_supabase.sh` / in-container `psql` pattern `deploy.yml` itself already
uses for schema grants — and states plainly that nothing currently blocks a PR that skips it.

### Test strategy mirrors `test_auto_close_workflow_shape.py` — and says so in its own docstring

The new `deploy.yml` step, like `auto-close-issues-on-staging.yml`, cannot be exercised by PR CI
(it only ever runs after landing on `staging`/`main`, on `push`, and only on a real migration
failure). The same mitigation applies: a unit test parses `deploy.yml`'s YAML and the embedded
`github-script` body, and asserts shape invariants (permissions, the SHA-pinned action, the
`state_reason`/`closed_by` guard, the step-id-scoped `if:` condition including `failure()`) plus
regex-extraction tests reusing `auto-close-issues-on-staging.yml`'s existing `KEYWORD_CASES`
table, parametrized over both jobs from the start. The new test file's module docstring states the
same causal reasoning `test_auto_close_workflow_shape.py`'s docstring does — the step only ever
runs on `push` after a real failure against real data, so this unit test is the only pre-merge
gate a regression in it would ever hit.

This repo has no dedicated `actionlint`/`yamllint` CI job (confirmed absent from `pr-checks.yml`
and `.pre-commit-config.yaml`) — the same gap the already-archived `fix-kong-reload-on-deploy`
change (`openspec/changes/archive/2026-08-09-fix-kong-reload-on-deploy/tasks.md:25`) flagged for
an equivalent `deploy.yml` edit. This change relies on the same mitigation it did: the new test's
own `yaml.safe_load` parse (which fails loudly on malformed YAML) plus a manual diff read-through
before merge — not a new linter.

The safety-critical guard logic (state_reason + closed_by + the misattribution check) is
shape-tested in `tests/unit/` (substring/pattern presence in the embedded script) for the same
reason nothing else here can run in PR CI. That test suite is not, on its own, proof the JS
actually behaves correctly — it can only prove the right tokens are present. During review, the
extracted script was additionally syntax-checked (`node --check`) and executed directly against a
small hand-built mock of the GitHub API (happy-path reopen, the misattribution guard correctly
blocking a cross-PR reopen, and the timeout wrapper firing at ~15s instead of hanging) — this
one-time verification is not itself part of the committed test suite (there is no Node test
runner in this repo's CI), but gives real confidence beyond static shape-matching that the
Section-2 tests alone couldn't provide.

Because the environment label is now derived from `context.job` at runtime rather than
hardcoded per job copy (see "The comment names which environment failed" above), the two jobs'
`with: script:` bodies are genuinely byte-identical — asserted directly by
`test_scripts_are_byte_identical_across_jobs`, replacing what was previously only a manual
pre-merge diff instruction.

**Caught by mutation-testing in a later review round**: two of the shape assertions were
demonstrably too loose to catch a real regression, not merely theoretically weak. Deliberately
breaking the misattribution guard (`if (closedByPrNumber !== pr.number)` → an always-false
condition, making the guard dead code) still passed `test_script_guards_against_misattributed_reopen`,
because it only checked that the strings `"Closed by #"` and `!==\s*pr\.number` appeared
*somewhere* in the script, not that the check was live. Likewise, unwrapping just one of the five
`github.rest.*` calls from `withTimeout` still passed `test_script_wraps_api_calls_with_a_timeout`,
because it only checked `Promise.race` appeared at least once, not that every call site used it.
Both were tightened: the misattribution test now asserts the exact live conditional string, and
the timeout test now asserts a 1:1 count between `github.rest.` call sites and `withTimeout(`
wraps — closing the specific gaps the mutation testing demonstrated, not just the ones that were
merely hypothesized.

### Step placement, and this runner's outbound-HTTPS track record

The new step is inserted immediately after "Show migration status on failure" and before
"Migration summary" in each job — a deterministic slot next to the other failure-diagnostic
steps, rather than an unspecified "wherever ordering is cleanest." An earlier draft of this doc
claimed this change is "the first to require the self-hosted `salk-network` runner to reach
`api.github.com` over HTTPS" — checked directly against the file during review and found false:
the pre-existing "Cloudflare API token preflight" step already `curl`s
`https://api.cloudflare.com/...` directly from this same runner, no SSH involved. That's actually
better evidence than the false claim it replaces — a real, already-working precedent for direct
outbound HTTPS from this exact runner class, not just an inference from "it needs HTTPS to receive
jobs at all." Combined with the explicit per-call timeout (above), a stalled connection here now
fails loud and fast rather than silently consuming the job's full ceiling; if it ever does need
debugging, the fix is the same one bloom#616 already applied (an explicit ufw allow rule), not a
new investigation.

## Risks / Trade-offs

- **The `deploy-production` copy of this step is, in this repo's actual release flow, usually a
  no-op — caught in review, and worth stating plainly rather than leaving implied.** `main` only
  ever receives pushes via periodic `staging`→`main` promotion PRs, and this repo's convention
  for those promotions is a real merge commit, not a squash (confirmed against real history: a
  squash would drop the parent link the promotion is specifically meant to preserve). For a merge
  commit, `commits/{sha}/pulls` resolves to the *promotion* PR, not the original feature PR — and
  promotion PR bodies reference issues in a parenthetical `(#N)` style that the shared
  closing-keyword regex deliberately does not match (the same `(#N)` case is already in
  `KEYWORD_CASES` as a documented non-match). Even in the rare case a promotion PR did contain a
  real closing keyword, the referenced issue was typically never auto-closed at merge-into-`main`
  time in the first place, since `auto-close-issues-on-staging.yml` only fires on merge into
  `staging`. Net effect: `deploy-production`'s step will almost always find one associated PR
  with zero keyword matches and log a clean no-op. This is accepted, not fixed, for this change:
  walking a merge commit's constituent commits to find the *original* feature PRs would be a
  meaningfully larger, more error-prone change than the problem calls for, and a no-op is this
  design's own safe-side default everywhere else. The step is kept on `deploy-production` anyway
  as a genuine (if currently rare-case) safety net — a squash-merged promotion, a direct push to
  `main`, or a future change to the promotion convention would all make it actually fire — rather
  than removed and having to be re-added later.
- **PR merged via `gh pr merge --squash` from outside the normal flow, or a direct push to
  `staging`** — `commits/{sha}/pulls` would return no associated PR, or more than one. The step
  logs this and takes no action rather than guessing; this is the same "log and continue on one
  bogus number" bias `auto-close-issues-on-staging.yml` already uses for a deleted/transferred
  issue.
- **A `workflow_dispatch` run manually dispatched against an old commit** could in principle
  reopen a long-resolved issue if that old commit's migration now fails for reasons unrelated to
  its original fix (e.g. environment drift since the commit was current) — `context.sha` still
  correctly reflects whatever ref was selected at dispatch time, so this is not a bug in the
  resolution logic, just a sharp edge of manually re-running old state. Not mitigated here;
  worth knowing about before dispatching against anything but the current tip.
- **The misattribution guard's skip is silent** — when a referenced issue was auto-closed by a
  *different* PR than the one whose deploy just failed, the only trace is a `core.info` log line
  in the Actions run, not any comment on the issue itself. Unlike the false-alarm risk above (where
  staying silent is deliberate, since a wrong *action* would be the worse mistake), a purely
  informational "this PR referenced #N, but #N was closed by a different PR, so I'm leaving it
  alone" comment would carry no such risk — it only restates data the script already fetched. Not
  implemented in this change (would add another `createComment` call and its own test coverage for
  a narrow, infrequent case — two different PRs' closing keywords colliding on the same issue
  number, with the second's deploy separately failing); accepted as a real, if minor, follow-up
  candidate rather than solved now.
- **The reopened issue's comment could itself look like a false alarm on a transient failure**
  (e.g. a network blip during the SSH step, unrelated to the migration's own correctness). The
  comment names the failed run and step so a human reading it can tell a transient infra hiccup
  from a real guard trip — the fix intentionally does not try to distinguish these
  programmatically, since that judgment is exactly what a human re-triage is for. The comment does
  **not** attempt to quote the migration's actual SQL error text — nothing in `deploy.yml` today
  captures that into a step output a later step could read, and adding that plumbing is out of
  scope for this change.
- **Rate limiting** on the new API calls is not specially handled beyond the top-level try/catch
  logging and exiting cleanly — accepted because this step only ever runs on an already-failed,
  low-volume path (once per failed deploy, not per PR).
- **The documented pre-merge checklist has no enforcement mechanism** — nothing blocks a PR that
  skips it. This is accepted: the alternative (a new secreted CI path) was rejected above, and the
  automation half of this change is the actual safety net for exactly this failure mode.
