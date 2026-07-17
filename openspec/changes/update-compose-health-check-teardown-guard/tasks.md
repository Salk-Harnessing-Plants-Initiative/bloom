## 1. Commit the OpenSpec proposal (initial + revisions)

- [x] 1.1 Original scope — `docs(#455): openspec proposal — compose-health-check teardown guard` (`20c0469`)
- [x] 1.2 First revision (expanded to `Show migration status on failure` + `dev-stack-smoke`'s `Cleanup`; `Migration summary` half converted to MODIFIED) — `docs(#455): expand proposal — cover Show migration status + dev-stack-smoke Cleanup` (`03e08a4`)
- [x] 1.3 Second revision, addressing PR #457's first code-review round — `docs(#455): second proposal revision — address PR #457 code review` (`106bbb7`)

## 2. Guard implementation, round 1 (4 of 6 steps)

- [x] 2.1–3.6 (commit `c9940df`): added `test_compose_health_check_teardown_steps_guard_missing_env_ci` / `test_dev_stack_smoke_cleanup_guards_missing_env_dev`, confirmed red, guarded `Migration summary`/`Show migration status on failure`/`Cleanup` (compose-health-check) + `Cleanup` (dev-stack-smoke) with `[ -f <file> ] || {...}`, confirmed green, committed test+fix together.

## 3. Round 2 — address PR #457's first code-review round

- [x] 3.1 **Blocking** (stale "all checks pass" claim): root cause was citing the wrong commit's CI run. Fixed by merging `origin/staging` in and re-verifying against the actual HEAD each time from here on.
- [x] 3.2 **Important**: folded in the previously-deferred guard for both jobs' `Debug logs on failure` steps (`if: failure()`) — same one-line guard pattern already applied elsewhere. Spec delta's ADDED requirement broadened from 2 steps to 4 (`Cleanup` × 2 jobs + `Debug logs on failure` × 2 jobs).
- [x] 3.3 **Important**: regression tests are now behavioral — each guard's first `run:` line is extracted and executed via `subprocess` against `tmp_path` fixtures in three states (absent / empty / non-empty), asserting exit code 0 throughout and that execution only continues past the guard when the file is non-empty.
- [x] 3.4 **Important**: `if:` condition check is now a `dict[str, str]` keyed by exact step name → exact expected condition, not membership in a shared `("always()", "failure()")` set.
- [x] 3.5 **Important** (adjacent, not fixed in this PR): pre-existing `deploy-migrations` spec contradiction (password embedded in `--db-url`; `Migration summary` uses `--debug`) documented as an explicit out-of-scope callout in `proposal.md`. Attempted to open a tracking issue; blocked by the harness's auto-mode classifier (filing a new public issue wasn't something the user had asked for — only the review suggested it). Flagged directly to the user instead.
- [x] 3.6 **Suggestion**: `[ -f <file> ]` → `[ -s <file> ]` (non-empty, not just present) across all six guards.
- [x] 3.7 **Suggestion**: `::notice::` GitHub annotations instead of plain `echo` for skip messages.
- [x] 3.8 **Suggestion**: skip-message wording now matches each step's actual trigger — `if: always()` steps say "canceled or failed"; `if: failure()` steps (which cancellation can never reach) say only "failed".
- [x] 3.9 Re-ran the full `tests/unit/` suite: 324 passed, 1 skipped (up from 318 — 6 new parametrized behavioral cases).
- [x] 3.10 Committed 3.2–3.8 together as one commit: `ci(#455): fold in Debug-logs guards, switch to -s, behavioral tests (review round 2)` (`26a60f3`)

## 4. Validate (round 2) — and correct an overclaim a third review caught

- [x] 4.1 Merged `origin/staging` into this branch (`62c6a49`) — was 5 commits behind, clean merge, no conflicts.
- [x] 4.2 Pushed; a subsequent GitHub-side "update branch" merged `staging` again after PR #456 (the related #454 fix) landed there (`ffcfa64`) — fast-forwarded locally, re-confirmed all 6 guards present (`grep -n '\[ -s .env' .github/workflows/pr-checks.yml`) and the full suite still green (324 passed, 1 skipped) after each merge.
- [x] 4.3 `openspec validate --strict` unavailable in this environment (no `openspec` binary, consistent throughout every attempt); hand-verified the spec delta against `openspec/AGENTS.md` format rules instead.
- [x] 4.4 **Correcting an overclaim a third review round caught**: an earlier version of this file claimed a cited `compose-health-check` run showed "all three `.env.ci` guards took the non-skip path." That's wrong for `Show migration status on failure`: it runs under `if: failure()`, so on a passing job (no failure occurred) it doesn't run at all — its own `if:` condition is false, which has nothing to do with this PR's guard. Only `Migration summary` and `Cleanup` (both `if: always()`) are empirically exercised on the non-skip path by a passing run. `Show migration status on failure`'s and `Debug logs on failure`'s (both `if: failure()`) non-skip path is NOT exercised live by any run so far — genuinely inducing it would require an artificial earlier-step failure, which wasn't done. Confidence in those two steps' guard mechanics instead rests on: (a) the shape test (exact string + exact `if:` condition), (b) the behavioral test (the exact guard line executed via subprocess in isolation, proving the bash logic — `||`, `exit 0`, `-s` semantics — is correct regardless of GitHub Actions context). This is an accepted, disclosed gap, not a silent one.
- [x] 4.5 That same third review round's other two "Important" findings (Debug logs unguarded; shape-only tests) and its CI-failure claim were checked against the actual current state and found stale — both were already fixed in round 2 (commit `26a60f3`, predating that review), and the live PR checks showed no failure on the commit in question at the time it was checked. Not re-litigated as new work; noted here so the discrepancy is traceable rather than silently dropped.
- [x] 4.6 Confirmed final green CI on HEAD `ffcfa64`: all 27 checks pass, run https://github.com/Salk-Harnessing-Plants-Initiative/bloom/actions/runs/29520530333 — including `Docker Compose Health Check` (10m1s) and `Dev stack smoke` (6m16s), no flakes.
- [x] 4.7 Replied to the PR review thread(s) summarizing what was fixed, the accepted disclosed gap (4.4), and the out-of-scope credential-handling item (3.5) with a plain explanation instead of a public tracking issue.
