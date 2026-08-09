# deploy-config-reload Specification

## Purpose
TBD - created by archiving change fix-kong-reload-on-deploy. Update Purpose after archive.
## Requirements
### Requirement: Deploy MUST detect content-only changes to bind-mounted config files separately from service-definition changes

`docker compose up -d --wait` only recreates a container when its _service definition_ changes (image, environment, command, volume list) — it does not detect that the _contents_ of a file behind an unchanged bind mount changed. Any service whose runtime config is delivered via a read-only bind mount into a container that only re-reads/re-derives that config at process start (not on a filesystem-change signal) MUST have its own explicit change-detection step in the deploy workflow, computed from the same pre-pull/post-pull commit range already used to reset the deploy host's checkout.

#### Scenario: Change detection reuses the pull step's before/after commits

- **GIVEN** the `Pull latest code` step has just run `git fetch` + `git reset --hard` and captured `BEFORE` (pre-reset HEAD) and `AFTER` (post-reset HEAD)
- **WHEN** the step checks whether a config file changed
- **THEN** it MUST run `git diff --quiet "$BEFORE" "$AFTER" -- <path>` scoped to that exact file
- **AND** it MUST emit a `<name>_changed` output (`true`/`false`) via `GITHUB_OUTPUT`, parsed from a `<NAME>_CHANGED=` marker line in the step's SSH output
- **AND** if the marker line is ever missing from the step's output (e.g. an SSH failure truncated it), the output MUST default to `true` — a missed reload is worse than an unnecessary one

#### Scenario: Caddyfile change detection (pre-existing behavior, documented here for the first time)

- **GIVEN** `caddy/Caddyfile` changed between `BEFORE` and `AFTER`
- **WHEN** the `Pull latest code` step (`id: pull_prod` / `id: pull_staging`) evaluates the diff
- **THEN** it MUST set `caddyfile_changed=true` in that step's `GITHUB_OUTPUT`

#### Scenario: kong.yml change detection

- **GIVEN** `volumes/api/kong.yml` changed between `BEFORE` and `AFTER`
- **WHEN** the same `Pull latest code` step evaluates the diff (reusing the already-computed `BEFORE`/`AFTER`, not a separate pull)
- **THEN** it MUST set `kongfile_changed=true` in that step's `GITHUB_OUTPUT`

### Requirement: Deploy MUST reload a config-delivering service when its bind-mounted config content changed

When a change-detection output from the requirement above is `true`, the deploy workflow MUST apply that service's config without waiting for an unrelated service-definition change to force a container recreate. The specific mechanism (in-place reload vs. full restart) MUST be whichever one is verified to actually re-derive that service's live config correctly — an in-place reload command that looks equivalent but doesn't reliably apply the new config is worse than a brief restart, because it fails silently.

#### Scenario: Caddy reloads in place (pre-existing behavior, documented here for the first time)

- **GIVEN** `caddyfile_changed == 'true'` for the current deploy
- **WHEN** the `Reload Caddy config` step runs
- **THEN** it MUST run `docker compose ... exec -T caddy caddy reload --config /etc/caddy/Caddyfile`
- **AND** this MUST NOT restart or recreate the `caddy` container — `caddy reload` re-reads the Caddyfile directly with no intermediate generation step, so an in-place, zero-downtime reload is correct here

#### Scenario: Restart step requires the kong container to already exist

- **GIVEN** the `Restart Kong config` step is about to capture a `RestartCount` baseline
- **WHEN** it resolves Kong's container via `docker compose ... ps -q kong`
- **THEN** if that resolves to an empty string, the step MUST fail with a clear `::error::` and MUST NOT attempt a restart — a missing container at this point is a pre-existing problem `--wait` should already have caught, not something this step should paper over by treating a missing container as `before=0`

#### Scenario: Kong restarts fully, because its config requires an entrypoint-only regeneration step

