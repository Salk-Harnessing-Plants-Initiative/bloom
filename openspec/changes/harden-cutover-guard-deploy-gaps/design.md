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
produced this commit, then re-reads **that PR's** live `title`/`body` via `github.rest.pulls.get`
and re-runs the exact same regex
(`auto-close-issues-on-staging.yml`'s `/\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s+#(\d+)/gi`)
against it — fetched via the API inside the script, never interpolated into the script source or
a shell `run:` block via `${{ }}`, matching `auto-close-issues-on-staging.yml`'s own safe pattern
and avoiding any script-injection surface. A referenced number that turns out to be a PR, not an
issue (`issue.pull_request` is present), is skipped, mirroring
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
`commits/{sha}/pulls` and `pulls.get` — `auto-close-issues-on-staging.yml` itself already declares
this permission even though it never calls a pulls API (it only reads the trigger's own payload),
so a step that *does* call one must declare it too, or the call 403s silently on the one path
nobody is watching synchronously.

### The whole script body is wrapped in a top-level try/catch, not just the per-issue loop

`auto-close-issues-on-staging.yml` wraps its per-issue close action in try/catch
(`:108-112`, "don't fail the whole run if one number is bogus"). This change's script adopts the
same per-issue resilience, and additionally wraps the top-level `commits/{sha}/pulls` /
`pulls.get` calls in their own try/catch — an unhandled exception there (rate limit, transient
network blip, a missing-permission 403) would otherwise fail this already-on-the-failure-path
step with a bare, unexplained stack trace, exactly when a human is trying to understand why an
issue silently stayed closed. Both layers log via `core.warning`/`core.info` and exit cleanly
rather than throwing.

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

The safety-critical guard logic (state_reason + closed_by) can only ever be shape-tested
(substring/pattern presence in the embedded script), not behaviorally exercised against a mocked
GitHub API response, for the same reason nothing else here can run in PR CI. This is the weakest
test coverage in the change and is accepted for the same reason `auto-close-issues-on-staging.yml`
accepts it for its own, symmetric guard — not because it doesn't matter, but because there is no
feasible way to run it short of a live GitHub App test harness this repo doesn't have.

### Step placement and this being the first GitHub-API call from this self-hosted runner

The new step is inserted immediately after "Show migration status on failure" and before
"Migration summary" in each job — a deterministic slot next to the other failure-diagnostic
steps, rather than an unspecified "wherever ordering is cleanest." Every existing step in
`deploy.yml` is SSH-only; this change is the first to require the self-hosted `salk-network`
runner to reach `api.github.com` over HTTPS directly (via `actions/github-script`). This is very
likely already fine — the same runner already needs outbound HTTPS to `github.com` to receive
jobs at all and to run `actions/checkout@v4` — but is called out explicitly here rather than left
an unstated assumption, given this repo's own recent history of exactly this class of surprise on
a Salk-network host (the bloom#616 ufw default-deny-outbound finding). If this ever needs
debugging, the fix is the same one bloom#616 already applied (an explicit ufw allow rule), not a
new investigation.

## Risks / Trade-offs

- **PR merged via `gh pr merge --squash` from outside the normal flow, or a direct push to
  `staging`** — `commits/{sha}/pulls` would return no associated PR, or more than one. The step
  logs this and takes no action rather than guessing; this is the same "log and continue on one
  bogus number" bias `auto-close-issues-on-staging.yml` already uses for a deleted/transferred
  issue.
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
