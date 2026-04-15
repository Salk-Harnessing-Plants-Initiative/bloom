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
	@echo "  make dev-up           - Run full stack in development mode"
	@echo "  make dev-down         - Stop development stack"
	@echo "  make prod-up          - Run full stack in production mode"
	@echo "  make prod-down        - Stop production stack"
	@echo "  make staging-up       - Run staging stack (port 8080)"
	@echo "  make staging-down     - Stop staging stack"
	@echo "  make dev-logs         - Tail development logs"
	@echo "  make staging-logs     - Tail staging logs"
	@echo "  make reset-storage    - Reset dev DB and storage (destructive)"
	@echo "  make drop-tables      - Drop all tables in public schema"
	@echo "  make new-migration name=xxx - Create a new migration file"
	@echo "  make apply-migrations-local - Apply database migrations locally"
	@echo "  make load-test-data   - Load CSV test data into dev database"
	@echo "  make upload-images    - Upload test images to MinIO storage"
	@echo "  make create-bucket    - Create a new S3 bucket (BUCKET=name [PUBLIC=true])"
	@echo "  make list-buckets     - List all S3 buckets"
	@echo "  make configure-storage - Configure storage backend (MinIO or AWS S3)"
	@echo "  make gen-types         - Generate database.types.ts from local DB and sync to all packages"

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
.PHONY: dev-logs
logs:
	docker compose -f docker-compose.dev.yml logs -f

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
		if docker exec db-dev pg_isready -U supabase_admin -h localhost >/dev/null 2>&1; then \
			echo "Database is ready"; \
			break; \
		fi; \
		echo "Waiting for database... ($$i/10)"; \
		sleep 2; \
	done
	
	@echo "Truncating all tables to remove seed data..."
	@docker exec db-dev psql -U supabase_admin -d postgres -c "\
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

## Apply database migrations locally (only unapplied ones)
.PHONY: apply-migrations-local
apply-migrations-local:
	@echo "Applying database migrations to local development database..."
	@if ! docker ps | grep -q db-dev; then \
		echo "Error: Development database not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@echo "Creating migrations tracking table if not exists..."
	@PGPASSWORD=$${POSTGRES_PASSWORD:-postgres} psql -h localhost -p 5432 -U supabase_admin -d postgres -c \
		"CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT NOW());" > /dev/null 2>&1
	@echo "Checking for unapplied migrations..."
	@applied=0; \
	for file in $$(ls -1 supabase/migrations/*.sql 2>/dev/null | sort); do \
		if [ -f "$$file" ]; then \
			filename=$$(basename "$$file"); \
			is_applied=$$(PGPASSWORD=$${POSTGRES_PASSWORD:-postgres} psql -h localhost -p 5432 -U supabase_admin -d postgres -tAc \
				"SELECT 1 FROM _migrations WHERE name = '$$filename';" 2>/dev/null); \
			if [ -z "$$is_applied" ]; then \
				echo "Applying: $$filename"; \
				PGPASSWORD=$${POSTGRES_PASSWORD:-postgres} psql -h localhost -p 5432 -U supabase_admin -d postgres -f "$$file" 2>&1 | grep -v "already exists" | grep -v "does not exist, skipping" || true; \
				PGPASSWORD=$${POSTGRES_PASSWORD:-postgres} psql -h localhost -p 5432 -U supabase_admin -d postgres -c \
					"INSERT INTO _migrations (name) VALUES ('$$filename');" > /dev/null 2>&1; \
				applied=$$((applied + 1)); \
			fi \
		fi \
	done; \
	if [ $$applied -eq 0 ]; then \
		echo "No new migrations to apply."; \
	else \
		echo "Applied $$applied migration(s) successfully."; \
	fi

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
