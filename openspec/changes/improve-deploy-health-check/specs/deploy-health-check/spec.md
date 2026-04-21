# deploy-health-check Specification

## Purpose

Verify that production and staging deploys only report success when every service is actually ready to serve traffic — closing the gap where hand-rolled `jq` polling treats `Health: starting` or "running but unchecked" containers as healthy.

## ADDED Requirements

### Requirement: Deploy MUST wait for every service to reach a healthy state

The deploy workflow MUST use Docker Compose's native `--wait` flag so that `docker compose up` blocks until every service with a healthcheck reaches `Health: healthy` and every service without a healthcheck reaches `State: running`. The `--wait-timeout` flag (added in Compose v2.17) MUST be set so that `--wait` has a finite ceiling; without it, `--wait` would block indefinitely on a stuck service.

#### Scenario: Deploy succeeds when all services become healthy within timeout

- **GIVEN** every long-running service in `docker-compose.prod.yml` has a healthcheck (or is a one-shot) and the stack is valid
- **WHEN** the deploy workflow runs `docker compose up -d --build --remove-orphans --wait --wait-timeout 600`
- **THEN** the command MUST block until all healthchecks pass or the timeout expires
- **AND** the command MUST exit 0 when all services are healthy
- **AND** the workflow MUST NOT invoke any hand-rolled polling loop

#### Scenario: Deploy fails fast and prints diagnostics when a service fails its healthcheck

- **GIVEN** one service enters a crash loop during deploy
- **WHEN** `docker compose up --wait` times out
- **THEN** the deploy step MUST exit non-zero
- **AND** a diagnostics step with `if: failure()` MUST run `docker compose ps` and `docker compose logs --tail=200`
- **AND** the rollback step MUST trigger off the deploy failure, not off a separate health-check step

#### Scenario: Deploy does not declare success while services are in starting state

- **GIVEN** services are mid-boot with `Health: starting`
- **WHEN** the workflow polls for completion
- **THEN** the workflow MUST NOT exit 0 until each service transitions from `starting` to `healthy` (or `running` if no healthcheck is defined)

### Requirement: Every long-running production service MUST define a Docker healthcheck when a viable probe exists

Every long-running service in `docker-compose.prod.yml` MUST define a `healthcheck:` block so that `--wait` cannot treat a running-but-broken container as healthy, PROVIDED a viable in-container probe tool exists. Images with no shell, no wget, no curl, and no scripting runtime (e.g., `FROM scratch`) are exempt; follow-up work MAY add indirect coverage via sidecar probes. One-shot services (e.g., `minio-init`) are exempt because Docker Compose `--wait` already waits for them to exit successfully via `service_completed_successfully`.

#### Scenario: Probe tool choice matches what the image ships

- **GIVEN** a service's image is pinned to a specific tag
- **WHEN** a healthcheck is added
- **THEN** the probe command MUST use a tool (wget, curl, native CLI, node one-liner, bash `/dev/tcp`, etc.) verified to exist in that image
- **AND** the choice MUST be verifiable via `docker run --rm --entrypoint sh <image> -c "command -v <tool>"`

#### Scenario: bloom-web is considered healthy only when its HTTP health endpoint responds

- **GIVEN** `bloom-web` is running
- **WHEN** its healthcheck probes `http://localhost:3000/api/health`
- **THEN** the endpoint MUST return HTTP 200 and a JSON body
- **AND** Docker MUST report `Health: healthy` only after a successful probe

#### Scenario: kong is considered healthy only when its admin health command passes

- **GIVEN** `kong` is running
- **WHEN** its healthcheck runs `kong health`
- **THEN** `kong health` MUST exit 0 for Docker to report `Health: healthy`

#### Scenario: caddy is considered healthy only when it can serve its own health route

- **GIVEN** `caddy` is running
- **WHEN** its healthcheck probes a local `/caddy-health` route on the internal `:2021` listener
- **THEN** the Caddyfile MUST include a matcher that returns HTTP 200 for `/caddy-health` on that listener
- **AND** no secrets or cert data MUST be exposed by that route

#### Scenario: Supabase services are considered healthy only when their built-in health endpoints respond

