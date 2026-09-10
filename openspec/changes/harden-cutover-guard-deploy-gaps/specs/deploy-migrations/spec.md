## ADDED Requirements

### Requirement: A migration failure MUST resolve the triggering PR and re-extract its referenced issues

When the "Apply database migrations" step's own outcome is `failure` (not `skipped` or `success`) in `deploy.yml`'s `deploy-staging` or `deploy-production` job, a subsequent step's `if:` SHALL be `failure() && steps.<migration-step-id>.outcome == 'failure'` — never a bare outcome comparison, since a step `if:` without an explicit `failure()`/`success()`/`always()`/`cancelled()` term is implicitly ANDed with `success()`, which is already false by the time this step would run. That step SHALL resolve the pull request associated with the triggering commit via `GET /repos/{owner}/{repo}/commits/{sha}/pulls` for `github.sha` (reading `title`/`body` from that same response, without a separate PR-get call), and SHALL log the condition and take no further action if zero or more than one PR is associated with that commit. For exactly one associated PR, the step SHALL re-extract same-repo issue numbers from that PR's live `title`/`body` (never interpolated via `${{ }}` into the script source or a shell command) using the same closing-keyword regex `auto-close-issues-on-staging.yml` uses (`\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s+#(\d+)`, case-insensitive), and SHALL skip any matched number whose referent is itself a pull request rather than an issue, mirroring `auto-close-issues-on-staging.yml`'s own guard. Every outbound GitHub API call the step makes SHALL be wrapped with an explicit timeout (bounded, rather than left to the enclosing job's own multi-minute ceiling), so a connection this runner's network silently drops fails loud and fast instead of holding the shared deploy concurrency lock for the remainder of the job's timeout.

#### Scenario: A failed staging migration resolves its triggering PR

- **GIVEN** a PR merged into `staging`, producing commit `sha1`
- **AND** the resulting push's `deploy-staging` job's "Apply database migrations (staging)" step (id `migrate_staging`) fails
- **WHEN** the workflow evaluates the new step's `if:` condition
- **THEN** it is exactly `failure() && steps.migrate_staging.outcome == 'failure'`, and the step runs
- **AND** the step resolves the merged PR via `commits/sha1/pulls` and extracts issue numbers from its live title/body

#### Scenario: A skipped migration step (0 pending migrations) never triggers the new step, even if a later step fails

- **GIVEN** `deploy-staging`'s "Check for pending migrations (staging)" step reports 0 pending
- **AND** "Apply database migrations (staging)" is therefore skipped (`outcome: skipped`), not run
- **AND** a later, unrelated step (e.g. the smoke test) fails
- **WHEN** the workflow evaluates the new step's `if:` condition
- **THEN** it evaluates false, because `steps.migrate_staging.outcome == 'failure'` is false for a `skipped` outcome
- **AND** the new step does not run

#### Scenario: A non-migration deploy failure does not trigger the new step

