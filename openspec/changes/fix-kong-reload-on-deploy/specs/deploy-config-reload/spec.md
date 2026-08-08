## ADDED Requirements

### Requirement: Deploy MUST detect content-only changes to bind-mounted config files separately from service-definition changes

`docker compose up -d --wait` only recreates a container when its *service definition* changes (image, environment, command, volume list) — it does not detect that the *contents* of a file behind an unchanged bind mount changed. Any service whose runtime config is delivered via a read-only bind mount into a container that only re-reads/re-derives that config at process start (not on a filesystem-change signal) MUST have its own explicit change-detection step in the deploy workflow, computed from the same pre-pull/post-pull commit range already used to reset the deploy host's checkout.

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

#### Scenario: Kong restarts fully, because its config requires an entrypoint-only regeneration step

- **GIVEN** `kongfile_changed == 'true'` for the current deploy
- **AND** Kong's declarative config (`~/kong.yml`, referenced by `KONG_DECLARATIVE_CONFIG`) is generated from the bind-mounted `~/temp.yml` by an env-substitution step that only runs inside the container's custom `entrypoint:`, never on a live-reload signal
- **WHEN** the `Restart Kong config` step runs
- **THEN** it MUST run a full `docker compose ... restart kong` (not `kong reload` or an Admin API `/config` call) so the entrypoint's substitution step re-runs against the current `~/temp.yml` content
- **AND** it MUST capture Kong's `RestartCount` (via `docker inspect --format='{{.RestartCount}}'` on the container resolved by `docker compose ... ps -q kong`) immediately before issuing the restart, for use by the crash-loop check
- **AND** the step MUST NOT attempt `kong reload` as a means of applying `kong.yml` content changes, because Kong's DB-less declarative-config reload has a documented history of not reliably applying config changes across multiple versions (Kong GitHub issues #4808, #5898, #6447), independent of the entrypoint-substitution problem above

### Requirement: Deploy MUST detect a config reload that put a service into a crash loop and stop it before it retries indefinitely

A bad config change (parse error, invalid reference) can put a service into a restart loop under its `restart: unless-stopped` policy. Left unchecked, this retries silently and, for services performing external side effects on start (e.g. ACME certificate requests), can burn through rate limits. The deploy workflow MUST detect this and stop the looping service, failing the deploy loudly instead of leaving it retrying.

#### Scenario: Caddy crash-loop check uses an absolute threshold (pre-existing behavior, documented here for the first time)

- **GIVEN** a clean `caddy reload` never changes `RestartCount` (no restart occurs on a successful in-place reload)
- **WHEN** the `Caddy crash-loop check` step runs (unconditionally, every deploy)
- **THEN** it MUST read the container's `RestartCount` and fail (dumping the last 100 log lines and stopping the container) if it is `> 2`
- **AND** since ACME/Let's Encrypt attempts happen on every Caddy start, this threshold exists specifically to stop repeated rate-limited ACME attempts from a bad Caddyfile

#### Scenario: Kong crash-loop check uses a before/after delta, because the reload step itself causes one expected restart

- **GIVEN** the `Restart Kong config` step (previous requirement) deliberately restarts Kong as its normal, successful-path action — so `RestartCount` is expected to increase by exactly 1 even when `kong.yml` is valid
- **WHEN** the `Kong crash-loop check` step runs, gated on the same `kongfile_changed == 'true'` condition as the restart step
- **THEN** it MUST delegate the decision to `scripts/check_kong_restart_delta.sh <before-count> <threshold> -- <docker compose command...>`, passing the `RestartCount` captured immediately before the restart and the compose invocation prefix appropriate to the current job (prod or staging)
- **AND** the script MUST compute `delta = <RestartCount after> - <before-count>` and fail (dumping the last 100 log lines via `<compose command> logs --tail=100 kong` and stopping Kong via `<compose command> stop kong`) only if `delta > threshold`, so the deliberate restart itself is never mistaken for a crash loop
- **AND** the workflow MUST pass `2` as `<threshold>`, keeping Kong's tolerance for one incidental extra restart consistent with Caddy's own `> 2` threshold

#### Scenario: check_kong_restart_delta.sh is independently testable without a live deploy

- **GIVEN** `scripts/check_kong_restart_delta.sh` is the single source of truth for the delta/threshold decision, invoked identically by both the `deploy-production` and `deploy-staging` jobs
- **WHEN** a test invokes it directly via `subprocess` with `docker` stubbed on `PATH` to return a controlled `RestartCount` and record `compose ... logs` / `compose ... stop` invocations
- **THEN** a `delta <= threshold` case MUST exit 0 without calling `logs` or `stop`
- **AND** a `delta > threshold` case MUST exit 1 and MUST have invoked both `logs --tail=100 kong` and `stop kong` through the passed-through compose command
