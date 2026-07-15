## 1. Commit the OpenSpec proposal

- [ ] 1.1 Commit `openspec/changes/update-compose-health-check-teardown-guard/**` on its own (`docs(#455): openspec proposal — compose-health-check teardown guard`)

## 2. Guard the Migration summary step

- [ ] 2.1 In `.github/workflows/pr-checks.yml`, add as the first line of the `Migration summary` step's `run:` block:
      `[ -f .env.ci ] || { echo "skipping — .env.ci was never generated (job canceled/failed early)"; exit 0; }`
- [ ] 2.2 Confirm the rest of the step (grep of `PG_*` vars, `supabase migration list`, writing to `$GITHUB_STEP_SUMMARY`) is unchanged below the guard

## 3. Guard the Cleanup step

- [ ] 3.1 Add the same guard line as the first line of the `Cleanup` step's `run:` block, before `docker compose $COMPOSE_FILES --env-file .env.ci down -v`

## 4. Validate

- [ ] 4.1 Confirm no other `if: always()` step in `compose-health-check` reads `.env.ci` without an existing guard (only `Migration summary` and `Cleanup` do — `Show migration status on failure` is `if: failure()`, which does not fire on `cancelled`, so it's already unaffected)
- [ ] 4.2 Run `openspec validate update-compose-health-check-teardown-guard --strict` and fix any issues (hand-verify against `openspec/AGENTS.md` format rules if the CLI is unavailable in this environment)
- [ ] 4.3 Open a PR referencing #455 and confirm a real `compose-health-check` run still passes end-to-end (both guarded steps take the non-skip path when `.env.ci` exists)
