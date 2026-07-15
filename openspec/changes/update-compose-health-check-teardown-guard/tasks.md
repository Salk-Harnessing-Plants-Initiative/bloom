## 1. Commit the revised OpenSpec proposal

- [x] 1.1 Commit the original scope (`docs(#455): openspec proposal — compose-health-check teardown guard`) — done (`20c0469`)
- [ ] 1.2 Commit the revision (expanded scope to `Show migration status on failure` + `dev-stack-smoke`'s `Cleanup`; `Migration summary` half converted to a MODIFIED delta) as its own commit: `docs(#455): expand proposal — cover Show migration status + dev-stack-smoke Cleanup; MODIFIED delta for Migration summary`

## 2. Add the regression test first (TDD)

- [ ] 2.1 Add a test to `tests/unit/test_pr_checks_workflow_shape.py` (reuse its existing `_load_workflow()` helper) that, for each of the four guarded steps below, finds the step by name in its job and asserts the first non-empty line of its `run:` block matches the guard pattern for that job's env file:
  - `compose-health-check` → `Migration summary` → guards `.env.ci`
  - `compose-health-check` → `Show migration status on failure` → guards `.env.ci`
  - `compose-health-check` → `Cleanup` → guards `.env.ci`
  - `dev-stack-smoke` → `Cleanup` → guards `.env.dev`

  Sketch:

  ```python
  GUARDED_STEPS = {
      "compose-health-check": {
          "Migration summary": ".env.ci",
          "Show migration status on failure": ".env.ci",
          "Cleanup": ".env.ci",
      },
      "dev-stack-smoke": {"Cleanup": ".env.dev"},
  }


  def test_teardown_steps_guard_missing_env_file() -> None:
      """Regression guard for #455: these steps run if: always()/if: failure()
      and must no-op (not crash) if their env file was never generated
      (job canceled early, or an earlier unrelated step failed first)."""
      workflow = _load_workflow()
      for job_name, steps in GUARDED_STEPS.items():
          job = workflow["jobs"][job_name]
          by_name = {s.get("name"): s for s in job["steps"]}
          for step_name, env_file in steps.items():
              step = by_name[step_name]
              assert step.get("if") in ("always()", "failure()"), (
                  f"{job_name}/{step_name} must keep its always()/failure() condition"
              )
              first_line = str(step["run"]).strip().splitlines()[0].strip()
              assert first_line.startswith(f"[ -f {env_file} ]"), (
                  f"{job_name}/{step_name} run: block must guard {env_file} "
                  f"existence as its first line; got {first_line!r}"
              )
  ```

- [ ] 2.2 Run the new test against the current (unpatched) workflow and confirm it FAILS for all four steps (proves the test actually exercises the gap before the fix lands)

## 3. Guard the four steps in pr-checks.yml

- [ ] 3.1 `compose-health-check` → `Migration summary`: add `[ -f .env.ci ] || { echo "skipping — .env.ci was never generated (job canceled/failed early)"; exit 0; }` as the first line of the `run:` block; add a one-line comment above it referencing #455 (extend the existing "Layer C" comment rather than replacing it)
- [ ] 3.2 `compose-health-check` → `Show migration status on failure`: same guard as the first line of its `run:` block; add a one-line comment noting it also guards against an earlier unrelated failure occurring before `.env.ci` exists (not just cancellation)
- [ ] 3.3 `compose-health-check` → `Cleanup`: convert `run:` from its current single-line scalar to a `run: |` block; add the same guard as its first line; add a one-line comment (this step currently has none)
- [ ] 3.4 `dev-stack-smoke` → `Cleanup`: convert `run:` to a `run: |` block; add `[ -f .env.dev ] || { echo "skipping — .env.dev was never generated (job canceled/failed early)"; exit 0; }` as its first line; add a one-line comment (this step currently has none)
- [ ] 3.5 Re-run the test from §2 and confirm it now PASSES for all four steps
- [ ] 3.6 Commit 3.1–3.5 together as one commit — same root cause and fix pattern across both jobs, inseparable in verification (one `compose-health-check` run and one `dev-stack-smoke` run exercise all four): `ci(#455): guard .env.ci/.env.dev-dependent teardown steps against early cancellation/failure`

## 4. Validate

- [ ] 4.1 Confirm the four steps above are exhaustive for this change's scope: both jobs' `Debug logs on failure` steps (`if: failure()`) share a structurally similar dependency on the same env files but are explicitly out of scope (see proposal.md) — not fixed here
- [ ] 4.2 Run `openspec validate update-compose-health-check-teardown-guard --strict` and fix any issues (hand-verify against `openspec/AGENTS.md` format rules if the CLI is unavailable, as it was in this environment)
- [ ] 4.3 Manually verify the cancellation path once: push, then push a superseding commit within ~15s to trigger `cancel-in-progress`; inspect the canceled run's `Migration summary` and both `Cleanup` steps' logs for the skip message, and confirm the job is reported `cancelled`, not `failed`, because of them
- [ ] 4.4 Open a PR referencing #455 targeting `staging`, and confirm a normal (non-canceled) `compose-health-check` + `dev-stack-smoke` run still passes end-to-end — all four guarded steps take the non-skip path and behave exactly as before
