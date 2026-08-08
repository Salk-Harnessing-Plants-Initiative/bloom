## 1. Proposal scaffold

- [x] 1.1 Commit `proposal.md`, `design.md`, `tasks.md`, `specs/deploy-config-reload/spec.md` on `fix/kong-reload-on-deploy` (branched from `origin/staging`).
- [x] 1.2 `openspec validate fix-kong-reload-on-deploy --strict` passes.

## 2. Workflow-shape tests first (RED) — `kongfile_changed` detection + gated steps exist and are ordered correctly

- [ ] 2.1 Write `tests/unit/test_deploy_kong_reload_on_config_change.py`, modeled on `tests/unit/test_deploy_data_dir_preflight_ordering.py` (load `deploy.yml` with `yaml.safe_load`, assert on step dicts — not string-grep). For **both** `deploy-production` and `deploy-staging` jobs, assert:
  - `Pull latest code` (`id: pull_prod` / `id: pull_staging`)'s `run:` text contains a `git diff --quiet "$BEFORE" "$AFTER" -- volumes/api/kong.yml` line and a `KONGFILE_CHANGED=` marker, positioned after the existing `CADDYFILE_CHANGED` line, and that the step also writes a `kongfile_changed` output to `$GITHUB_OUTPUT`.
  - A `Restart Kong config` step exists, `if: steps.pull_<env>.outputs.kongfile_changed == 'true'`, has `id: restart_kong_<env>`, its `run:` invokes `docker compose ... restart kong` (NOT `kong reload`), guards on `ps -q kong` returning non-empty before capturing `before_restart_count`, polls `docker inspect --format='{{.State.Health.Status}}'` for `healthy` (bounded, not a flat `sleep N`), and writes a `before_restart_count` output.
  - A `Kong crash-loop check` step exists **immediately** after it (index difference of exactly 1, not just "somewhere later") — this repo's `test_deploy_data_dir_preflight_ordering.py` only checks relative order (`<`) for its preflight/deploy pair, but since these two Kong steps are meant to be an inseparable pair, this test asserts adjacency specifically, catching a future step insertion between them — with the *same* `if:` condition as the restart step, and its `run:` invokes `scripts/check_kong_restart_delta.sh` with `steps.restart_kong_<env>.outputs.before_restart_count` and threshold `2`, passing the correct compose args for that job (no `-p` + `.env.prod` for prod; `-p bloom_v2_staging` + `.env.staging` for staging), and includes `::add-mask::` lines for `SUPABASE_SERVICE_KEY`/`BLOOM_AGENT_KEY`/`DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD` before the script invocation.
  - Both new steps are positioned after the existing `Caddy crash-loop check` step (relative order, matching current placement) in each job.
  - This test MUST fail (red) against current `deploy.yml` before any implementation changes — confirm by running it before task 3.
