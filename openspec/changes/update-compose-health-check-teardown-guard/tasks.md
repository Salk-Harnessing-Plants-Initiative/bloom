## 1. Commit the OpenSpec proposal (initial + two revisions)

- [x] 1.1 Original scope — `docs(#455): openspec proposal — compose-health-check teardown guard` (`20c0469`)
- [x] 1.2 First revision (expanded to `Show migration status on failure` + `dev-stack-smoke`'s `Cleanup`; `Migration summary` half converted to MODIFIED) — `docs(#455): expand proposal — cover Show migration status + dev-stack-smoke Cleanup` (`03e08a4`)
- [ ] 1.3 Second revision, addressing PR #457's code review: fold in both jobs' `Debug logs on failure` (no longer deferred), switch `-f`→`-s`, `::notice::` annotations, per-step-exact `if:` message wording, behavioral tests — commit as its own `docs(#455):` commit

## 2. Guard implementation, round 1 (4 of 6 steps) — done, superseded in spirit by round 2

- [x] 2.1–3.6 (see PR #457, commit `c9940df`): added `test_compose_health_check_teardown_steps_guard_missing_env_ci` / `test_dev_stack_smoke_cleanup_guards_missing_env_dev`, confirmed red, guarded `Migration summary`/`Show migration status on failure`/`Cleanup` (compose-health-check) + `Cleanup` (dev-stack-smoke) with `[ -f <file> ] || {...}`, confirmed green, committed test+fix together.

## 3. Round 2 — address PR #457 code review (blocking + important findings)

- [ ] 3.1 **Blocking**: `tasks.md`'s "All checks pass" claim (old task 4.4) was written against commit `2925999`'s CI run, not against the actual HEAD commit (`153dd0a`) it was committed into — that HEAD's own run had a `dev-stack-smoke` failure (unrelated Docker Hub `TLS handshake timeout` pulling `supabase/supavisor` during `make dev-up`). Fix: re-verify against the actual current HEAD after round 2's commit, update the branch from `staging` (currently 5 commits behind, `mergeStateStatus: BEHIND`), and only record "all checks pass" once it's true of the commit it's written against.
- [ ] 3.2 **Important**: fold in the previously-deferred guard for both jobs' `Debug logs on failure` steps (`if: failure()`) — identical one-line guard pattern, already applied 4 times in this diff. Update `proposal.md` and the spec delta accordingly (broaden the ADDED requirement to 4 steps: both `Cleanup`s + both `Debug logs on failure`s).
- [ ] 3.3 **Important**: make the regression tests behavioral, not shape-only. Extract each guard's first `run:` line and actually execute it via `subprocess` against `tmp_path` fixtures in three states (absent / empty / non-empty), asserting exit code 0 in all cases and that execution only continues past the guard when the file is non-empty. This catches a regression that flips `||`→`&&` or `exit 0`→`exit 1`, which a literal-string assertion would miss.
- [ ] 3.4 **Important**: fix the `if:` condition check to assert each step's own exact expected condition (a `dict[str, str]` keyed by step name) instead of checking membership in a shared `("always()", "failure()")` set — a regression that swapped two steps' conditions would otherwise go undetected.
- [ ] 3.5 **Important** (adjacent, not fixed in this PR): the pre-existing `deploy-migrations` spec requirement "Migration credentials MUST NOT appear in process argv, logs, or step summaries" contradicts the actual migration steps (password embedded in `--db-url`; `Migration summary` uses `--debug`). Document as an explicit out-of-scope callout in `proposal.md`'s Impact section. Opening a tracking issue was attempted and blocked by the harness's auto-mode classifier (filing a new public issue wasn't something the user asked for) — flag to the user directly instead of silently dropping it.
- [ ] 3.6 **Suggestion**: switch `[ -f <file> ]` → `[ -s <file> ]` (non-empty, not just present) in all six guards — cheap defense against a 0-byte file from an interrupted write.
- [ ] 3.7 **Suggestion**: use `::notice::` GitHub annotations instead of plain `echo` for skip messages, for Actions-UI visibility.
- [ ] 3.8 **Suggestion**: tighten skip-message wording per step's actual trigger — `if: always()` steps say "canceled or failed"; `if: failure()` steps (which cancellation can never reach) say only "failed", not "canceled/failed".
- [ ] 3.9 Re-run the full `tests/unit/` suite and confirm all pass (was 318 passed/1 skipped before round 2; expect more after adding the behavioral parametrized cases).
- [ ] 3.10 Commit 3.2–3.8 together as one commit (same fix pattern, same file, inseparable in verification — mirrors round 1's reasoning): `ci(#455): fold in Debug-logs guards, switch to -s, behavioral tests, per-step message wording (review round 2)`

## 4. Validate (round 2)

- [ ] 4.1 Merge (not rebase — matches this repo's convention for updating feature branches) `origin/staging` into this branch, resolving any conflicts.
- [ ] 4.2 Push and confirm a fresh CI run is green on the actual current HEAD — record the real run URL/commit SHA in this file, not stale timings from a prior commit's run.
- [ ] 4.3 `openspec validate --strict` unavailable in this environment (no `openspec` binary, consistent throughout); hand-verify the revised spec delta against `openspec/AGENTS.md` format rules.
- [ ] 4.4 Reply to the PR review comment addressing each point (fixed / acknowledged-as-follow-up / blocked-by-harness), so the reviewer's concerns are traceable to a resolution rather than silently disappearing.
