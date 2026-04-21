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

Docker Compose v2.17+ has a built-in `--wait` flag that blocks `up` until:

- Every service with a healthcheck reaches `Health: healthy`
- Every service without a healthcheck reaches `State: running`
- Or `--wait-timeout` expires, in which case `up` exits non-zero with diagnostics.

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
      cd /opt/bloom/production
      docker compose -f docker-compose.prod.yml --env-file .env.prod \
        up -d --build --remove-orphans --wait --wait-timeout 600
    "
- name: Deploy diagnostics on failure
  if: failure()
  run: |
    ssh ... "
      cd /opt/bloom/production
      docker compose -f docker-compose.prod.yml --env-file .env.prod ps
      docker compose -f docker-compose.prod.yml --env-file .env.prod logs --tail=200
    "
```

Removes ~30 lines of `jq`/`seq`/`sleep` per job (prod + staging = 60 lines total), removes the NDJSON parsing concern entirely, and correctly handles the `starting` state.

### Layer 2 — Add healthchecks to every service that lacks them

Current audit of [docker-compose.prod.yml](docker-compose.prod.yml): **16 services, only 4 custom files have healthchecks** (`langchain-agent`, `bloommcp`, `supabase-minio`, `db-prod`). Without healthchecks on the other 11, Layer 1's `--wait` treats them as healthy the instant they're `running`, even if the app inside is crashed.

Add `healthcheck:` blocks to every remaining service:

| Service       | Probe                                                                                                                                                               |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `caddy`     | `wget -q --spider http://localhost:80/caddy-health` (new Caddyfile route returning 200)                                                                           |
| `bloom-web` | `wget -q --spider http://localhost:3000/api/health` (new Next.js route in `web/app/api/health/route.ts`; BusyBox wget is already present in `node:20-alpine`) |
| `kong`      | `kong health` (built-in admin command, exit 0 = healthy)                                                                                                          |
| `auth`      | `wget -q --spider http://localhost:9999/health` (GoTrue built-in)                                                                                                 |
| `rest`      | `wget -q --spider http://localhost:3000/` (PostgREST serves OpenAPI on `/`)                                                                                     |
| `realtime`  | `wget -q --spider http://localhost:4000/api/health` (Realtime built-in)                                                                                           |
| `storage`   | `wget -q --spider http://localhost:5000/status` (storage-api built-in)                                                                                            |
| `supavisor` | `wget -q --spider http://localhost:4000/api/health` (Supavisor built-in)                                                                                          |
| `studio`    | `wget -q --spider http://localhost:3000/api/profile`                                                                                                              |
| `imgproxy`  | `imgproxy health` (built-in)                                                                                                                                      |
| `meta`      | `wget -q --spider http://localhost:8080/health` (postgres-meta built-in)                                                                                          |

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
      set -e
      cd /opt/bloom/production
      for endpoint in '/' '/api' '/studio'; do
        STATUS=\$(curl -s -o /dev/null -w '%{http_code}' \
          -H 'Host: ${{ secrets.PROD_DOMAIN_MAIN }}' \
          --max-time 10 \
          http://localhost\$endpoint)
        if [ \"\$STATUS\" -ge 500 ] || [ \"\$STATUS\" -eq 000 ]; then
          echo \"::error::Smoke test failed on \$endpoint (HTTP \$STATUS)\"
          exit 1
        fi
        echo \"  \$endpoint -> \$STATUS\"
      done
    "
```

Catches failure modes that container-level healthchecks miss:

- Caddy running but not routing requests to `bloom-web` correctly
- `bloom-web` running but `/api/*` returning 500 because a dependency is mis-wired
- Studio unreachable even though the container is "healthy"

## Dependencies

- Requires Docker Compose **v2.17+** on the Salk deploy server and the CI runner. Ubuntu 22.04's default Docker packaging includes v2.17+. **Must verify before merge.**
- Layer 2 requires implementing a new `/api/health` route in `web/` for `bloom-web`.
- Layer 3 requires `$PROD_DOMAIN_MAIN` (and `$STAGING_DOMAIN_MAIN`) secrets to already be configured (they are — see #135).

## Risks

- **Minimum Compose version**: if the server has Compose <v2.17, `--wait` is a no-op flag and the deploy will exit 0 regardless. Mitigation: add a preflight step that fails fast if `docker compose version` < 2.17.
- **`--wait` timeout too short**: first-ever prod deploy has cold-start costs (Caddy Let's Encrypt issuance, Next.js first build, Postgres migrations). 600s (10 min) is the proposed ceiling vs. the current 300s; can be tuned.
- **Healthcheck false negatives**: a new `kong health` or `caddy /caddy-health` probe that's misconfigured could make a valid deploy fail. Mitigation: land Layer 2 on staging first via a staging-targeted deploy before enabling on prod.
- **Smoke test bringing up secrets in logs**: `curl` output is minimal (status code only), so no secrets leak. Requests intentionally send `Host: $DOMAIN` but the host header isn't sensitive.

## Out of scope

- Rollback behavior (tracked by #138 and #140)
- GHCR image tagging / promote-exact-SHA (tracked by #107)
- Blue-green deploy (post-first-deploy)
- Observability / metrics / alerting (tracked separately)

## Tracking

Implements the structural fix for #138 item 15 (no-healthcheck containers pass) and the hidden `starting`-state bug surfaced during #139 self-review. Supersedes the `jq -s` NDJSON fix planned for PR 2 (the entire polling loop goes away).
