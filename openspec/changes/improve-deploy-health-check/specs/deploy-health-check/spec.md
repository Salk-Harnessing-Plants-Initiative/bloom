# deploy-health-check Specification

## Purpose

Verify that production and staging deploys only report success when every service is actually ready to serve traffic — closing the gap where hand-rolled `jq` polling treats `Health: starting` or "running but unchecked" containers as healthy.

## ADDED Requirements

### Requirement: Deploy MUST wait for every service to reach a healthy state

The deploy workflow MUST use Docker Compose's native `--wait` flag so that `docker compose up` blocks until every service with a healthcheck reaches `Health: healthy` and every service without a healthcheck reaches `State: running`.

#### Scenario: Deploy succeeds when all services become healthy within timeout

Given every service in `docker-compose.prod.yml` has a healthcheck and the stack is valid
When the deploy workflow runs `docker compose up -d --build --remove-orphans --wait --wait-timeout 600`
Then the command MUST block until all healthchecks pass or the timeout expires
And the command MUST exit 0 when all services are healthy
And the workflow MUST NOT invoke any hand-rolled polling loop

#### Scenario: Deploy fails fast and prints diagnostics when a service fails its healthcheck

Given one service enters a crash loop during deploy
When `docker compose up --wait` times out
Then the deploy step MUST exit non-zero
And a diagnostics step with `if: failure()` MUST run `docker compose ps` and `docker compose logs --tail=200`
And the rollback step MUST trigger off the deploy failure, not off a separate health-check step

#### Scenario: Deploy does not declare success while services are in starting state

Given services are mid-boot with `Health: starting`
When the workflow polls for completion
Then the workflow MUST NOT exit 0 until each service transitions from `starting` to `healthy` (or `running` if no healthcheck is defined)

### Requirement: Every long-running production service MUST define a Docker healthcheck

Every long-running service in `docker-compose.prod.yml` MUST define a `healthcheck:` block so that `--wait` cannot treat a running-but-broken container as healthy. One-shot services (e.g. `minio-init`) are exempt because Docker Compose `--wait` already waits for them to exit successfully via `service_completed_successfully`.

#### Scenario: bloom-web is considered healthy only when its HTTP health endpoint responds

Given `bloom-web` is running
When its healthcheck probes `http://localhost:3000/api/health`
Then the endpoint MUST return HTTP 200 and a JSON body
And Docker MUST report `Health: healthy` only after a successful probe

#### Scenario: kong is considered healthy only when its admin health command passes

Given `kong` is running
When its healthcheck runs `kong health`
Then `kong health` MUST exit 0 for Docker to report `Health: healthy`

#### Scenario: caddy is considered healthy only when it can serve its own health route

Given `caddy` is running
When its healthcheck probes a local `/caddy-health` route
Then the Caddyfile MUST include a matcher that returns HTTP 200 for `/caddy-health` on the internal listener
And no secrets or cert data MUST be exposed by that route

#### Scenario: Supabase services are considered healthy only when their built-in health endpoints respond

Given a Supabase service (`auth`, `rest`, `realtime`, `storage`, `supavisor`, `studio`, `meta`) is running
When its healthcheck probes the service's published health endpoint
Then Docker MUST report `Health: healthy` only after the probe returns 200
And the healthcheck `start_period` MUST be tuned per-service to accommodate boot time (e.g. Realtime's seed phase of ~60s)

#### Scenario: Imgproxy is considered healthy only when its built-in health command passes

Given `imgproxy` is running
When its healthcheck runs `imgproxy health`
Then `imgproxy health` MUST exit 0 for Docker to report `Health: healthy`

#### Scenario: One-shot containers are exempt from the healthcheck requirement

Given `minio-init` is a one-shot container that exits after registering buckets
When the deploy workflow waits for it
Then `docker compose up --wait` MUST accept `service_completed_successfully` as the readiness signal
And no Docker healthcheck is required on the service

### Requirement: Deploy MUST run an HTTP-level smoke test before declaring success

After `--wait` reports all services healthy, the deploy workflow MUST exercise real request paths through Caddy to verify the public-facing endpoints respond.

#### Scenario: Smoke test exercises main, API, and studio routes

Given Caddy and all upstream services are healthy per Docker
When the smoke test step runs
Then it MUST make HTTP requests to at least `/`, `/api`, and `/studio` through Caddy with the production `Host:` header
And any response with status 500-599 or connection failure (curl exit code ≠ 0) MUST fail the workflow

#### Scenario: Smoke test does not leak secrets to workflow logs

Given secrets are used to construct the Host header and URL
When curl runs during the smoke test
Then only HTTP status codes and endpoint paths MUST appear in workflow output
And full URLs containing secret values MUST NOT be printed

### Requirement: Deploy MUST fail fast if the server's Docker Compose is too old

The deploy workflow MUST verify the remote Docker Compose version is at least 2.17.0 before running `up --wait`, because `--wait` is silently ignored on older versions.

#### Scenario: Preflight blocks deploy when remote Compose is v2.16 or older

Given the Salk server has Docker Compose 2.16.3 installed
When the deploy workflow runs the preflight version check
Then the workflow MUST fail with a clear error naming the required minimum version
And the deploy step MUST NOT proceed to `up --wait`
