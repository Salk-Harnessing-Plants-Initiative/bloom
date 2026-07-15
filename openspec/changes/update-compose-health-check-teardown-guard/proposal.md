## Why

`pr-checks.yml`'s `concurrency: { cancel-in-progress: true }` block is workflow-level, so a newer push cancels every job in the run, not just `compose-health-check`. Four steps across two jobs unconditionally assume their job's compose env file already exists, but each can run before that file was ever generated: `compose-health-check`'s `Migration summary` (`if: always()`) and `Cleanup` (`if: always()`) crash on early cancellation; its `Show migration status on failure` (`if: failure()`, immune to cancellation but not to an earlier genuine failure such as "Install Supabase CLI") crashes the same way; `dev-stack-smoke`'s `Cleanup` (`if: always()`, depends on `.env.dev` from `make init`) shares the identical defect under the same workflow-level cancellation. All four fail with the same class of error:

```
grep: .env.ci: No such file or directory
...
couldn't find env file: /home/runner/work/bloom/bloom/.env.ci
```

This never affects a run that completes normally, or fails after its env file exists — but it stacks confusing secondary errors on top of the real signal (a cancellation, or an unrelated earlier failure), compounding the rapid-rebase-churn noise #454 already flagged for CI signal quality.

## What Changes

- Guard all four steps with an existence check on their respective env file (`.env.ci` for the three `compose-health-check` steps, `.env.dev` for `dev-stack-smoke`'s `Cleanup`) as the first line of their `run:` block; if absent, print a skip message and exit 0 instead of crashing.
- Convert `Cleanup`'s `run:` (both jobs) from a single-line scalar to a `run: |` block to fit the guard; add an inline comment at each guarded step (both `Cleanup` steps currently have none).
- Add a regression test to `tests/unit/test_pr_checks_workflow_shape.py` asserting the guard is present as the first line of each of the four steps' `run:` blocks.
- No change to behavior for any run where the relevant env file exists — all four steps keep their existing logic exactly as before.
- Out of scope: both jobs' `Debug logs on failure` steps (`if: failure()`) share a structurally similar dependency on the same env files, but weren't part of the reviewed scope for this change — left as a candidate follow-up, not fixed here.

## Impact

- Affected specs: `deploy-migrations` — MODIFIES "Migration failures MUST be highly visible on GitHub Actions" (already documents `Migration summary`'s and `Show migration status on failure`'s `.env.ci`-dependent behavior) and ADDS a new requirement for the two `Cleanup` steps (neither had prior spec coverage)
- Affected code: `.github/workflows/pr-checks.yml` (`compose-health-check`'s `Migration summary`/`Show migration status on failure`/`Cleanup`; `dev-stack-smoke`'s `Cleanup`), `tests/unit/test_pr_checks_workflow_shape.py` (new regression test)
- Related: #454 (CI reliability), #455 (this issue)
