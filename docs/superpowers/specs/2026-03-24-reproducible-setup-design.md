# Bloom Reproducible Setup — Design Spec

## Goal

Make Bloom deployable from a fresh `git clone` with `make init && make dev-up`. Target audience: external users and open-source contributors with no prior Bloom knowledge.

## Architecture Decision

**Hand-maintained compose file + automated init script for secrets and volumes.**

- Keep `docker-compose.dev.yml` as a single, hand-maintained file with all services (Supabase + Bloom)
- Add `make init` to automate secret generation, Supabase volumes download, and prerequisite checks
- Add `make upgrade-supabase` to compare image versions and env vars between releases
- Staging and production run simultaneously on the server via nginx subdomain routing

### Why hand-maintained?

We evaluated and rejected several "cleaner" approaches:

| Approach | Why rejected |
|---|---|
| Docker Compose `include` | Can't override included service properties |
| Docker Compose `-f` merge | `--project-directory` can't resolve paths for both files |
| Git submodule for Supabase | Repo is 2.3 GB; we need ~1 MB |
| Python-generated compose | Over-engineering: YAML fidelity issues, ambiguous source of truth |
| Template-based compose | Fragile string interpolation |

## Branch Model

```
feature branches → staging → main
                   │          │
                   │          └─ CI deploys to production
                   └─ CI deploys to staging server
```

- `staging` branch: CI auto-deploys to `staging.pbiob-gh.salk.edu` on push
- `main` branch: CI deploys to `bloom.pbiob-gh.salk.edu` after manual approval
- `dev` is retired as a branch name (too easily confused with local development)
- Feature branches PR into `staging`

## Environment Model

```
Local laptop (developer):
  ~/repos/salk-bloom/                     → local dev (make dev-up)
  Branch: feature/* or staging
  Ports: standard (3000, 8000, 5432)
  No nginx, no SSL, no subdomains

bloom-dev server (pbiob-gh.salk.edu):
  /opt/bloom/staging/                     → staging environment
  Branch: staging (auto-deployed by CI)
  Ports: offset (3100, 8100, 9101, etc.)

  /opt/bloom/production/                  → production environment
  Branch: main (deployed after approval)
  Ports: standard (3000, 8000, 9001, etc.)

  nginx on ports 80/443                   → routes subdomains to stacks
```

### Subdomain → Port Routing (nginx)

| Subdomain | Routes to | Docker Service | Stack |
|---|---|---|---|
| `bloom.pbiob-gh.salk.edu` | `localhost:3000` | bloom-web | Production |
| `api.pbiob-gh.salk.edu` | `localhost:8000` | Kong API gateway | Production |
| `mcp.pbiob-gh.salk.edu` | `localhost:8811` | bloommcp | Production |
| `storage.pbiob-gh.salk.edu` | `localhost:9001` | MinIO console | Production |
| `staging.pbiob-gh.salk.edu` | `localhost:3100` | bloom-web | Staging |
| `staging-api.pbiob-gh.salk.edu` | `localhost:8100` | Kong API gateway | Staging |
| `staging-mcp.pbiob-gh.salk.edu` | `localhost:8812` | bloommcp | Staging |
| `staging-storage.pbiob-gh.salk.edu` | `localhost:9101` | MinIO console | Staging |

