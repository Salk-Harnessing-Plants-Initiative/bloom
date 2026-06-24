# Makefile for Bloom Monorepo

# Postgres connection variables — override on the command line or via env
POSTGRES_USER     ?= postgres
POSTGRES_PASSWORD ?= postgres
POSTGRES_HOST     ?= localhost
POSTGRES_PORT     ?= 5432
POSTGRES_DB       ?= postgres

# Default target when you just run `make`
.PHONY: help
help:
	@echo "Usage:"
	@echo "  make init             - Generate .env.dev from .env.dev.example (FORCE=1 to overwrite)"
	@echo "  make dev-up           - Run full stack in development mode"
	@echo "  make dev-down         - Stop development stack"
	@echo "  make prod-up          - Run full stack in production mode"
	@echo "  make prod-down        - Stop production stack"
	@echo "  make staging-up       - Run staging stack (port 8080)"
	@echo "  make staging-down     - Stop staging stack"
	@echo "  make dev-logs         - Tail development logs"
	@echo "  make staging-logs     - Tail staging logs"
	@echo "  make reset-storage    - Reset dev DB and storage (destructive)"
	@echo "  make new-migration name=xxx - Create a new migration file"
	@echo "  make migrate-local    - Apply migrations to local dev DB via Supabase CLI"
	@echo "  make test-integration - Run integration tests against the local dev stack"
	@echo "  make bloommcp-smoke   - Live persistence smoke: drive a workflow through real Supabase storage"
	@echo "  make check            - Verify local stack: services, roles, schemas, migrations"
	@echo "  make verify-dev       - Clean reset -> up -> migrate -> check (destructive)"
	@echo "  make load-test-data   - Load CSV test data into dev database"
	@echo "  make upload-images    - Upload test images to MinIO storage"
	@echo "  make create-bucket    - Create a new S3 bucket (BUCKET=name [PUBLIC=true])"
	@echo "  make list-buckets     - List all S3 buckets"
	@echo "  make configure-storage-dev - Configure storage backend (MinIO or AWS S3)"
	@echo "  make gen-types         - Generate database.types.ts from local DB and sync to all packages"

# Generate a local .env.dev from .env.dev.example with fresh secrets.
# Pass FORCE=1 to overwrite an existing .env.dev (it is backed up first).
.PHONY: init
init: check-uv
	@uv run --with pyjwt,python-dotenv python scripts/init_dev.py $(if $(FORCE),--force,)

# Run development stack
.PHONY: dev-up
dev-up:
	@echo " Checking frontend dependencies..."
	@if [ ! -f "./web/package-lock.json" ]; then \
		echo " package-lock.json not found. Installing dependencies..."; \
		cd web && npm install; \
	else \
		echo " package-lock.json found."; \
	fi
	@echo " Starting Bloom Dev Stack..."
	docker compose -f docker-compose.dev.yml --env-file .env.dev up -d --build
	@echo " Bloom Dev Stack running in background"
	@echo " Access at: http://localhost:3000"
	@echo " View logs: make dev-logs"

.PHONY: rebuild-dev-fresh
rebuild-dev-fresh:
	@echo "Stopping dev stack if running..."
	@docker compose -f docker-compose.dev.yml --env-file .env.dev down -v 2>/dev/null || true
	@echo "Pruning project volumes..."
	@docker volume ls -q --filter name=bloom_v2 | xargs -r docker volume rm 2>/dev/null || true
	@echo "Removing existing node_modules for a fresh install..."
	rm -rf web/node_modules packages/*/node_modules
	@echo "Rebuilding Dev Stack without cache..."
	docker compose -f docker-compose.dev.yml --env-file .env.dev build --no-cache
	@echo "Dev Stack images rebuilt with fresh dependencies."


# Run production stack
.PHONY: prod-up
prod-up:
	@echo " Checking frontend dependencies..."
	@if [ ! -f "./web/package-lock.json" ]; then \
		echo " package-lock.json not found. Installing dependencies..."; \
		cd web && npm install; \
	else \
		echo " package-lock.json found. Installing .. "; \
	fi
	@echo " Starting Bloom Production Stack..."
	docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
	@echo " Bloom Production running in background"

.PHONY: rebuild-prod-fresh
rebuild-prod-fresh:
	@echo "Removing existing node_modules for a fresh install..."
	rm -rf web/node_modules packages/*/node_modules
	@echo "Rebuilding Prod Stack without cache..."
	docker compose -f docker-compose.prod.yml build --no-cache
	@echo "Prod Stack images rebuilt with fresh dependencies."

