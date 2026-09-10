## ADDED Requirements

### Requirement: A migration failure MUST resolve the triggering PR and re-extract its referenced issues

When the "Apply database migrations" step's own outcome is `failure` (not `skipped` or `success`) in `deploy.yml`'s `deploy-staging` or `deploy-production` job, a subsequent step's `if:` SHALL be `failure() && steps.<migration-step-id>.outcome == 'failure'` — never a bare outcome comparison, since a step `if:` without an explicit `failure()`/`success()`/`always()`/`cancelled()` term is implicitly ANDed with `success()`, which is already false by the time this step would run. That step SHALL resolve the pull request associated with the triggering commit via `GET /repos/{owner}/{repo}/commits/{sha}/pulls` for `github.sha`, and SHALL log the condition and take no further action if zero or more than one PR is associated with that commit. For exactly one associated PR, the step SHALL re-extract same-repo issue numbers from that PR's live `title`/`body` (fetched via the API inside the script, never interpolated via `${{ }}` into the script source or a shell command) using the same closing-keyword regex `auto-close-issues-on-staging.yml` uses (`\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s+#(\d+)`, case-insensitive), and SHALL skip any matched number whose referent is itself a pull request rather than an issue, mirroring `auto-close-issues-on-staging.yml`'s own guard.

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

### Requirement: Reopen and comment actions MUST be limited to issues closed by the auto-close automation

For each issue number re-extracted from the failing PR (per the PR-resolution requirement above), the step SHALL fetch that issue via `GET /repos/{owner}/{repo}/issues/{issue_number}` and SHALL reopen it and post an explanatory comment naming the failed run and step only if all of the following hold: `state` is `closed`, `state_reason` is `completed`, and `closed_by.login` is `github-actions[bot]`. If any of these does not hold, the step SHALL leave that issue untouched and SHALL log which condition failed and why the issue was skipped. Each referenced issue number SHALL be evaluated independently, so that a failing PR referencing multiple issues reopens exactly those that pass the guard and leaves the rest untouched, unaffected by each other's outcome. The step SHALL wrap its top-level PR-resolution calls and its per-issue processing each in their own error handling, so that a single API failure (a bogus/deleted/transferred issue number, a transient error) logs a warning and continues rather than raising an unhandled exception.

#### Scenario: An issue auto-closed by the automation is reopened with an explanatory comment

- **GIVEN** issue #900 is currently `state: closed`, `state_reason: completed`, `closed_by.login: github-actions[bot]`
- **AND** #900 was referenced by the failing PR's title/body via a closing keyword
- **WHEN** the step evaluates #900
- **THEN** it reopens #900 and posts a comment naming the failed run URL and the failed step, stating that automatic close-on-merge outran deploy verification

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

#### Scenario: A single failing PR with mixed referenced issues reopens only the ones that qualify

- **GIVEN** the failing PR's title/body references both #900 (auto-closed, per the first scenario above) and #901 (human-closed, per the second scenario above)
- **WHEN** the step evaluates both
- **THEN** #900 is reopened with a comment and #901 is left untouched, and neither issue's outcome is affected by the other's

#### Scenario: An unhandled error fetching or reopening one issue does not abort processing of others

- **GIVEN** the failing PR references two issues, and fetching or updating the first raises an error (e.g. a deleted issue, a transient API failure)
- **WHEN** the step processes the referenced issues
- **THEN** it logs a warning for the failed issue and continues to evaluate the remaining issue(s) normally

### Requirement: The reopening job MUST declare explicit least-privilege permissions

`deploy.yml`'s `deploy-staging` and `deploy-production` jobs SHALL each declare an explicit `permissions:` block including `contents: read`, `issues: write`, and `pull-requests: read`, rather than relying on the repository's default workflow permissions setting. `pull-requests: read` is required because the new step calls the commits-associated-PRs and PR-get APIs; omitting it would cause those calls to fail on the one path — an already-failed deploy — where nobody is watching synchronously.

#### Scenario: The reopening jobs declare explicit least-privilege permissions

- **WHEN** `deploy.yml`'s `deploy-staging` and `deploy-production` jobs are inspected
- **THEN** each declares an explicit `permissions:` block including `contents: read`, `issues: write`, and `pull-requests: read`, rather than relying on the repository's default workflow permissions setting
