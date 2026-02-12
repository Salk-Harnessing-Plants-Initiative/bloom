# Makefile for Bloom Monorepo

# Default target when you just run `make`
.PHONY: help
help:
	@echo "Usage:"
	@echo "  make dev-up           - Run full stack in development mode"
	@echo "  make dev-down         - Stop development stack"
	@echo "  make prod-up          - Run full stack in production mode"
	@echo "  make prod-down        - Stop production stack"
	@echo "  make dev-logs         - Tail development logs"
	@echo "  make reset-storage    - Reset dev DB and storage (destructive)"
	@echo "  make drop-tables      - Drop all tables in public schema"
	@echo "  make new-migration name=xxx - Create a new migration file"
	@echo "  make apply-migrations-local - Apply database migrations locally"
	@echo "  make load-test-data   - Load CSV test data into dev database"
	@echo "  make upload-images    - Upload test images to MinIO storage"
	@echo "  make create-bucket    - Create a new S3 bucket (BUCKET=name [PUBLIC=true])"
	@echo "  make list-buckets     - List all S3 buckets"
	@echo "  make configure-storage - Configure storage backend (MinIO or AWS S3)"

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
	@echo "Removing existing node_modules for a fresh install..."
	rm -rf web/node_modules packages/*/node_modules
	@echo "Rebuilding Dev Stack without cache..."
	docker compose -f docker-compose.dev.yml build --no-cache
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

# View logs for all services
.PHONY: dev-logs
logs:
	docker compose -f docker-compose.dev.yml logs -f

.PHONY: prod-logs
prod-logs:
	docker compose -f docker-compose.prod.yml logs -f


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
	@PGPASSWORD=postgres psql -h localhost -p 5432 -U supabase_admin -d postgres -c \
		"CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT NOW());" > /dev/null 2>&1
	@echo "Checking for unapplied migrations..."
	@applied=0; \
	for file in $$(ls -1 supabase/migrations/*.sql 2>/dev/null | sort); do \
		if [ -f "$$file" ]; then \
			filename=$$(basename "$$file"); \
			is_applied=$$(PGPASSWORD=postgres psql -h localhost -p 5432 -U supabase_admin -d postgres -tAc \
				"SELECT 1 FROM _migrations WHERE name = '$$filename';" 2>/dev/null); \
			if [ -z "$$is_applied" ]; then \
				echo "Applying: $$filename"; \
				PGPASSWORD=postgres psql -h localhost -p 5432 -U supabase_admin -d postgres -f "$$file" 2>&1 | grep -v "already exists" | grep -v "does not exist, skipping" || true; \
				PGPASSWORD=postgres psql -h localhost -p 5432 -U supabase_admin -d postgres -c \
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

## Load test data into development database
.PHONY: load-test-data
load-test-data:
	@echo "Loading test data into development database..."
	@if ! docker ps | grep -q db-dev; then \
		echo "Error: Development database not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@echo "Installing Python dependencies..."
	@python3 -m pip install --quiet supabase pandas 2>/dev/null || (echo "Error: Failed to install dependencies. Install with: pip install supabase pandas" && exit 1)
	@echo "Running data loader script..."
	@python3 scripts/load_test_data.py

## Upload test images to MinIO storage
.PHONY: upload-images
upload-images:
	@echo "Uploading test images to MinIO storage..."
	@if ! docker ps | grep -q supabase-minio; then \
		echo "Error: MinIO not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@echo "Installing Python dependencies..."
	@python3 -m pip install --quiet supabase 2>/dev/null || (echo "Error: Failed to install dependencies. Install with: pip install supabase" && exit 1)
	@echo "Running image uploader script..."
	@python3 scripts/upload_test_images.py

## Create a new MinIO bucket
.PHONY: create-bucket
create-bucket:
	@if [ -z "$(BUCKET)" ]; then \
		echo "Error: BUCKET name required. Usage: make create-bucket BUCKET=my-bucket-name [PUBLIC=true]"; \
		exit 1; \
	fi
	@if ! docker ps | grep -q supabase-minio; then \
		echo "Error: MinIO not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@echo "Installing Python dependencies..."
	@python3 -m pip install --quiet supabase 2>/dev/null || (echo "Error: Failed to install dependencies. Install with: pip install supabase" && exit 1)
	@echo "Creating bucket via Supabase Storage API..."
	@if [ "$(PUBLIC)" = "true" ]; then \
		python3 scripts/create_bucket.py create $(BUCKET) public; \
	else \
		python3 scripts/create_bucket.py create $(BUCKET); \
	fi

## List all MinIO buckets
.PHONY: list-buckets
list-buckets:
	@if ! docker ps | grep -q supabase-minio; then \
		echo "Error: MinIO not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@echo "Installing Python dependencies..."
	@python3 -m pip install --quiet supabase 2>/dev/null || (echo "Error: Failed to install dependencies. Install with: pip install supabase" && exit 1)
	@python3 scripts/create_bucket.py list

# Force rebuild (even without changes)
.PHONY: rebuild
rebuild: ensure-lock
	@echo " Rebuilding all Docker images..."
	docker compose -f docker-compose.dev.yml build --no-cache

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