- **GIVEN** `kongfile_changed == 'true'` for the current deploy
- **AND** Kong's declarative config (`~/kong.yml`, referenced by `KONG_DECLARATIVE_CONFIG`) is generated from the bind-mounted `~/temp.yml` by an env-substitution step that only runs inside the container's custom `entrypoint:`, never on a live-reload signal
- **WHEN** the `Restart Kong config` step runs
- **THEN** it MUST capture Kong's `RestartCount` (via `docker inspect --format='{{.RestartCount}}'`) immediately before issuing the restart, for use by the crash-loop check
- **AND** it MUST then run a full `docker compose ... restart kong --timeout 10` (not `kong reload` or an Admin API `/config` call) so the entrypoint's substitution step re-runs against the current `~/temp.yml` content
- **AND** the step MUST NOT attempt `kong reload` as a means of applying `kong.yml` content changes — see `design.md`'s Decision 1 for the full rationale (Kong's DB-less declarative-config reload has a documented history of not reliably applying config changes, independent of the entrypoint-substitution problem above)

#### Scenario: A missing or malformed before-restart-count fails toward inaction, not a false crash-loop stop

- **GIVEN** the `Restart Kong config` step's `RestartCount` capture is ever missing or malformed in its own output (e.g. an SSH buffering glitch), unlike `kongfile_changed`'s deliberate fail-toward-`true` default
- **WHEN** the step writes its `before_restart_count` output
- **THEN** it MUST NOT default the missing value to `0` — doing so would inflate the crash-loop check's computed delta and could stop a perfectly healthy Kong based on absent data, a self-inflicted full-outage action
- **AND** it MUST instead pass the value through as-is (empty), relying on `scripts/check_kong_restart_delta.sh`'s own argument validation to reject it with a usage error (exit 2) — the deploy still correctly fails, but without taking the destructive stop-kong action

#### Scenario: Restart step waits for Kong to report healthy before the deploy continues

- **GIVEN** the `Restart Kong config` step has just issued `docker compose ... restart kong`
- **WHEN** the step decides when to hand off to the next step
- **THEN** it MUST delegate to `scripts/wait_for_kong_healthy.sh <container-id> 120`, which polls `docker inspect --format='{{.State.Health.Status}}'` on the same container until it reports `healthy` or the given timeout elapses, rather than a fixed sleep — see `design.md`'s Decision 5 for why a flat sleep is insufficient and for the full reasoning behind the 120s bound
- **AND** a timeout MUST NOT itself fail the step — the following crash-loop check step is what decides pass/fail, using `RestartCount`, which correctly reflects either a crash loop or a stuck-starting container either way

#### Scenario: wait_for_kong_healthy.sh is independently testable without a live deploy

- **GIVEN** `scripts/wait_for_kong_healthy.sh` is the single source of truth for the health-poll loop, invoked identically by both the forward-path restart step and the rollback step, for both `deploy-production` and `deploy-staging`
- **WHEN** a test invokes it directly via `subprocess` with `docker` stubbed on `PATH` to return a controlled healthcheck status sequence
- **THEN** it MUST print the final observed status to stdout and exit 0 once that status is `healthy`
- **AND** if the given timeout elapses without ever observing `healthy`, it MUST still print the last-observed status to stdout and exit 1, without treating the timeout itself as a usage error
- **AND** if `docker inspect` itself fails mid-poll (e.g. the container disappears), the script MUST fall back to treating that poll as `unknown` rather than aborting — mirroring `check_kong_restart_delta.sh`'s general resilience to transient Docker errors
- **AND** a non-numeric or non-positive `timeout-seconds` or `poll-interval-seconds` argument MUST be rejected as a usage error (exit 2), matching `check_kong_restart_delta.sh`'s validation convention for its own numeric arguments — a `poll-interval-seconds` of `0` in particular must be rejected, since it would spin the poll loop forever without `elapsed` ever advancing

### Requirement: Deploy MUST detect a config reload that put a service into a crash loop and stop it before it retries indefinitely

A bad config change (parse error, invalid reference) can put a service into a restart loop under its `restart: unless-stopped` policy. Left unchecked, this retries silently and, for services performing external side effects on start (e.g. ACME certificate requests), can burn through rate limits. The deploy workflow MUST detect this and stop the looping service, failing the deploy loudly instead of leaving it retrying.

#### Scenario: Caddy crash-loop check uses an absolute threshold (pre-existing behavior, documented here for the first time)

- **GIVEN** a clean `caddy reload` never changes `RestartCount` (no restart occurs on a successful in-place reload)
- **WHEN** the `Caddy crash-loop check` step runs (unconditionally, every deploy)
- **THEN** it MUST read the container's `RestartCount` and fail (dumping the last 100 log lines and stopping the container) if it is `> 2`
- **AND** since ACME/Let's Encrypt attempts happen on every Caddy start, this threshold exists specifically to stop repeated rate-limited ACME attempts from a bad Caddyfile

