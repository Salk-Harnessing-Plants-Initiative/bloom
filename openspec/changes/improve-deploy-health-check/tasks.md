## 1. Layer 1 — `docker compose up --wait` replaces hand-rolled polling

- [x] 1.1 Add `Verify Docker Compose version (>= 2.17 required for --wait-timeout)` preflight step to both `deploy-production` and `deploy-staging` jobs in `.github/workflows/deploy.yml`
- [x] 1.2 Preflight MUST strip leading `v` from version string before numeric comparison (some installs emit `v2.21.0`)
- [x] 1.3 Error message names `--wait-timeout` (not `--wait`) as the feature gated by the 2.17 floor
- [x] 1.4 Replace forward-deploy step's `docker compose up -d --build --remove-orphans` with `up -d --build --remove-orphans --wait --wait-timeout 600`
- [x] 1.5 Remove the hand-rolled polling loop (`for i in $(seq 1 60); do UNHEALTHY=$(... | jq ...); ...; done`) — replaced entirely by `--wait`
- [x] 1.6 Add `Deploy diagnostics on failure` step gated by `if: failure()` that dumps `docker compose ps` + `docker compose logs --tail=200` in collapsible `::group::` sections
- [x] 1.7 Add `set -euo pipefail` to the forward-deploy SSH command
- [x] 1.8 Explicit `: "${PROD_DEPLOY_PATH:?...}"` guards for secret values that could interpolate to empty strings (set -u doesn't catch that case)

## 2. Layer 2 — Healthchecks on every long-running service

Each healthcheck uses `interval: 10s, timeout: 5s, retries: 5`, with `start_period` tuned per service (20s default; 30s for node/Next.js boot; 60s for Realtime/Supavisor seed phase).

- [x] 2.1 `caddy`: `wget -q --spider http://localhost:2021/caddy-health` — also adds `:2021 { respond /caddy-health "ok" 200 }` block to `caddy/Caddyfile` as an internal-only listener
- [x] 2.2 `bloom-web`: `wget -q --spider http://localhost:3000/api/health` — adds new `web/app/api/health/route.ts` returning `{ ok: true, commit: <sha> }`
- [x] 2.3 `kong`: `kong health` (built-in CLI)
- [x] 2.4 `auth` (supabase/gotrue:v2.188.1): `wget -q --spider http://localhost:9999/health` (wget verified present in Alpine base)
- [x] 2.5 `realtime` (supabase/realtime:v2.34.47): `CMD-SHELL curl -sf http://localhost:4000/api/health` — image is debian-slim, has curl + sh but NOT wget. Verified via `docker run --rm --entrypoint sh <image> -c "command -v wget"` (empty).
- [x] 2.6 `storage` (supabase/storage-api:v1.48.14): `wget -q --spider http://localhost:5000/status` (wget verified present)
- [x] 2.7 `supavisor` (supabase/supavisor:2.7.4): `CMD-SHELL curl -sf http://localhost:4000/api/health` — same image family as realtime, same no-wget-but-has-curl verification
- [x] 2.8 `studio`: `wget -q --spider http://localhost:3000/api/profile`
- [x] 2.9 `imgproxy`: `imgproxy health` (built-in CLI, v3.10+)
- [x] 2.10 `meta` (supabase/postgres-meta:v0.96.2): `CMD-SHELL node -e "require('http').get(...)..."` node-based probe — image has bash + node but NO wget or curl. Verified via `docker run` that both `command -v wget` and `command -v curl` return empty.
- [x] 2.11 `rest` (postgrest/postgrest:v12.2.12): **NO healthcheck** — image is `FROM scratch` with no shell, no wget, no curl, no node. `--wait` treats no-healthcheck as ready-when-running. Follow-up at #161 to add indirect coverage (sidecar / custom Dockerfile / kong-indirect).
- [x] 2.12 `minio-init`: already one-shot, no healthcheck required; `--wait` treats `service_completed_successfully` as ready

## 3. Layer 3 — HTTP smoke test after `--wait` passes

- [x] 3.1 Add `Smoke test` step to both `deploy-production` and `deploy-staging` jobs, running AFTER the successful forward deploy
- [x] 3.2 Probes hit each Caddy site block by Host header: `DOMAIN_MAIN` + `DOMAIN_STUDIO` + `DOMAIN_MINIO` (NOT a single host with path-based probes — Caddy routes subdomains, not paths, for studio + minio)
- [x] 3.3 Source domain values + port from the deployed `.env.prod` / `.env.staging` file on the server (NOT from GitHub Secrets — PR #144 moved these to committed `.env.*.defaults` files)
- [x] 3.4 Success window is 2xx/3xx only. 4xx (routing misses) AND 5xx AND connection failure (curl exit != 0) all fail the step.
- [x] 3.5 curl invoked with `-L -k` to follow auto-HTTPS 308 redirect and accept `tls internal` self-signed cert (PR #145)
- [x] 3.6 Include `/api/health` probe (zero cost; covers the new Next.js health route that otherwise has no external test)

## 4. Concurrency guard

- [x] 4.1 Both `deploy-production` and `deploy-staging` jobs share `concurrency: { group: deploy-bloom, cancel-in-progress: false }` so only one deploy runs at a time against the single Salk server + single Docker daemon + shared `.previous_sha` rollback state

## 5. Rollback aligned with forward deploy

- [x] 5.1 Rollback's `docker compose up` uses `--wait --wait-timeout 300` (300s because rollback reuses already-built images)
- [x] 5.2 `--wait` failure is caught with explicit `::error::Rollback reset to $PREV succeeded but docker compose --wait failed...` annotation (extends the error-annotation pattern from merged PR #139)
- [x] 5.3 Success message `Rollback complete — previous version restored and healthy` only prints after `--wait` returns 0

## 6. Validation

- [x] 6.1 `openspec validate improve-deploy-health-check --strict` passes
- [x] 6.2 `.github/workflows/deploy.yml` YAML parses cleanly
- [x] 6.3 `docker-compose.prod.yml` YAML parses cleanly
- [x] 6.4 Verified image probe-tool availability via `docker run --rm --entrypoint sh <image> -c "command -v <tool>"` for each of the 4 previously-broken services (rest/realtime/supavisor/meta)
- [ ] 6.5 Staging deploy completes successfully end-to-end (requires PR merged + push to staging branch)
- [ ] 6.6 Deliberately broken healthcheck (injected via draft PR) fails the staging deploy within the 600s ceiling, rollback fires, rollback itself waits for healthy (tests Requirement 4)
- [ ] 6.7 Concurrent-deploy test: push to main AND staging within 30s of each other, verify GH Actions queue-not-cancel behavior

## 7. Follow-ups (filed separately, NOT in this PR)

- [ ] 7.1 **#161 — PostgREST healthcheck coverage** (sidecar / custom Dockerfile / kong-indirect)
- [ ] 7.2 Post-deploy drift detection (crash-after-deploy-success invisible — file issue)
- [ ] 7.3 Audit-trail structured logging on deploy success (SHA, digests, duration)
- [ ] 7.4 Image-tooling CI preflight test — would have caught the `wget`-in-scratch class of bug before merge
- [ ] 7.5 Stability requirement — N consecutive healthy probes before flipping to `healthy` (currently docker flips on first success during `start_period`)
- [ ] 7.6 External-reachability probe from `ubuntu-latest` runner (loopback isn't everything — expected to fail from outside Salk until DNS + firewall finalize per #135)
