## Why

`pr-checks.yml`'s `concurrency: { cancel-in-progress: true }` block is workflow-level, so a newer push cancels every job in the run, not just `compose-health-check`. Six steps across two jobs unconditionally assume their job's compose env file already exists, but each can run before that file was ever generated: `compose-health-check`'s `Migration summary` and `Cleanup` (both `if: always()`) crash on early cancellation; its `Show migration status on failure` and `Debug logs on failure` (both `if: failure()`, immune to cancellation but not to an earlier genuine failure such as "Install Supabase CLI") crash the same way; `dev-stack-smoke`'s `Cleanup` and `Debug logs on failure` (depending on `.env.dev` from `make init`) share the identical defect under the same workflow-level cancellation / earlier-failure conditions. All six fail with the same class of error:

```
grep: .env.ci: No such file or directory
...
couldn't find env file: /home/runner/work/bloom/bloom/.env.ci
```

This never affects a run that completes normally, or fails after its env file exists — but it stacks confusing secondary errors on top of the real signal (a cancellation, or an unrelated earlier failure), compounding the rapid-rebase-churn noise #454 already flagged for CI signal quality.

(Revision note: an initial version of this PR guarded only 4 of these 6 steps, leaving both jobs' `Debug logs on failure` as a documented-but-unfixed follow-up. Code review on PR #457 correctly pointed out this was the identical one-line guard already applied elsewhere in the same diff, so it's folded in now instead of deferred.)

## What Changes

- Guard all six steps with a non-empty-file check (`[ -s <file> ]`, not `[ -f <file> ]` — a 0-byte file from an interrupted write is treated the same as absent) on their respective env file (`.env.ci` for the four `compose-health-check` steps, `.env.dev` for `dev-stack-smoke`'s `Cleanup`/`Debug logs on failure`) as the first line of their `run:` block; if absent/empty, print a `::notice::`-annotated skip message and exit 0 instead of crashing.
- Skip message wording matches each step's actual trigger: `if: always()` steps (which fire on both cancellation and failure) say "canceled or failed"; `if: failure()` steps (which cancellation can never reach) say only "failed" — cancellation can't reach an `if: failure()` step, so the earlier draft's identical wording across both was misleading.
- Convert `Cleanup`'s `run:` (both jobs) from a single-line scalar to a `run: |` block to fit the guard; add an inline comment at each guarded step (both `Cleanup` steps and both `Debug logs on failure` steps currently have none for this concern).
- Add regression tests to `tests/unit/test_pr_checks_workflow_shape.py` and `tests/unit/test_ci_dev_stack_smoke.py`: a shape check (per-step exact `if:` condition + guard-string prefix) and a behavioral check that actually executes each guard line via subprocess against absent/empty/non-empty fixture files, so a regression that flips `||`→`&&`, `exit 0`→`exit 1`, or `-s`→`-f` is caught, not just a literal-text mismatch.
- No change to behavior for any run where the relevant env file exists and is non-empty — all six steps keep their existing logic exactly as before.

## Impact

- Affected specs: `deploy-migrations` — MODIFIES "Migration failures MUST be highly visible on GitHub Actions" (covers `Migration summary` + `Show migration status on failure`) and ADDS a new requirement covering the four `Cleanup`/`Debug logs on failure` steps (none had prior spec coverage)
- Affected code: `.github/workflows/pr-checks.yml` (`compose-health-check`'s `Migration summary`/`Show migration status on failure`/`Cleanup`/`Debug logs on failure`; `dev-stack-smoke`'s `Cleanup`/`Debug logs on failure`), `tests/unit/test_pr_checks_workflow_shape.py`, `tests/unit/test_ci_dev_stack_smoke.py`
- Known pre-existing gap, out of scope for this change (not introduced by it, surfaced during PR #457 review): the migration steps embed the Postgres password directly in `--db-url` and `Migration summary` passes `--debug`, both of which contradict `deploy-migrations`'s existing "Migration credentials MUST NOT appear in process argv, logs, or step summaries" requirement. Worth a dedicated follow-up; not fixed here since this PR didn't introduce it and touching credential handling deserves its own review.
- Related: #454 (CI reliability), #455 (this issue)
