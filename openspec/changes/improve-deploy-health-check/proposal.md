# Change: Improve Deploy Workflow Health Check (3 layers)

## Why

The production deploy workflow ([.github/workflows/deploy.yml](.github/workflows/deploy.yml)) currently uses a hand-rolled polling loop with this filter:

```bash
jq '[.[] | select(.State == "restarting" or .Health == "unhealthy")] | length'
```

Two structural bugs make this unsafe for the first production deploy (planned for Monday):

1. **Misses `Health: starting`** — Containers in the middle of booting pass the filter. Combined with a 5-second first iteration, the workflow can declare "Production deployed successfully!" while every service is still initializing. Users hit 502s before realizing the deploy "succeeded."
2. **No-healthcheck services pass trivially** — `bloom-web`, `kong`, `caddy` have no Docker healthcheck defined. Their `Health` field is empty → filter is false → they are counted as healthy no matter what state their process is actually in.

This proposal replaces the hand-rolled logic with Docker-native tooling, fills the healthcheck gaps on the services that have none, and adds an end-to-end smoke test.

## What Changes

Three layers, each deliverable independently:

### Layer 1 — Replace hand-rolled polling with `docker compose up --wait`

Docker Compose has a built-in `--wait` flag (added v2.1.1) that blocks `up` until:

- Every service with a healthcheck reaches `Health: healthy`
- Every service without a healthcheck reaches `State: running`
- Or `--wait-timeout` (added v2.17.0) expires, in which case `up` exits non-zero with diagnostics.

**Minimum Compose version: 2.17.0** — required for `--wait-timeout`. Without the timeout flag, `--wait` would block indefinitely on a stuck service.

Replace the current two-step pattern:

```yaml
- name: Deploy production stack
  run: ssh ... "docker compose up -d --build --remove-orphans"
- name: Health check
  run: ssh ... "for i in {1..60}; do UNHEALTHY=$(... | jq ...); ...; done"
```

with:

```yaml
- name: Deploy production stack
  run: |
    ssh ... "
      set -euo pipefail
      cd /data/bloom/production
      docker compose -f docker-compose.prod.yml --env-file .env.prod \
        up -d --build --remove-orphans --wait --wait-timeout 600
    "
- name: Deploy diagnostics on failure
  if: failure()
  run: |
    ssh ... "
      cd /data/bloom/production
      docker compose -f docker-compose.prod.yml --env-file .env.prod ps
      docker compose -f docker-compose.prod.yml --env-file .env.prod logs --tail=200
    "
```

Removes ~30 lines of `jq`/`seq`/`sleep` per job (prod + staging = 60 lines total), removes the NDJSON parsing concern entirely, and correctly handles the `starting` state.

### Layer 2 — Add healthchecks to every service that lacks them

Current audit of [docker-compose.prod.yml](docker-compose.prod.yml): **16 services, only 4 custom files have healthchecks** (`langchain-agent`, `bloommcp`, `supabase-minio`, `db-prod`). Without healthchecks on the other 11, Layer 1's `--wait` treats them as healthy the instant they're `running`, even if the app inside is crashed.

Add `healthcheck:` blocks to every remaining service. The probe tool (`wget` vs `curl` vs `node` vs a service-native CLI) depends on what each image actually ships — verified by `docker run --rm --entrypoint sh <image> -c "command -v <tool>"` against the pinned tags:

| Service       | Probe                                                                                                                                                                        |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `caddy`     | `wget -q --spider http://localhost:2021/caddy-health` (new Caddyfile route on internal `:2021` listener returning 200; BusyBox wget in `caddy:2.11.2-alpine`)               |
| `bloom-web` | `wget -q --spider http://localhost:3000/api/health` (new Next.js route in `web/app/api/health/route.ts`; BusyBox wget in `node:20-alpine`)                                 |
| `kong`      | `kong health` (built-in admin command, exit 0 = healthy)                                                                                                                   |
| `auth`      | `wget -q --spider http://localhost:9999/health` (GoTrue built-in; wget present in `supabase/gotrue:v2.188.1` Alpine base)                                                  |
| `rest`      | **No healthcheck possible** — `postgrest/postgrest:v12.2.12` is a scratch image (no shell, no wget, no curl, no node). `--wait` treats no-healthcheck as ready-when-running. See #161 for follow-up options (sidecar / custom Dockerfile / kong-indirect). |
| `realtime`  | `curl -sf http://localhost:4000/api/health` via `CMD-SHELL` (debian-slim Elixir image has curl + sh but NOT wget — verified)                                               |
| `storage`   | `wget -q --spider http://localhost:5000/status` (storage-api built-in; Alpine base has wget)                                                                               |
| `supavisor` | `curl -sf http://localhost:4000/api/health` via `CMD-SHELL` (debian-slim Elixir image has curl + sh but NOT wget — verified, same as realtime)                             |
| `studio`    | `wget -q --spider http://localhost:3000/api/profile` (studio has Alpine wget; endpoint returns 200 for unauth on 2026.03.30 build)                                         |
| `imgproxy`  | `imgproxy health` (built-in CLI subcommand, imgproxy v3.10+)                                                                                                               |
| `meta`      | `node -e "require('http').get(...)..."` via `CMD-SHELL` (postgres-meta image has bash + node but NOT wget or curl — verified; node probe against `/health` with 200 check) |

**Excluded**: `minio-init` is a one-shot container — Docker Compose's `--wait` treats `service_completed_successfully` as ready, so no healthcheck is needed.