#### Scenario: Kong crash-loop check uses a before/after delta, because Kong's container persists restart history across its whole lifetime

- **GIVEN** the `Restart Kong config` step (previous requirement) deliberately restarts Kong via `docker compose restart` as its normal, successful-path action — a compose-issued restart does NOT itself increment `RestartCount` (only Docker's `restart: unless-stopped` policy retrying the container after an unexpected exit does), so the happy-path delta from this restart is `0`, not `1`
- **AND** Kong's container is never recreated by this restart, so restarts accumulated over its entire lifetime (including ones unrelated to and predating the current deploy) persist in `RestartCount` forever — an absolute threshold would eventually and permanently fail every future kong.yml-changing deploy once that lifetime count organically climbs past the threshold, regardless of whether the current config is fine
- **WHEN** the `Kong crash-loop check` step runs, gated on the same `kongfile_changed == 'true'` condition as the restart step
- **THEN** it MUST delegate the decision to `scripts/check_kong_restart_delta.sh <before-count> <threshold> -- <docker compose command...>`, passing the `RestartCount` captured immediately before the restart and the compose invocation prefix appropriate to the current job (prod or staging)
- **AND** the script MUST compute `delta = <RestartCount after> - <before-count>` and fail (dumping the last 100 log lines via `<compose command> logs --tail=100 kong` and stopping Kong via `<compose command> stop kong`) only if `delta > threshold`, so the deliberate restart itself is never mistaken for a crash loop
- **AND** the workflow MUST pass `2` as `<threshold>`, keeping Kong's tolerance for one incidental extra restart consistent with Caddy's own `> 2` threshold
- **AND** this check only detects the service _exiting_ (a crash loop) — it does not, by itself, verify that a `kong.yml` change that starts successfully is serving the intended routes correctly; see `design.md`'s Decision 2 "Known limitation" note

#### Scenario: check_kong_restart_delta.sh fails cleanly when the kong container does not exist

- **GIVEN** `docker compose ... ps -q kong` resolves to an empty string when the crash-loop check runs (e.g. the restart step's own container somehow disappeared)
- **WHEN** `scripts/check_kong_restart_delta.sh` runs
- **THEN** it MUST print a clear `::error::` and exit 1 without attempting `docker inspect`

#### Scenario: check_kong_restart_delta.sh fails cleanly on a malformed RestartCount reading

- **GIVEN** `docker inspect --format='{{.RestartCount}}'` returns an empty string or non-numeric output (an unexpected/malformed result, not a real restart count)
- **WHEN** `scripts/check_kong_restart_delta.sh` computes the delta
- **THEN** it MUST treat this as a hard failure — printing a clear `::error::` and exiting 1
- **AND** it MUST NOT silently coerce the malformed value to `0`, which could mask a real problem by making an actual crash loop compute as `delta <= threshold`

#### Scenario: Crash-loop check masks Kong's secret env vars before dumping logs

- **GIVEN** Kong's compose service injects `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`, `BLOOM_AGENT_KEY`, `DASHBOARD_USERNAME`, and `DASHBOARD_PASSWORD` as environment variables substituted into the generated `~/kong.yml` — sourced, respectively, from the deployed `.env.<env>` file's `ANON_KEY`, `SERVICE_ROLE_KEY`, `BLOOM_AGENT_KEY`, `DASHBOARD_USERNAME`, and `DASHBOARD_PASSWORD` keys (the compose-level names are what Kong's container receives; the `.env.<env>` names are what the workflow reads them as, matching how every other `.env.<env>`-sourced value in this workflow is named)
- **WHEN** the `Kong crash-loop check` step is about to run `<compose command> logs --tail=100 kong` (i.e. `delta > threshold`)
- **THEN** it MUST emit `::add-mask::` annotations for all five secret values before the log dump runs, as defense-in-depth beyond GitHub's automatic literal-secret redaction — see `design.md`'s Decision 6 (this step runs before this workflow's existing smoke-test step, which already masks `ANON_KEY` but too late to protect this earlier log dump)

#### Scenario: check_kong_restart_delta.sh is independently testable without a live deploy

- **GIVEN** `scripts/check_kong_restart_delta.sh` is the single source of truth for the delta/threshold decision, invoked identically by both the `deploy-production` and `deploy-staging` jobs
- **WHEN** a test invokes it directly via `subprocess` with `docker` stubbed on `PATH` to return a controlled `RestartCount` and record `compose ... logs` / `compose ... stop` invocations
- **THEN** a `delta <= threshold` case (including `delta == threshold` exactly) MUST exit 0 without calling `logs` or `stop`
- **AND** a `delta > threshold` case MUST exit 1 and MUST have invoked both `logs --tail=100 kong` and `stop kong` through the passed-through compose command
- **AND** a malformed usage invocation (wrong argument count, or a missing `--` separator) MUST exit 2, distinct from a `delta > threshold` failure's exit 1 — matching `scripts/validate_env.sh`'s existing usage-error-vs-check-failure exit code convention

### Requirement: Rollback MUST restore a config-delivering service's content too, not just recreate it

`Rollback on failure`'s `git reset --hard <previous-sha>` followed by `docker compose ... up -d --wait` is the same mechanism this whole capability exists to work around — it cannot detect that a bind-mounted config's _content_ reverted, any more than a forward deploy's `up -d` can detect it changed. If a config-delivering service was already reloaded onto new content earlier in the same job (via the requirements above) before a later step failed, rollback MUST NOT leave that service running the failed deploy's config while believing the rollback succeeded.

#### Scenario: Rollback restarts Kong when kong.yml changed during this job

- **GIVEN** the `Restart Kong config` step already restarted Kong onto `kongfile_changed`'s new content earlier in this job
- **AND** a later step (cert verification, smoke test, migrations, schema grants) fails, triggering `Rollback on failure`
- **WHEN** the rollback step's own `git reset --hard $PREV` and `up -d --wait` complete successfully
- **THEN** it MUST check the same `kongfile_changed` output used by the forward path, and if `true`, restart Kong again (same restart + health-poll mechanism as the forward path) so Kong picks up the just-reverted `kong.yml`
- **AND** this restart MUST happen after the rollback's own `up -d`, not before — restarting Kong before the code/env files are reverted would restart it onto the still-broken config
- **AND** if the kong container cannot be found at this point, the step MUST emit a `::warning::` and continue rather than failing the already-in-progress rollback over a secondary concern
- **AND** if the restart command itself fails, the step MUST emit a `::warning::` and continue (not `exit 1`) — the rollback SSH block runs under `set -e`, and by this point the critical work (`git reset --hard` and `up -d --wait`) has already succeeded; a bare, unguarded restart command would let a secondary Kong-restart hiccup abort the whole step and falsely report that the rollback itself failed

#### Scenario: Rollback's final success message MUST NOT claim Kong is healthy when it isn't

- **GIVEN** `kongfile_changed` was `true` for this job and the rollback's Kong-restart command itself succeeded
- **WHEN** the subsequent health-poll (`scripts/wait_for_kong_healthy.sh`) does not observe `healthy` within its timeout — Kong stuck `starting`/`unhealthy`
- **THEN** the rollback step MUST emit a `::warning::` for the non-healthy status
- **AND** the rollback step's final message MUST NOT be the unqualified `Rollback complete — previous version restored and healthy` — it MUST instead be a `::warning::`-prefixed message that does not claim Kong is healthy
- **AND** this same gating applies to the other two branches that already warn in this requirement (kong container not found; restart command failed) — any branch that emits a warning about Kong's state MUST also suppress the unqualified success message, not just log a warning alongside it
- **AND** the rollback step itself MUST exit non-zero in this case (not just print a warning and exit 0) — a step that reports green in the GitHub Actions UI while Kong's health is unconfirmed would mislead on-call scanning step colors rather than reading annotations; this does not retroactively mark the already-succeeded code/env revert as failed, it specifically reflects that Kong's own state is unconfirmed

#### Scenario: Restart commands are bounded by an external timeout, not just Docker's internal grace period

- **GIVEN** `docker compose restart kong --timeout 10`'s `--timeout` flag only bounds Docker's own SIGTERM-then-SIGKILL grace period for the stop phase, not the whole command's wall-clock time
- **WHEN** the Docker daemon itself becomes unresponsive (e.g. host resource exhaustion)
- **THEN** both the forward-path and rollback-path Kong restart invocations MUST be wrapped in an external `timeout 60`, so a hung Docker daemon cannot block the step indefinitely on top of Docker's own internal timeout