- [ ] 2.2 In the same file, add a **regression-pinning** test for Caddy's existing (already-shipped, currently untested) `caddyfile_changed` detection, `Reload Caddy config`, and `Caddy crash-loop check` steps — asserting their current shape matches the `deploy-config-reload` spec's retroactively-documented scenarios. This is new test coverage for old, unchanged behavior; it exists because that behavior is now formally specified by this change and deserves the same protection as the new Kong requirements it's documented alongside.
- [ ] 2.3 Add a **behavioral** test for the "marker line missing → default `true`" fail-safe (spec Requirement 1, first scenario): extract the exact `changed=$(grep ... ); echo "kongfile_changed=${changed:-true}" >> "$GITHUB_OUTPUT"`-style one-liner from the step text via regex (same extraction technique `tests/unit/test_pr_checks_workflow_shape.py`'s teardown-guard tests use — see its git history for why behavioral beats shape-only here) and execute it via `subprocess` with empty/absent marker input, asserting the parsed value defaults to `true`.
- [ ] 2.4 Confirm red: `uv run --extra test pytest tests/unit/test_deploy_kong_reload_on_config_change.py -v` fails with clear assertion errors (missing steps/outputs), not a collection error. Tasks 2.2's Caddy regression-pinning assertions are expected to already pass (they describe existing behavior) — if any of them fail, that means the "current Caddy behavior" description in this task or in `spec.md` is wrong and must be corrected before proceeding, not implemented around.

## 3. Implement the `deploy.yml` changes (GREEN for task 2)

- [ ] 3.1 Extend `Pull latest code` in `deploy-production` (`id: pull_prod`): add the `KONGFILE_CHANGED=` diff line for `volumes/api/kong.yml` right after the existing `CADDYFILE_CHANGED=` line (same `BEFORE`/`AFTER`, no new SSH round-trip), and parse/emit `kongfile_changed` the same way `caddyfile_changed` is parsed (default `true` if the marker is missing).
- [ ] 3.2 Add `Restart Kong config (production)` (`id: restart_kong_prod`) immediately after the existing `Caddy crash-loop check` step: `if: steps.pull_prod.outputs.kongfile_changed == 'true'`; resolves `kong`'s container id via `docker compose -f docker-compose.prod.yml --env-file .env.prod ps -q kong` (fail with `::error::` if empty, no restart attempted); captures `RestartCount` via `docker inspect`; runs `docker compose -f docker-compose.prod.yml --env-file .env.prod restart kong`; polls `docker inspect --format='{{.State.Health.Status}}'` on the same container every few seconds until `healthy` or 45s elapsed (see `design.md` Decision 5 — NOT a flat sleep); writes `before_restart_count` to `$GITHUB_OUTPUT` (same tee-and-parse pattern as `caddyfile_changed`).
- [ ] 3.3 Add `Kong crash-loop check (production)` immediately after it: same `if:` condition; emits `::add-mask::` for `SUPABASE_SERVICE_KEY`/`BLOOM_AGENT_KEY`/`DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD` (`${{ secrets.PROD_* }}` values) before invoking `bash scripts/check_kong_restart_delta.sh "${{ steps.restart_kong_prod.outputs.before_restart_count }}" 2 -- docker compose -f docker-compose.prod.yml --env-file .env.prod` over SSH from `$PROD_DEPLOY_PATH`.
- [ ] 3.4 Repeat 3.1–3.3 for `deploy-staging` (`id: pull_staging` / `id: restart_kong_staging`), using `-p bloom_v2_staging` + `.env.staging` throughout, positioned after staging's own `Caddy crash-loop check`.
- [ ] 3.5 Confirm green: `uv run --extra test pytest tests/unit/test_deploy_kong_reload_on_config_change.py -v` passes (shape + Caddy regression-pinning + fail-safe-default tests).
- [ ] 3.6 Confirm `deploy.yml` still parses as valid YAML and valid GitHub Actions syntax — this repo has no dedicated `yamllint`/`actionlint` CI job today (confirmed: not present in `pr-checks.yml` or `.pre-commit-config.yaml`), so this is covered implicitly by task 2's `yaml.safe_load`-based test plus a manual read-through of the diff for stray indentation/quoting errors before committing.

## 4. Behavioral tests first (RED) — `scripts/check_kong_restart_delta.sh`

- [ ] 4.1 Add tests to `tests/unit/test_deploy_kong_reload_on_config_change.py` (or a sibling file if it reads more clearly split out) that invoke the script directly via `subprocess`, with a fake `docker` executable prepended to `PATH` (a small bash stub dispatching on `inspect` vs `compose ... ps -q kong` / `logs --tail=100 kong` / `stop kong`, returning a controlled `RestartCount` via an env var and appending to a call-log file in `tmp_path`):
  - `test_delta_within_threshold_passes_without_stopping_kong`: `before=5`, faked `RestartCount=6` (delta 1, our own restart) → exit 0, `logs`/`stop` never called.
  - `test_delta_at_threshold_passes`: `before=5`, faked `RestartCount=7` (delta 2, exactly at threshold) → exit 0.
  - `test_delta_just_over_threshold_fails`: `before=5`, faked `RestartCount=8` (delta 3, threshold+1 — the actual boundary, not skipped this time) → exit 1, `logs`/`stop` called.
  - `test_delta_far_over_threshold_stops_kong_and_fails`: `before=5`, faked `RestartCount=9` (delta 4) → exit 1, both `logs --tail=100 kong` and `stop kong` recorded as called through the passed-through compose command.
  - `test_missing_container_fails_cleanly`: fake `docker compose ... ps -q kong` returns empty → exit 1 with a clear `::error::` message, no `docker inspect` call attempted.
  - `test_non_numeric_restart_count_fails_cleanly`: fake `docker inspect` returns empty string / non-numeric garbage instead of a count → exit 1 with a clear `::error::`, NOT silently coerced to `0` (a coercion could mask a real problem as a passing delta).
  - `test_usage_error_exits_2`: invoke with a missing `--` separator / wrong arg count → exit 2, distinct from the exit-1 check-failure path, matching `scripts/validate_env.sh`'s convention.
- [ ] 4.2 Confirm red: `scripts/check_kong_restart_delta.sh` doesn't exist yet, so these tests fail (script-not-found), before task 5.

## 5. Implement `scripts/check_kong_restart_delta.sh` (GREEN for task 4)

- [ ] 5.1 Write `scripts/check_kong_restart_delta.sh` per `design.md`'s Decision 3 (usage: `<before-count> <threshold> -- <docker compose command...>`; `set -euo pipefail`; usage errors exit 2; resolves container id via `"${compose_cmd[@]}" ps -q kong`, exit 1 if empty; computes delta from `docker inspect --format='{{.RestartCount}}'`, exit 1 if non-numeric; on `delta > threshold`, emits `::error::`, dumps `"${compose_cmd[@]}" logs --tail=100 kong`, runs `"${compose_cmd[@]}" stop kong`, exits 1; otherwise prints `kong restart delta: N` and exits 0). Header comment states it's called from both `deploy.yml` jobs and `tests/unit/test_deploy_kong_reload_on_config_change.py`, single source of truth, mirroring `scripts/validate_env.sh`'s existing header convention.
- [ ] 5.2 `chmod +x scripts/check_kong_restart_delta.sh`.
- [ ] 5.3 Confirm green: `uv run --extra test pytest tests/unit/test_deploy_kong_reload_on_config_change.py -v` — all tests (shape + Caddy regression-pinning + behavioral) pass.

## 6. Validate + document manual verification and docs updates

- [ ] 6.1 Full `uv run --extra test pytest tests/unit/` run — no regressions.
- [ ] 6.2 `openspec validate fix-kong-reload-on-deploy --strict` passes.
- [ ] 6.3 Update `PROD_SETUP.md`'s "Deploying" numbered list to include the new `Restart Kong config` / `Kong crash-loop check` steps (it's already missing the existing Caddy reload/crash-loop steps — fold that correction in too while touching this list, matching the precedent in `add-bloommcp-prod-staging-data-dir-preflight`'s tasks.md, which did the same for a single added step).
- [ ] 6.4 Update this file with a **manual, post-merge-only** verification step (do NOT attempt from this environment — no SSH/deploy-host access): once this change reaches `staging` and staging redeploys, confirm `GET https://<staging-domain>/api/auth/v1/.well-known/openid-configuration` returns `200` (not the stale `401 No API key found in request`), proving both this fix and PR #613's routes are live together.
  - If it still 401s, check the `Kong crash-loop check (staging)` step's output in the deploy run first — it would indicate a bad `kong.yml` stopped Kong outright rather than this change simply not having deployed yet.
  - **Recovery if the crash-loop check fired and stopped Kong on staging**: (1) preferred — fix or revert the `kong.yml` change and let the next deploy's `Restart Kong config` step bring it back up automatically; (2) if urgent, SSH to the deploy host and run `docker compose -f docker-compose.prod.yml --env-file .env.staging -p bloom_v2_staging start kong` to restore service manually (this restores whatever `kong.yml` is currently checked out — revert first if that's still the broken version).

## 7. PR

- [ ] 7.1 Run `/pre-merge`; fix anything flagged.
- [ ] 7.2 Run `/pr-description`; open PR against `staging`, title reflecting spec + implementation bundled together (not proposal-only).

## 8. Archive (after merge)

- [ ] 8.1 Run `/openspec:archive fix-kong-reload-on-deploy` once merged, folding `deploy-config-reload` into `openspec/specs/`.