# Stop dev
.PHONY: dev-down
dev-down:
	docker compose -f docker-compose.dev.yml --env-file .env.dev down
	@echo "All containers stopped."

# Stop prod
.PHONY: prod-down
prod-down:
	docker compose -f docker-compose.prod.yml --env-file .env.prod down
	@echo "All containers stopped."

# Run staging stack (same compose file, different env and project name)
.PHONY: staging-up
staging-up:
	@echo " Starting Bloom Staging Stack..."
	docker compose -p bloom_v2_staging -f docker-compose.prod.yml --env-file .env.staging up -d --build
	@echo " Bloom Staging running on port 8080"

# Stop staging
.PHONY: staging-down
staging-down:
	docker compose -p bloom_v2_staging -f docker-compose.prod.yml --env-file .env.staging down
	@echo "Staging containers stopped."

# View logs for all services
.PHONY: logs dev-logs
logs:
	docker compose -f docker-compose.dev.yml logs -f

# Alias so `make dev-logs` works alongside prod-logs / staging-logs.
dev-logs: logs

.PHONY: prod-logs
prod-logs:
	docker compose -f docker-compose.prod.yml logs -f

.PHONY: staging-logs
staging-logs:
	docker compose -p bloom_v2_staging -f docker-compose.prod.yml --env-file .env.staging logs -f