- **GIVEN** a Supabase service (`auth`, `realtime`, `storage`, `supavisor`, `studio`, `meta`) is running
- **WHEN** its healthcheck probes the service's published health endpoint
- **THEN** Docker MUST report `Health: healthy` only after the probe returns 200
- **AND** the healthcheck `start_period` MUST be tuned per-service to accommodate boot time (e.g. Realtime's seed phase of ~60s)

#### Scenario: Imgproxy is considered healthy only when its built-in health command passes

- **GIVEN** `imgproxy` is running
- **WHEN** its healthcheck runs `imgproxy health`
- **THEN** `imgproxy health` MUST exit 0 for Docker to report `Health: healthy`

#### Scenario: One-shot containers are exempt from the healthcheck requirement

- **GIVEN** `minio-init` is a one-shot container that exits after registering buckets
- **WHEN** the deploy workflow waits for it
- **THEN** `docker compose up --wait` MUST accept `service_completed_successfully` as the readiness signal
- **AND** no Docker healthcheck is required on the service

#### Scenario: Services in images with no probe tooling are exempt pending follow-up

- **GIVEN** a service's image is `FROM scratch` (e.g., `postgrest/postgrest`) or otherwise ships no shell and no HTTP probe tool
- **WHEN** a healthcheck cannot be added in-container
- **THEN** the service MAY omit `healthcheck:`, with `--wait` treating it as ready-when-running
- **AND** a follow-up GitHub issue MUST track adding indirect coverage (sidecar, custom Dockerfile, or kong-indirect)

### Requirement: Deploy MUST run an HTTP-level smoke test before declaring success

After `--wait` reports all services healthy, the deploy workflow MUST exercise real request paths through Caddy to verify the public-facing endpoints respond.

#### Scenario: Smoke test exercises every Caddy site block by Host header

- **GIVEN** Caddy and all upstream services are healthy per Docker
- **WHEN** the smoke test step runs
- **THEN** it MUST make HTTP requests to at least one route per Caddy site block (`DOMAIN_MAIN`, `DOMAIN_STUDIO`, `DOMAIN_MINIO`) using the corresponding `Host:` header
- **AND** any response with status < 200 or >= 400, or connection failure (curl exit code ≠ 0), MUST fail the workflow

#### Scenario: Smoke test follows auto-HTTPS redirects and accepts tls internal certs

- **GIVEN** Caddy is configured with auto-HTTPS (redirecting port 80 → 443) and `tls internal` (self-signed CA)
- **WHEN** the smoke test probes `http://localhost:<port>/...`
- **THEN** curl MUST be invoked with `-L` (follow redirects) and `-k` (accept self-signed) so the probe measures the real app response, not Caddy's redirect or TLS rejection

#### Scenario: Smoke test sources domain values from deployed env file

- **GIVEN** the deploy workflow writes `DOMAIN_MAIN`, `DOMAIN_STUDIO`, `DOMAIN_MINIO`, and `CADDY_HTTP_LISTEN_PORT` into the server-side `.env.<env>` file before the smoke test runs (today from GitHub Secrets; post-PR #144 from committed `.env.<env>.defaults` merged with secrets)
- **WHEN** the smoke test assembles Host headers and URLs
- **THEN** it MUST read those values from the deployed `.env.prod` or `.env.staging` file on the server — NOT directly from `${{ secrets.* }}` expressions in the workflow step itself
- **AND** this decouples the smoke test from the config-source refactor, so the same code works before and after PR #144 merges

#### Scenario: Smoke test does not leak secrets to workflow logs

- **GIVEN** secrets are used elsewhere in the deploy workflow
- **WHEN** curl runs during the smoke test
- **THEN** only HTTP status codes and endpoint paths MUST appear in workflow output
- **AND** full URLs or Host headers containing secret values MUST NOT be printed

### Requirement: Deploy rollback MUST use the same health gate as forward deploy

When the rollback-on-failure step fires, its `docker compose up` MUST use `--wait --wait-timeout 300` so a rollback that restores an also-broken previous version is detected, not silently reported as successful.

#### Scenario: Rollback waits for rolled-back services to become healthy

- **GIVEN** the forward deploy failed and the rollback step is running
- **WHEN** `git reset --hard <previous-sha>` succeeds and `docker compose up -d --build --remove-orphans --wait --wait-timeout 300` is invoked
- **THEN** the step MUST block until the rolled-back stack passes every healthcheck, or the 300s timeout expires
- **AND** on timeout, the step MUST emit `::error::Rollback reset to $PREV succeeded but docker compose --wait failed` and exit 1
- **AND** the message `Rollback complete — previous version restored and healthy` MUST only print after `--wait` returns 0

### Requirement: Deploy MUST fail fast if the server's Docker Compose is too old

The deploy workflow MUST verify the remote Docker Compose version is at least 2.17.0 before running `up --wait`, because `--wait-timeout` is silently ignored on older versions.

#### Scenario: Preflight blocks deploy when remote Compose is v2.16 or older

- **GIVEN** the Salk server has Docker Compose 2.16.3 installed
- **WHEN** the deploy workflow runs the preflight version check
- **THEN** the workflow MUST fail with a clear error naming the required minimum version
- **AND** the deploy step MUST NOT proceed to `up --wait`

#### Scenario: Preflight handles version strings with and without `v` prefix

- **GIVEN** some Docker installs emit `v2.21.0` and others emit `2.21.0` from `docker compose version --short`
- **WHEN** the preflight parses the version
- **THEN** the leading `v` MUST be stripped before numeric comparison
- **AND** both formats MUST pass when the numeric version is >= 2.17

### Requirement: Deploy MUST serialize across prod and staging

The deploy workflow MUST prevent concurrent runs of `deploy-production` and `deploy-staging` because both target the same Salk server + Docker daemon + shared rollback state at `.previous_sha`.

#### Scenario: Second deploy queues behind first

- **GIVEN** a `deploy-production` run is in progress
- **WHEN** a push to staging triggers `deploy-staging`
- **THEN** the staging job MUST queue behind the prod job (`cancel-in-progress: false`)
- **AND** MUST NOT proceed until the prod job completes (success, failure, or rollback-complete)

#### Scenario: Concurrent prod deploys cannot race on .previous_sha

- **GIVEN** a deploy is mid-way through the "Save previous SHA for rollback" step
- **WHEN** a second deploy is triggered
- **THEN** the concurrency group MUST prevent the second deploy from running the same step until the first completes
- **AND** `.previous_sha` MUST NOT be overwritten by an interleaved deploy