- **GIVEN** the same deploy run's "Apply database migrations" step succeeds
- **AND** a later step (e.g. the smoke test, or the Let's Encrypt certificate check) fails instead
- **WHEN** the workflow evaluates the new step's `if:` condition
- **THEN** it evaluates false, because it is gated on the migration step's own outcome specifically, not on general job failure

#### Scenario: No associated PR, or more than one, is found for the failing commit

- **GIVEN** the commit that triggered the failing deploy has zero or more than one PR associated with it via `commits/{sha}/pulls` (e.g. a direct push to `staging`, or an unusual merge history)
- **WHEN** the step runs
- **THEN** it logs this condition and takes no further action, rather than guessing which PR or issue is responsible

#### Scenario: A matched number that refers to a PR, not an issue, is skipped

- **GIVEN** the failing PR's title/body contains a closing-keyword reference to `#950`
- **AND** `#950` is itself a pull request, not an issue
- **WHEN** the step evaluates `#950`
- **THEN** it skips `#950` without attempting to reopen or comment on it

#### Scenario: A PR referencing no closing keyword produces no action

- **GIVEN** the failing PR's title/body contains no text matching the closing-keyword regex
- **WHEN** the step runs
- **THEN** it logs that no same-repo closing references were found and takes no further action, without error

#### Scenario: A hung API call times out rather than blocking the job

- **GIVEN** an outbound call the step makes (PR resolution, or a per-issue call) never receives a response, because the runner's network silently drops the connection rather than refusing it
- **WHEN** the wrapped call exceeds its bounded timeout
- **THEN** it rejects with an error naming the call and the timeout duration
- **AND** the enclosing error handling logs a warning and the step completes well within the job's own multi-minute timeout, rather than holding the shared deploy concurrency lock until that ceiling is reached

### Requirement: Reopen and comment actions MUST be limited to issues closed by the auto-close automation, for THIS PR specifically

For each issue number re-extracted from the failing PR (per the PR-resolution requirement above), the step SHALL fetch that issue via `GET /repos/{owner}/{repo}/issues/{issue_number}` and SHALL consider it a reopen candidate only if all of the following hold: `state` is `closed`, `state_reason` is `completed`, and `closed_by.login` is `github-actions[bot]`. A reopen candidate SHALL be reopened and commented on only if an additional check passes: the step SHALL read the issue's comments — paginating through its full comment history rather than a single bounded page, since the closing comment is posted at merge time and a single-page, oldest-first fetch would systematically miss it on any sufficiently-discussed issue — locate the most recent comment authored by `github-actions[bot]` whose body matches auto-close-issues-on-staging.yml's own closing-comment text (`Closed by #<N> (merged into \`staging\`)...`), and compare that `<N>` to the number of the PR this run itself resolved. If they do not match — the issue was closed by a *different* PR than the one whose deploy just failed — the step SHALL leave the issue untouched, exactly as if the three-part state guard had failed. This prevents an unrelated later PR that happens to reference the same issue number from wrongly reopening an issue whose own, real fix already deployed successfully. If any check fails, the step SHALL leave that issue untouched and SHALL log which condition failed and why the issue was skipped. Each referenced issue number SHALL be evaluated independently, so that a failing PR referencing multiple issues reopens exactly those that pass every guard and leaves the rest untouched, unaffected by each other's outcome. When all guards pass, the step SHALL first update the issue's state to `open`, and only then post the explanatory comment — never the reverse — so that a failure partway through (the comment posting but the state update failing, or vice versa) never leaves a "reopening" comment on a still-closed issue. The posted comment SHALL name the failed run, the failed step ("Apply database migrations"), and the specific environment that failed (derived at runtime, e.g. from the running job's own id, rather than hardcoded per job so the two jobs' implementations can stay identical). The step SHALL wrap its top-level PR-resolution calls and its per-issue processing (including the comment-lookup call) each in their own error handling, so that a single API failure (a bogus/deleted/transferred issue number, a transient error) logs a warning and continues rather than raising an unhandled exception.

#### Scenario: An issue auto-closed by the automation, by THIS run's own PR, is reopened with an explanatory comment

- **GIVEN** issue #900 is currently `state: closed`, `state_reason: completed`, `closed_by.login: github-actions[bot]`
- **AND** #900 was referenced by the failing PR (PR #900) via a closing keyword
- **AND** #900's most recent `github-actions[bot]` comment reads `Closed by #900 (merged into \`staging\`).`
- **WHEN** the step evaluates #900
- **THEN** it first updates #900's state to `open`, then posts a comment naming the failed run URL, the failed step, and the failing environment, stating that automatic close-on-merge outran deploy verification

#### Scenario: An issue closed by a human for an unrelated reason is left untouched

- **GIVEN** issue #901 is referenced by the failing PR's title/body via a closing keyword
- **AND** #901 was closed manually by a human collaborator (`closed_by.login` is not `github-actions[bot]`), or with `state_reason: not_planned`
- **WHEN** the step evaluates #901
- **THEN** it does not reopen #901 and does not post a comment on it
- **AND** it logs that #901 was skipped and which condition failed

#### Scenario: An already-open issue is left untouched

- **GIVEN** issue #902 is referenced by the failing PR's title/body
- **AND** #902 is currently open (e.g. the auto-close step itself failed, or it was reopened already)
- **WHEN** the step evaluates #902
- **THEN** it does not attempt to reopen it or post a duplicate comment, because `state === 'closed'` is false

#### Scenario: An issue auto-closed by a DIFFERENT PR than the one whose deploy just failed is left untouched

- **GIVEN** issue #900 was auto-closed by PR #900, whose own deploy already succeeded
- **AND** a later, unrelated PR #950 also references "#900" via a closing keyword in its own title/body
- **AND** PR #950's deploy fails for a reason unrelated to #900's original fix
- **WHEN** the step (running for PR #950's failed deploy) evaluates #900
- **THEN** it reads #900's most recent `github-actions[bot]` closing comment, sees it reads `Closed by #900`, not `Closed by #950`
- **AND** it leaves #900 untouched and logs that the issue was closed by a different PR than this run's own

#### Scenario: The correct closing PR is found even on an issue with a long comment history

- **GIVEN** issue #900 has over 100 comments in total, and its most recent one is `github-actions[bot]`'s own `Closed by #900 (merged into \`staging\`).`
- **WHEN** the step evaluates #900
- **THEN** it pages through the issue's full comment history rather than stopping at a single page
- **AND** it correctly identifies `closedByPrNumber = 900`, matching this run's own PR, and reopens #900

#### Scenario: A single failing PR with mixed referenced issues reopens only the ones that qualify

- **GIVEN** the failing PR's title/body references both #900 (auto-closed by this same PR, per the first scenario above) and #901 (human-closed, per the second scenario above)
- **WHEN** the step evaluates both
- **THEN** #900 is reopened with a comment and #901 is left untouched, and neither issue's outcome is affected by the other's

#### Scenario: An unhandled error fetching or reopening one issue does not abort processing of others

- **GIVEN** the failing PR references two issues, and fetching or updating the first raises an error (e.g. a deleted issue, a transient API failure)
- **WHEN** the step processes the referenced issues
- **THEN** it logs a warning for the failed issue and continues to evaluate the remaining issue(s) normally

### Requirement: The reopening job MUST declare explicit least-privilege permissions

`deploy.yml`'s `deploy-staging` and `deploy-production` jobs SHALL each declare an explicit `permissions:` block including `contents: read`, `issues: write`, and `pull-requests: read`, rather than relying on the repository's default workflow permissions setting. `pull-requests: read` is required because the new step calls the commits-associated-PRs API; omitting it would cause that call to fail on the one path — an already-failed deploy — where nobody is watching synchronously.

#### Scenario: The reopening jobs declare explicit least-privilege permissions

- **WHEN** `deploy.yml`'s `deploy-staging` and `deploy-production` jobs are inspected
- **THEN** each declares an explicit `permissions:` block including `contents: read`, `issues: write`, and `pull-requests: read`, rather than relying on the repository's default workflow permissions setting