Standard healthcheck config for each:

```yaml
healthcheck:
  test: [<probe command from table>]
  interval: 10s
  timeout: 5s
  retries: 5
  start_period: 20s
```

`start_period` gives services a grace window to boot before failed probes count against `retries`. Tuned per service where the default isn't enough (Realtime and Postgres need ~30s).

### Layer 3 — App-level HTTP smoke test after `--wait` passes

After Layer 1 reports success, add a smoke-test step that exercises the real request path through Caddy:

```yaml
- name: Smoke test
  run: |
    ssh ... "
      set -euo pipefail
      cd /data/bloom/production
      # Domain + port come from .env.prod (rendered from .env.prod.defaults +
      # secrets by PR #144) — NOT from secrets.*_DOMAIN_MAIN, which no longer
      # exist as GitHub Secrets after the env-config refactor.
      DOMAIN_MAIN=\$(grep '^DOMAIN_MAIN=' .env.prod | cut -d= -f2-)
      DOMAIN_STUDIO=\$(grep '^DOMAIN_STUDIO=' .env.prod | cut -d= -f2-)
      DOMAIN_MINIO=\$(grep '^DOMAIN_MINIO=' .env.prod | cut -d= -f2-)
      PORT=\$(grep '^CADDY_HTTP_LISTEN_PORT=' .env.prod | cut -d= -f2-)
      failed=0
      # Per-Host probes — Caddy routes studio + minio on SUBDOMAINS, not paths
      # on the apex. Success window is 2xx/3xx only (4xx = routing bug).
      # curl -L -k follows auto-HTTPS 308 and accepts tls internal self-sign.
      for probe in \"\$DOMAIN_MAIN|/\" \"\$DOMAIN_MAIN|/api/\" \"\$DOMAIN_STUDIO|/\" \"\$DOMAIN_MINIO|/\" \"\$DOMAIN_MAIN|/api/health\"; do
        HOST=\"\${probe%|*}\"; ENDPOINT=\"\${probe#*|}\"
        STATUS=\$(curl -sLk -o /dev/null -w '%{http_code}' \
          -H \"Host: \$HOST\" --max-time 10 \
          \"http://localhost:\$PORT\$ENDPOINT\" || echo 000)
        if [ \"\$STATUS\" -lt 200 ] || [ \"\$STATUS\" -ge 400 ]; then
          echo \"::error::Smoke test failed on \$HOST\$ENDPOINT (HTTP \$STATUS)\"
          failed=1
        fi
      done
      exit \$failed
    "
```

Catches failure modes that container-level healthchecks miss:

- Caddy running but not routing requests to `bloom-web` correctly
- `bloom-web` running but `/api/*` returning 500 because a dependency is mis-wired
- Studio unreachable on its own subdomain even though the container is "healthy"
- MinIO console unreachable on its own subdomain

## Dependencies

- Requires Docker Compose **v2.17+** on the Salk deploy server and the CI runner. Ubuntu 22.04's default Docker packaging includes v2.17+. **Verified by deploy-time preflight** (new step; fails fast with an `::error::` annotation on older versions).
- Layer 2 requires implementing a new `/api/health` route in `web/` for `bloom-web`.
- Layer 3 requires `DOMAIN_MAIN`, `DOMAIN_STUDIO`, `DOMAIN_MINIO`, `CADDY_HTTP_LISTEN_PORT` in the deployed `.env.prod` / `.env.staging` files. These come from `.env.prod.defaults` / `.env.staging.defaults` committed to the repo (per PR #144's env-config refactor) — they are NOT GitHub Secrets.

## Risks

- **Minimum Compose version**: if the server has Compose <v2.17, `--wait` is a no-op flag and the deploy will exit 0 regardless. Mitigation: add a preflight step that fails fast if `docker compose version` < 2.17.
- **`--wait` timeout too short**: first-ever prod deploy has cold-start costs (Caddy Let's Encrypt issuance, Next.js first build, Postgres migrations). 600s (10 min) is the proposed ceiling vs. the current 300s; can be tuned.
- **Healthcheck false negatives**: a new `kong health` or `caddy /caddy-health` probe that's misconfigured could make a valid deploy fail. Mitigation: land Layer 2 on staging first via a staging-targeted deploy before enabling on prod.
- **Smoke test bringing up secrets in logs**: `curl` output is minimal (status code only), so no secrets leak. Requests intentionally send `Host: $DOMAIN` but the host header isn't sensitive.

## Out of scope

- Rollback stickiness (tracked by #140). Basic rollback safety already landed in merged PR #139; this change aligns rollback's `docker compose up` with the forward deploy's `--wait` semantics so a failed rollback is detectable.
- GHCR image tagging / promote-exact-SHA (tracked by #107)
- Blue-green deploy (post-first-deploy)
- Observability / metrics / alerting (tracked separately)
- External-reachability probe (loopback-only smoke test; does not exercise Salk firewall + public DNS path — gap noted, not addressed here)
- Post-deploy drift detection (a service that crashes 60s after `--wait` returns success is invisible — tracked separately)
- PostgREST healthcheck coverage — dropped in this PR because the image is scratch; follow-up at #161 to add via sidecar or custom Dockerfile

## Tracking

Implements the structural fix for #138 item 15 (no-healthcheck containers pass — `bloom-web`, `storage`, `kong`, `caddy`) and the `starting`-state bug in the hand-rolled polling filter. Supersedes the `jq -s` NDJSON fix planned for PR 2 (the entire polling loop goes away).