All external traffic comes through nginx on 443 (HTTPS with Let's Encrypt). Docker services are not directly exposed to the internet.

**Note:** 8 DNS A records needed (add `mcp.*` and `staging-mcp.*` to issue #17's DNS request).

**nginx rewrite is a follow-up** aligned with CI/CD epic #16. The current `nginx/nginx.conf.template` runs inside Docker and handles 3 server blocks (main domain, studio, minio) with path-based API routing, no SSL. The target architecture needs 8 subdomain-based server blocks with SSL. This work belongs in issues #17/#21, not this PR.

**Services NOT exposed externally** (internal only, accessed through Kong or within Docker network):
- PostgreSQL (5432/5433) — database, never public
- PostgREST (3000 internal) — accessed through Kong's `/rest/v1/` route
- GoTrue auth (9999 internal) — accessed through Kong's `/auth/v1/` route
- Realtime (4000 internal) — accessed through Kong's `/realtime/v1/` route
- Supabase Studio (55323/55324) — admin UI, access via SSH tunnel or VPN only
- langchain-agent (5002/5003) — accessed by bloom-web internally
- imgproxy, meta, analytics, supavisor — all internal

### Isolation guarantees

| Property | Dev (laptop) | Staging (server) | Production (server) |
|---|---|---|---|
| Directory | `~/repos/salk-bloom/` | `/opt/bloom/staging/` | `/opt/bloom/production/` |
| Branch | feature/* | `staging` | `main` |
| Compose file | `docker-compose.dev.yml` | `docker-compose.dev.yml` | `docker-compose.prod.yml` |
| Env file | `.env.dev` | `.env.staging` | `.env.prod` |
| Postgres data | `./volumes/db/data` | `./volumes/db/data` | `./volumes/db/data` |
| MinIO data | `./minio_data` | `./minio_data` | configured path |
| JWT_SECRET | unique per install | unique | unique |
| POSTGRES_PASSWORD | unique per install | unique | unique |
| Docker project | `bloom_v2_dev` | `bloom-staging` | `bloom-prod` |
| Host ports | standard | offset (+100) | standard |

Each directory is self-contained. Relative paths (`./volumes/db/data`) resolve to different absolute paths per directory. Unique secrets mean staging tokens cannot authenticate against production.

### Port assignments

| Service | Internal Port | Dev (laptop) | Staging (server) | Production (server) |
|---|---|---|---|---|
| bloom-web | 3000 | 3000 | 3100 | 3000 |
| Kong HTTP | 8000 | 8000 | 8100 | 8000 |
| Kong HTTPS | 8443 | 8443 | 8543 | 8443 |
| MinIO API | 9000 | 9100 | 9200 | 9100 |
| MinIO Console | 9001 | 9101 | 9201 | 9101 |
| PostgreSQL | 5432 | 5432 | 5433 | not exposed |
| Studio | 3000 (int) | 55323 | 55324 | 55323 |
| langchain-agent | 5002 | 5002 | 5003 | not exposed |
| bloommcp | 8811 | 8811 | 8812 | not exposed |
| analytics | 4000 | 4000 | 4001 | not exposed |
| storage-api | 5000 | 5551 | 5552 | not exposed |
| swagger-ui | 8080 | 8085 | 8086 | not exposed |

Dev uses standard ports (no conflicts — only one stack on laptop). Staging offsets by ~100 to coexist with production on the same server.

### Deployment workflow

```
Developer pushes feature branch
  → PR to staging
  → Code review
  → Merge to staging
  → CI: run tests, build, deploy to /opt/bloom/staging/
  → Team tests on staging.pbiob-gh.salk.edu

Ready for production:
  → PR from staging to main
  → Manual approval
  → Merge to main
  → CI: rebuild production from main at /opt/bloom/production/
  → Live on bloom.pbiob-gh.salk.edu
```

## Repo Structure

```
salk-bloom/
├── docker-compose.dev.yml             # Hand-maintained, all services (dev + staging)
├── docker-compose.prod.yml            # Hand-maintained, production config
├── .env.example                       # Documented template (all vars)
├── .env.dev                           # Generated by make init (gitignored)
├── .env.staging                       # Generated by make init-staging (gitignored)
├── SUPABASE_VERSION                   # Pinned tag (e.g., "v1.26.03")
│
├── volumes/                           # Downloaded by make init (gitignored)
│   ├── .supabase-version              # Marker file for version checking
│   ├── api/kong.yml
│   ├── api/kong-entrypoint.sh
│   ├── db/*.sql
│   └── pooler/pooler.exs
│
├── scripts/
│   ├── init_dev.py                    # Setup: prereqs, download volumes, generate secrets
│   └── check_health.py               # Post-startup validation
│
├── nginx/                             # Nginx configs for server
│   └── nginx.conf.template
├── web/                               # Next.js (unchanged)
├── langchain/                         # LangChain agent (unchanged)
├── bloommcp/                          # MCP server (unchanged)
├── minio/                             # MinIO init scripts (unchanged)
├── Makefile
├── DEV_SETUP.md
├── PROD_SETUP.md
└── README.md
```

## What Changes in `docker-compose.dev.yml`

### 1. Remove all `container_name` directives

Remove from all 11 services. Docker Compose auto-generates names with the project prefix. Required for staging + production to coexist on the same server (hardcoded container names ignore `-p` and would collide).

**Migration note:** `docker exec db-dev ...` → `docker compose exec db-dev ...`. Update Makefile targets: `reset-storage`, `apply-migrations-local`, `load-test-data`, `upload-images`, `gen-types`, `list-buckets`.

### 2. Image version upgrades

Bump Supabase images to match pinned release v1.26.03:

| Service | Current | Target | Notes |
|---|---|---|---|
| postgres | `15.8.1.060` | `15.8.1.085` | |
| gotrue (auth) | `v2.177.0` | `v2.186.0` | |
| realtime | `v2.34.47` | `v2.76.5` | |
| storage-api | `v1.25.7` | `v1.37.8` | |
| studio | `2025.06.30-sha-6f5982d` | `2026.02.16-sha-26c615c` | |
| postgres-meta | `v0.91.6` | `v0.95.2` | |
| logflare | `1.14.2` | `1.31.2` | |
| imgproxy | `v3.8.0` | `v3.30.1` | |
| supavisor | `2.5.7` | `2.7.4` | |
| postgrest | `v12.2.12` | `v14.5` | Major version jump |
| kong | `kong/kong:3.9.1` | `kong/kong:3.9.1` | Keep current (documented divergence from v1.26.03's kong:2.8.1) |

### 3. Add missing health checks

| Service | Health check | Notes |
|---|---|---|
| bloom-web | `node -e "fetch('http://localhost:3000').then(r=>{if(!r.ok)process.exit(1)})"` | Node 18+ built-in fetch |
| supabase-minio | `curl -sf http://localhost:9000/minio/health/live` | |
| kong | `kong health` | Built-in CLI |
| meta | `wget --no-verbose --tries=1 --spider http://localhost:8080/health` | node-alpine has wget, not curl |

### 4. Make all host-exposed ports configurable

For staging port offsets on the server:

```yaml
# Bloom services
bloom-web:
  ports: ["${BLOOM_WEB_PORT:-3000}:3000"]
langchain-agent:
  ports: ["${BLOOM_LANGCHAIN_PORT:-5002}:5002"]
bloommcp:
  ports: ["${BLOOM_MCP_PORT:-8811}:8811"]
supabase-minio:
  ports:
    - "${BLOOM_MINIO_PORT:-9100}:9000"
    - "${BLOOM_MINIO_CONSOLE_PORT:-9101}:9001"

# Supabase services
studio:
  ports: ["${STUDIO_PORT:-55323}:3000"]
analytics:
  ports: ["${ANALYTICS_PORT:-4000}:4000"]
db-dev:
  ports: ["${DB_PORT:-5432}:5432"]
storage:
  ports: ["${STORAGE_PORT:-5551}:5000"]
swagger-ui:
  ports: ["${SWAGGER_PORT:-8085}:8080"]
```

Kong and supavisor already use env vars. Local dev uses defaults (standard ports). Staging `.env.staging` sets offset ports.

### 5. Make data directories configurable

```yaml
db-dev:
  volumes:
    - ${PGDATA_PATH:-./volumes/db/data}:/var/lib/postgresql/data:Z
```

`MINIO_DATA_PATH` is already an env var.

### 6. Add missing env vars, fix CORS and OPENAI_API_KEY

- Add `LANGCHAIN_POSTGRES_URL`, `MINIO_STORAGE_S3_ENDPOINT`, `MINIO_S3_STORAGE_REGION`, `POOLER_DB_POOL_SIZE` to compose
- Change `CORS_ORIGINS` from hardcoded to `${CORS_ORIGINS:-http://localhost:3000}`
- Remove `OPENAI_API_KEY` from compose `environment:` (pass via .env only when set)

## Init Script: `scripts/init_dev.py`

Run via: `uv run --with pyjwt,python-dotenv scripts/init_dev.py [--env dev|staging] [--force] [--download-only]`

### Flow

1. **Check prerequisites** — Docker, Compose >= 2.20, uv
2. **Check idempotency** — warn if env file exists, require `--force`
3. **Download Supabase volumes** — from `SUPABASE_VERSION` tag, with version marker
4. **Generate env file** — secrets + upstream defaults + Bloom overrides
5. **Conditional vLLM check** — if `LOCAL_LLM_URL` set
6. **Print summary**

### Secret generation table

| Variable | Method | Notes |
|---|---|---|
| POSTGRES_PASSWORD | `secrets.token_urlsafe(24)` | ~32 chars |
| JWT_SECRET | `secrets.token_urlsafe(48)` | ~64 chars |
| ANON_KEY | JWT: role=anon, iss=supabase, iat=now, exp=now+10yr, HS256 | |
| SERVICE_ROLE_KEY | JWT: role=service_role, same params | |
| VAULT_ENC_KEY | `secrets.token_hex(16)` | Exactly 32 hex chars |
| SUPAVISOR_ENC_KEY | `secrets.token_hex(32)` | 64 hex chars |
| SECRET_KEY_BASE | `secrets.token_urlsafe(48)` | ~64 chars |
| DB_ENC_KEY | `secrets.token_urlsafe(24)` | |
| DASHBOARD_PASSWORD | `secrets.token_urlsafe(16)` | |
| PG_META_CRYPTO_KEY | `secrets.token_urlsafe(24)` | |
| LOGFLARE_PUBLIC_ACCESS_TOKEN | `secrets.token_urlsafe(24)` | |
| LOGFLARE_PRIVATE_ACCESS_TOKEN | `secrets.token_urlsafe(24)` | |
| MINIO_ROOT_PASSWORD | `secrets.token_urlsafe(16)` | |
| BLOOMMCP_API_KEY | `secrets.token_urlsafe(24)` | |

### Environment-specific overrides

For `--env dev` (local laptop):
- Standard ports (defaults in compose)
- `SITE_URL=http://localhost:3000`
- `CORS_ORIGINS=http://localhost:3000`

For `--env staging` (server):
- Offset ports: `BLOOM_WEB_PORT=3100`, `KONG_HTTP_PORT=8100`, etc. (see port table above)
- `SITE_URL=https://staging.pbiob-gh.salk.edu`
- `API_EXTERNAL_URL=https://staging-api.pbiob-gh.salk.edu`
- `CORS_ORIGINS=https://staging.pbiob-gh.salk.edu`

Production uses `PROD_SETUP.md` manual guide with:
- Standard ports
- `SITE_URL=https://bloom.pbiob-gh.salk.edu`
- `CORS_ORIGINS=https://bloom.pbiob-gh.salk.edu`

## Makefile Targets

```makefile
SHELL := bash
COMPOSE_FILE ?= docker-compose.dev.yml
ENV_FILE ?= .env.dev
COMPOSE := docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE)

.PHONY: init init-staging dev-up dev-down dev-logs check download-volumes upgrade-supabase

init:                              ## First-time dev setup (local)
	uv run --with pyjwt,python-dotenv scripts/init_dev.py --env dev

init-staging:                      ## First-time staging setup (server)
	uv run --with pyjwt,python-dotenv scripts/init_dev.py --env staging

dev-up:                            ## Start dev stack
	$(COMPOSE) up -d --build

dev-down:                          ## Stop dev stack
	$(COMPOSE) down

dev-logs:                          ## Tail dev logs
	$(COMPOSE) logs -f

staging-up:                        ## Start staging stack (on server)
	docker compose -f docker-compose.dev.yml --env-file .env.staging -p bloom-staging up -d --build

staging-down:                      ## Stop staging stack
	docker compose -f docker-compose.dev.yml --env-file .env.staging -p bloom-staging down

check:                             ## Verify all services healthy
	uv run scripts/check_health.py

download-volumes:                  ## Re-download Supabase volumes
	uv run --with pyjwt,python-dotenv scripts/init_dev.py --download-only

upgrade-supabase:                  ## Compare current vs new Supabase release
	@echo "Current: $$(cat SUPABASE_VERSION)"; \
	read -p "New version: " new_ver; \
	old_ver=$$(cat SUPABASE_VERSION); \
	old=$$(curl -sf "https://raw.githubusercontent.com/supabase/supabase/$$old_ver/docker/docker-compose.yml"); \
	new=$$(curl -sf "https://raw.githubusercontent.com/supabase/supabase/$$new_ver/docker/docker-compose.yml"); \
	if [ -z "$$old" ] || [ -z "$$new" ]; then echo "ERROR: Check version tags."; exit 1; fi; \
	echo "\n=== Image changes ($$old_ver → $$new_ver) ==="; \
	diff <(echo "$$old" | grep 'image:' | sort) <(echo "$$new" | grep 'image:' | sort) || true; \
	echo "\n=== New env vars ==="; \
	comm -13 <(curl -sf "https://raw.githubusercontent.com/supabase/supabase/$$old_ver/docker/.env.example" | grep -v '^#' | grep '=' | cut -d= -f1 | sort) \
	         <(curl -sf "https://raw.githubusercontent.com/supabase/supabase/$$new_ver/docker/.env.example" | grep -v '^#' | grep '=' | cut -d= -f1 | sort); \
	echo "\nNext: update image tags, add new env vars, update SUPABASE_VERSION, make download-volumes"
```

## .gitignore Changes

```gitignore
volumes/
.env.dev
.env.staging
.env.prod
minio_data/
```

## Upgrade Workflow

1. `make upgrade-supabase` — compare versions
2. Update image tags in `docker-compose.dev.yml`
3. Add any new env vars to `.env.example` and `scripts/init_dev.py`
4. Update `SUPABASE_VERSION`, run `make download-volumes`
5. Push to `staging` branch → CI deploys to staging server
6. Test on `staging.pbiob-gh.salk.edu`
7. Merge `staging` → `main` → CI rebuilds production
8. **Rollback:** revert merge on `main`, CI redeploys previous version

## Migration Guide

### For local developers
```
1. docker compose -f docker-compose.dev.yml down
2. container_name removed: use "docker compose exec" not "docker exec"
3. Database data at ./volumes/db/data is preserved
4. make init   (generates secrets, downloads volumes)
5. make dev-up && make check
```

### For bloom-dev server
```
1. docker compose down (both staging and production if running)
2. Set up staging:  cd /opt/bloom/staging && git clone <repo> . && git checkout staging && make init-staging
3. Set up production: cd /opt/bloom/production && git clone <repo> . && make init  (then manually create .env.prod per PROD_SETUP.md)
4. Configure nginx with subdomain routing (see epic #16, issue #17)
5. staging-up && check, then dev-up with .env.prod
```

## Production Notes

`docker-compose.prod.yml` — known issues to fix (follow-up):
- `CORS_ORIGINS` insecure default
- `DB_ENC_KEY` insecure default
- Kong version alignment

## Deliverables for this PR

1. `SUPABASE_VERSION` file (content: `v1.26.03`)
2. `.env.example` (documented template)
3. `scripts/init_dev.py` (init script)
4. `scripts/check_health.py` (health checker)
5. Updated `docker-compose.dev.yml` (container_name removal, image upgrades, health checks, port env vars, env var fixes)
6. Updated `Makefile` (new targets, fix container name references)
7. Updated `.gitignore`
8. Updated `README.md`, `DEV_SETUP.md`, `PROD_SETUP.md`
9. `langchain/agent.py` Qwen model name fix

## Decisions Log

| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| Compose strategy | Hand-maintained | `include`, `-f` merge, generated, template | Simplest, most debuggable |
| Supabase volumes | Download at init | Git submodule, committed | Third-party out of git |
| Secret management | Auto-generate | Hardcoded dev secrets | Unique per install |
| Init script | Python via `uv` | Bash | Cross-platform, JWT needs pyjwt |
| Environment model | Separate directories + port offsets | Single directory with sequential testing | Aligns with CI/CD epic #16; staging + prod run simultaneously |
| Branch model | staging → main | dev → main | Avoids confusion between "dev branch" and "dev environment" |
| container_name | Remove all | Keep | Required for multi-stack on same host |
| Kong version | Stay on 3.9.1 | Revert to 2.8.1 | Already working; documented divergence |
| Staging ports | Offset by +100 | Same ports (sequential) | Both stacks must run simultaneously for CI/CD |
| External access | nginx subdomain routing | Direct port access | HTTPS, clean URLs, matches epic #16 plan |
| Internal services | Not exposed externally | Expose all | Only bloom-web, Kong, MinIO console need public access |