## Dev-only destructive operation: reset storage and DB (clear all data)
.PHONY: reset-storage
reset-storage:
	@echo "WARNING: This will DESTROY development DB volumes and clear the MinIO host storage."
	@printf "Are you sure you want to continue? Type 'yes' to proceed: " ; read ans ; if [ "$$ans" != "yes" ] ; then echo "Aborted." ; exit 1 ; fi
	@echo "Stopping dev stack..."
	@docker compose -f docker-compose.dev.yml down || true

	# Try to detect MinIO host path from docker-compose.dev.yml (first match under supabase-minio service)
	@minio_host_path=$$(awk '/^\s*supabase-minio:/ {p=1} p && /^\s*volumes:/ {getline; if ($$0 ~ /-/) {print $$2} ; exit}' docker-compose.dev.yml | sed -E 's/^(-\s*)?([^:]+):.*$$/\2/') || true ; \
	if [ -z "$$minio_host_path" ] || [ "$$minio_host_path" = "docker-compose.dev.yml" ] ; then \
		echo "Could not detect MinIO host path from docker-compose.dev.yml. Please enter the host path to clear (e.g. /Users/minio):"; read minio_host_path ; \
	fi ; \
	if [ -n "$$minio_host_path" ]; then \
		echo "About to remove all files in $$minio_host_path (this is destructive)."; \
		printf "Final confirmation - type 'delete' to remove files in $$minio_host_path: "; read final ; \
		if [ "$$final" = "delete" ]; then \
			echo "Removing files in $$minio_host_path..." ; \
			rm -rf "$$minio_host_path"/* || true ; \
			echo "MinIO host path cleared: $$minio_host_path" ; \
		else \
			echo "Skipped clearing MinIO host path." ; \
		fi ; \
	else \
		echo "No MinIO host path specified. Skipping MinIO cleanup." ; \
	fi

	@echo "Removing docker volumes prefixed with 'bloom_v2_dev_'..."
	@docker volume ls -q | grep '^bloom_v2_dev_' | xargs -r docker volume rm || true

	@echo "Bringing dev stack back up..."
	@docker compose -f docker-compose.dev.yml --env-file .env.dev up -d --build
	
	@echo "Waiting for database to be ready..."
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
		if docker compose -f docker-compose.dev.yml exec -T db-dev pg_isready -U supabase_admin -h localhost >/dev/null 2>&1; then \
			echo "Database is ready"; \
			break; \
		fi; \
		echo "Waiting for database... ($$i/10)"; \
		sleep 2; \
	done
	
	@echo "Truncating all tables to remove seed data..."
	@docker compose -f docker-compose.dev.yml exec -T db-dev psql -U supabase_admin -d postgres -c "\
		DO \$$\$$ \
		DECLARE \
			r RECORD; \
		BEGIN \
			FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP \
				EXECUTE 'TRUNCATE TABLE public.' || quote_ident(r.tablename) || ' CASCADE'; \
			END LOOP; \
		END \$$\$$;" || echo "Note: Some tables may not exist yet"
	
	@echo "reset-storage completed. Database and storage are now empty."

## Create a new migration file
## Usage: make new-migration name=create_users_table
.PHONY: new-migration
new-migration:
	@if [ -z "$(name)" ]; then \
		echo "Error: Please provide a migration name. Usage: make new-migration name=your_migration_name"; \
		exit 1; \
	fi
	@timestamp=$$(date +%Y%m%d%H%M%S); \
	filename="supabase/migrations/$${timestamp}_$(name).sql"; \
	echo "-- Migration: $(name)" > $$filename; \
	echo "-- Created: $$(date)" >> $$filename; \
	echo "" >> $$filename; \
	echo "-- Write your SQL migration here" >> $$filename; \
	echo "" >> $$filename; \
	echo "Created: $$filename"

## Apply migrations to local dev DB via Supabase CLI.
## Tracking lives in `supabase_migrations.schema_migrations` (CLI-managed) —
## the old `public._migrations` table used by the removed apply-migrations-local
## rule is no longer read or written.
##
## Connection params come from .env.dev so the generated password and the
## configured POSTGRES_HOST_PORT are honoured. To dodge a host 5432 conflict
## (e.g. a WSL-relayed Postgres) set POSTGRES_HOST_PORT=5433 in .env.dev or the
## environment. Host port is resolved at parse time (env > .env.dev > 5432) so
## it is visible in `make -n`; the password/user/db are sourced at runtime so
## secrets never appear in `make -n` output.
POSTGRES_HOST_PORT ?= $(shell sed -n 's/^POSTGRES_HOST_PORT=//p' .env.dev 2>/dev/null | head -n1 | tr -d '\r')
ifeq ($(strip $(POSTGRES_HOST_PORT)),)
POSTGRES_HOST_PORT := 5432
endif

.PHONY: migrate-local
migrate-local:
	@command -v supabase >/dev/null 2>&1 || ( \
		echo "Error: supabase CLI not found on PATH."; \
		echo "Install: brew install supabase/tap/supabase"; \
		exit 1; \
	)
	@if ! docker ps | grep -q db-dev; then \
		echo "Error: Development database not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@if [ ! -f .env.dev ]; then \
		echo "Error: .env.dev not found. Run 'make init' first."; \
		exit 1; \
	fi
	@set -e; \
	PG_USER=$$(sed -n 's/^POSTGRES_USER=//p' .env.dev 2>/dev/null | head -n1 | tr -d '\r'); PG_USER=$${PG_USER:-supabase_admin}; \
	PG_PASSWORD=$$(sed -n 's/^POSTGRES_PASSWORD=//p' .env.dev 2>/dev/null | head -n1 | tr -d '\r'); \
	if [ -z "$$PG_PASSWORD" ]; then echo "Error: POSTGRES_PASSWORD is empty in .env.dev — run 'make init'."; exit 1; fi; \
	PG_DB=$$(sed -n 's/^POSTGRES_DB=//p' .env.dev 2>/dev/null | head -n1 | tr -d '\r'); PG_DB=$${PG_DB:-postgres}; \
	echo "Waiting for storage schema (storage-api provisions storage.buckets)..."; \
	for i in $$(seq 1 90); do \
		if docker compose -f docker-compose.dev.yml --env-file .env.dev exec -T -e PGPASSWORD="$$PG_PASSWORD" db-dev \
			psql -U "$$PG_USER" -d "$$PG_DB" -tAc \
			"SELECT 1 FROM information_schema.columns WHERE table_schema='storage' AND table_name='buckets' AND column_name='public'" \
			2>/tmp/migrate_storage_wait.err | grep -q 1; then break; fi; \
		if [ $$i -eq 90 ]; then echo "Error: storage.buckets not ready after 180s (is storage-api running? some migrations INSERT into it). Last psql stderr:"; tail -5 /tmp/migrate_storage_wait.err 2>/dev/null; exit 1; fi; \
		sleep 2; \
	done; \
	echo "Installing bloom_* schema-USAGE grant helper as supabase_admin (idempotent)..."; \
	docker compose -f docker-compose.dev.yml --env-file .env.dev exec -T -e PGPASSWORD="$$PG_PASSWORD" db-dev \
		psql -U "$$PG_USER" -d "$$PG_DB" -v ON_ERROR_STOP=1 \
		< supabase/grants/install_bloom_grant_helper.sql; \
	supabase db push \
		--db-url "postgresql://$${PG_USER}:$${PG_PASSWORD}@127.0.0.1:$(POSTGRES_HOST_PORT)/$${PG_DB}?sslmode=disable" \
		--debug \
		--yes
# NOTE: bloom_* schema-USAGE grants are applied by migration
# 20260624120000_apply_bloom_schema_usage_via_helper.sql, which CALLS the
# SECURITY DEFINER helper public.bloom_grant_schema_usage (owned by
# supabase_admin). `supabase db push` runs migrations after `SET SESSION ROLE
# postgres`, which cannot grant on the supabase_admin-owned storage/auth schemas,
# so a raw `GRANT USAGE ON SCHEMA storage` silently no-ops; the helper runs the
# grant with the owner's authority. The helper is installed above (as
# supabase_admin, $$PG_USER) BEFORE db push so the helper-calling migration sticks
# on existing local volumes; fresh inits (CI, `make verify-dev` reset, DR) install
# it via the db docker-entrypoint-initdb.d layer (see supabase/grants/). This
# supersedes the #330 repair grant (issue #333).

# Preflight helper: fail fast with an actionable message if `uv` is not on PATH.
# Used as a prerequisite by every target that invokes `uv run ...` below so the
# developer gets a clear install hint instead of a generic `uv: command not found`
# coming out of the middle of a script.
.PHONY: check-uv
check-uv:
	@command -v uv >/dev/null 2>&1 || ( \
		echo "Error: uv is required but not installed or not on PATH."; \
		echo "Install: https://docs.astral.sh/uv/getting-started/installation/"; \
		exit 1; \
	)

## Run the integration test suite locally against the running dev stack.
## Requires the stack to be up (`make dev-up`) and migrated (`make migrate-local`);
## conftest reads .env.dev for the DB credentials.
.PHONY: test-integration
test-integration: check-uv
	@uv run --extra test pytest tests/integration/ -v

## Verify the local dev stack is correct: services healthy, required roles +
## auth/storage schemas present, every migration applied (issue #104).
.PHONY: check
check: check-uv
	@uv run --extra test python scripts/check_health.py

## Live persistence smoke (issue #326): drive a workflow end-to-end through the
## REAL SupabaseReader/SupabaseResultStore against the running dev stack, then
## assert the committed run is a v3 manifest with real provenance and that each
## recorded output_sha256 == the bytes actually stored. Requires the stack to be
## up (`make dev-up`) and migrated (`make migrate-local` — which creates the
## bloommcp-data bucket and applies the storage grants the write path needs).
## Bridges the host<->container env gap: .env.dev points SUPABASE_URL at the
## in-container gateway http://kong:8000 and BLOOM_*_DIR at /app paths, so here we
## derive the host gateway from KONG_HTTP_PORT and let the driver use host temp
## dirs. The same target backs the CI gate, so local and CI never drift. The
## BLOOM_AGENT_KEY line is `@`-prefixed and never echoed.
.PHONY: bloommcp-smoke
bloommcp-smoke: check-uv
	@if [ ! -f .env.dev ]; then \
		echo "Error: .env.dev not found. Run 'make init' first."; \
		exit 1; \
	fi
	@if ! docker ps | grep -q db-dev; then \
		echo "Error: dev stack not running. Start with 'make dev-up' (then 'make migrate-local')."; \
		exit 1; \
	fi
	@KONG_PORT=$$(sed -n 's/^KONG_HTTP_PORT=//p' .env.dev 2>/dev/null | head -n1 | tr -d '\r'); KONG_PORT=$${KONG_PORT:-8000}; \
	BLOOM_AGENT_KEY=$$(sed -n 's/^BLOOM_AGENT_KEY=//p' .env.dev 2>/dev/null | head -n1 | tr -d '\r'); \
	if [ -z "$$BLOOM_AGENT_KEY" ]; then echo "Error: BLOOM_AGENT_KEY is empty in .env.dev — run 'make init'."; exit 1; fi; \
	cd bloommcp && SUPABASE_URL="http://localhost:$${KONG_PORT}" BLOOM_AGENT_KEY="$$BLOOM_AGENT_KEY" \
		uv run python scripts/live_persistence_smoke.py

## One-shot: clean reset -> up -> migrate -> health check. Destructive (wipes the
## local DB). Use to reproduce a fresh-clone init and prove it end to end.
.PHONY: verify-dev
verify-dev: check-uv
	@echo "Clean reset of the dev stack (this wipes the local DB)..."
	docker compose -f docker-compose.dev.yml --env-file .env.dev down -v
	@echo "Wiping the local DB bind-mount ($(CURDIR)/volumes/db/data)..."
	@rm -rf "$(CURDIR)/volumes/db/data"
	$(MAKE) dev-up
	@echo "Waiting for db-dev to accept connections..."
	@for i in $$(seq 1 60); do \
		if docker compose -f docker-compose.dev.yml exec -T db-dev pg_isready -U supabase_admin -h localhost >/dev/null 2>&1; then \
			echo "db-dev ready"; break; \
		fi; \
		if [ $$i -eq 60 ]; then echo "Error: db-dev did not accept connections after 120s. Check 'make dev-logs'."; exit 1; fi; \
		sleep 2; \
	done
	$(MAKE) migrate-local
	$(MAKE) check

## Load test data into development database
.PHONY: load-test-data
load-test-data: check-uv
	@echo "Loading test data into development database..."
	@if ! docker ps | grep -q db-dev; then \
		echo "Error: Development database not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@echo "Running data loader script..."
	@uv run --with supabase,pandas -- python3 scripts/load_test_data.py

## Upload test images to MinIO storage
.PHONY: upload-images
upload-images: check-uv
	@echo "Uploading test images to MinIO storage..."
	@if ! docker ps | grep -q supabase-minio; then \
		echo "Error: MinIO not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@echo "Running image uploader script..."
	@uv run --with supabase -- python3 scripts/upload_test_images.py

## Create a new MinIO bucket
.PHONY: create-bucket
create-bucket: check-uv
	@if [ -z "$(BUCKET)" ]; then \
		echo "Error: BUCKET name required. Usage: make create-bucket BUCKET=my-bucket-name [PUBLIC=true]"; \
		exit 1; \
	fi
	@if ! docker ps | grep -q supabase-minio; then \
		echo "Error: MinIO not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@echo "Creating bucket via Supabase Storage API..."
	@if [ "$(PUBLIC)" = "true" ]; then \
		uv run --with supabase -- python3 scripts/create_bucket.py create $(BUCKET) public; \
	else \
		uv run --with supabase -- python3 scripts/create_bucket.py create $(BUCKET); \
	fi

## List all MinIO buckets
.PHONY: list-buckets
list-buckets: check-uv
	@if ! docker ps | grep -q supabase-minio; then \
		echo "Error: MinIO not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@uv run --with supabase -- python3 scripts/create_bucket.py list

# Force rebuild (even without changes)
.PHONY: rebuild
rebuild: ensure-lock
	@echo " Rebuilding all Docker images..."
	docker compose -f docker-compose.dev.yml --env-file .env.dev build --no-cache

## Configure storage backend (MinIO or AWS S3)
.PHONY: configure-storage-dev
configure-storage-dev:
	@echo "Running storage backend configuration..."
	@bash scripts/configure_storage.sh

## Configure storage backend for production
.PHONY: configure-storage-prod
configure-storage-prod:
	@echo "Running storage backend configuration for production..."
	@bash scripts/configure_storage.sh prod

## Generate database.types.ts from local DB and copy to all packages
## Requires the local dev database to be running (make dev-up)
.PHONY: gen-types
gen-types:
	@echo "Generating TypeScript types from local database..."
	@if ! docker ps | grep -q db-dev; then \
		echo "Error: Development database not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@npx supabase gen types typescript \
		--db-url "$${SUPABASE_DB_URL:-postgresql://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@$(POSTGRES_HOST):$(POSTGRES_PORT)/$(POSTGRES_DB)}" \
		> /tmp/database.types.ts
	@echo "Copying database.types.ts to all packages..."
	@cp /tmp/database.types.ts packages/bloom-fs/src/types/database.types.ts
	@cp /tmp/database.types.ts packages/bloom-js/src/types/database.types.ts
	@cp /tmp/database.types.ts packages/bloom-nextjs-auth/src/lib/database.types.ts
	@cp /tmp/database.types.ts web/lib/database.types.ts
	@rm /tmp/database.types.ts
	@echo "Database types updated in:"
	@echo "  - packages/bloom-fs/src/types/database.types.ts"
	@echo "  - packages/bloom-js/src/types/database.types.ts"
	@echo "  - packages/bloom-nextjs-auth/src/lib/database.types.ts"
	@echo "  - web/lib/database.types.ts"
	@echo "Done. All packages now have identical types."
