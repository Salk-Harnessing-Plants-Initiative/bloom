## Why

The `compose-health-check` job in `pr-checks.yml` runs under `pr-checks-${{ github.ref }}` with `cancel-in-progress: true`, so a new push to a PR cancels any in-flight run for the same ref. When that cancellation lands before the "Generate .env.ci from secrets" step has run, the job's `Migration summary` and `Cleanup` steps — both `if: always()` — still execute and unconditionally assume `.env.ci` exists. Both crash:

```
grep: .env.ci: No such file or directory
...
couldn't find env file: /home/runner/work/bloom/bloom/.env.ci
```

This is cosmetic — it never affects a run that completes normally or fails after `.env.ci` exists — but it stacks confusing secondary errors on top of the real cancellation, making it harder to tell "this run was just superseded" from "something is actually broken." That ambiguity compounds the rapid-rebase-churn noise #454 already flagged for CI signal quality.

## What Changes

- Guard the `Migration summary` step: check `.env.ci` exists before the `grep`/`supabase migration list` calls; if absent, print a skip message and exit 0 instead of crashing on the missing file.
- Guard the `Cleanup` step: check `.env.ci` exists before `docker compose --env-file .env.ci down -v`; if absent, print a skip message and exit 0 (nothing was ever brought up, so there's nothing to tear down).
- No change to behavior for any run where `.env.ci` was generated — both steps keep reporting migration state / tearing down the stack exactly as before.

## Impact

- Affected specs: `deploy-migrations` (already documents the `compose-health-check` job's `if: always()` step behavior)
- Affected code: `.github/workflows/pr-checks.yml` (`Migration summary` and `Cleanup` steps in the `compose-health-check` job)
- Related: #454 (CI reliability), #455 (this issue)
